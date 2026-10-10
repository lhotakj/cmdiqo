#if !defined(_WIN32)

#include "cmdiqo/agent/process.hpp"
#include "cmdiqo/agent/logger.hpp"

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <string>
#include <vector>

namespace cmdiqo::agent {

std::vector<std::string> build_argv(std::string_view kind, std::string_view cmd) {
    if (kind == "ps1") {
        return {"pwsh", "-NoProfile", "-NonInteractive", "-Command", std::string(cmd)};
    }
    return {"/bin/bash", "-c", std::string(cmd)};
}

ProcessResult execute_process(
    std::string_view kind,
    std::string_view cmd,
    const std::optional<std::string>& cwd,
    int timeout_seconds
) {
    int effective_timeout = (timeout_seconds > 0) ? timeout_seconds : 120;
    auto argv_strings = build_argv(kind, cmd);

    int pipe_fd[2];
    if (pipe(pipe_fd) != 0) {
        return {false, 1, "", "OS error: failed to create pipe"};
    }

    pid_t pid = fork();
    if (pid < 0) {
        close(pipe_fd[0]);
        close(pipe_fd[1]);
        return {false, 1, "", "OS error: fork failed"};
    }

    if (pid == 0) {
        // Child process
        close(pipe_fd[0]);
        dup2(pipe_fd[1], STDOUT_FILENO);
        dup2(pipe_fd[1], STDERR_FILENO);
        close(pipe_fd[1]);

        // Create new process group so timeouts kill entire child process tree
        setpgid(0, 0);

        if (cwd.has_value() && !cwd->empty()) {
            if (chdir(cwd->c_str()) != 0) {
                _exit(126);
            }
        }

        std::vector<char*> c_argv;
        c_argv.reserve(argv_strings.size() + 1);
        for (auto& s : argv_strings) {
            c_argv.push_back(s.data());
        }
        c_argv.push_back(nullptr);

        execvp(c_argv[0], c_argv.data());
        _exit(127);
    }

    // Parent process
    close(pipe_fd[1]);

    int flags = fcntl(pipe_fd[0], F_GETFL, 0);
    if (flags != -1) {
        fcntl(pipe_fd[0], F_SETFL, flags | O_NONBLOCK);
    }

    std::string output;
    char buffer[4096];
    auto start_time = std::chrono::steady_clock::now();
    int status = 0;
    bool child_exited = false;
    bool timed_out = false;

    while (true) {
        if (!child_exited) {
            pid_t res = waitpid(pid, &status, WNOHANG);
            if (res == pid || res < 0) {
                child_exited = true;
            }
        }

        struct pollfd pfd{};
        pfd.fd = pipe_fd[0];
        pfd.events = POLLIN | POLLHUP;
        int poll_res = poll(&pfd, 1, 50);

        if (poll_res > 0 && (pfd.revents & (POLLIN | POLLHUP | POLLERR))) {
            ssize_t bytes_read = read(pipe_fd[0], buffer, sizeof(buffer));
            if (bytes_read > 0) {
                output.append(buffer, static_cast<size_t>(bytes_read));
            } else if (bytes_read == 0 && child_exited) {
                break;
            }
        } else if (child_exited) {
            while (true) {
                ssize_t bytes_read = read(pipe_fd[0], buffer, sizeof(buffer));
                if (bytes_read <= 0) {
                    break;
                }
                output.append(buffer, static_cast<size_t>(bytes_read));
            }
            break;
        }

        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
                           std::chrono::steady_clock::now() - start_time)
                           .count();
        if (elapsed >= effective_timeout) {
            timed_out = true;
            kill(-pid, SIGKILL);
            waitpid(pid, &status, 0);
            break;
        }
    }

    close(pipe_fd[0]);

    if (timed_out) {
        return {false, -1, "", "timed out after " + std::to_string(effective_timeout) + "s"};
    }

    int exit_code = 0;
    if (WIFEXITED(status)) {
        exit_code = WEXITSTATUS(status);
    } else if (WIFSIGNALED(status)) {
        exit_code = 128 + WTERMSIG(status);
    }

    bool ok = (exit_code == 0);
    return {ok, exit_code, output, std::nullopt};
}

}  // namespace cmdiqo::agent

#endif
