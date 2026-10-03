#if !defined(_WIN32)

#include "cmdiqo/agent/process.hpp"
#include "cmdiqo/agent/logger.hpp"

#include <fcntl.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <future>
#include <string>
#include <thread>
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

    auto read_future = std::async(std::launch::async, [read_fd = pipe_fd[0]]() -> std::string {
        std::string output;
        char buffer[4096];
        ssize_t bytes_read = 0;
        while ((bytes_read = read(read_fd, buffer, sizeof(buffer))) > 0) {
            output.append(buffer, static_cast<size_t>(bytes_read));
        }
        close(read_fd);
        return output;
    });

    auto start_time = std::chrono::steady_clock::now();
    int status = 0;
    bool timed_out = false;

    while (true) {
        pid_t res = waitpid(pid, &status, WNOHANG);
        if (res == pid) {
            break;
        }
        if (res < 0) {
            break;
        }

        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
                           std::chrono::steady_clock::now() - start_time)
                           .count();
        if (elapsed >= effective_timeout) {
            timed_out = true;
            // Kill entire process group
            kill(-pid, SIGKILL);
            waitpid(pid, &status, 0);
            break;
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    if (timed_out) {
        read_future.wait_for(std::chrono::milliseconds(200));
        return {false, -1, "", "timed out after " + std::to_string(effective_timeout) + "s"};
    }

    std::string output = read_future.get();
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
