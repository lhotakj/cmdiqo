#include "cmdiqo/installer.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <system_error>
#include <thread>
#include <utility>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#else
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace cmdiqo::installer {
namespace {

std::string environment(std::string_view name, std::string fallback = {}) {
    const char* value = std::getenv(std::string(name).c_str());
    return value == nullptr || *value == '\0' ? fallback : value;
}

bool contains_case_insensitive(std::string value, std::string needle) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
        return static_cast<char>(std::toupper(character));
    });
    std::transform(needle.begin(), needle.end(), needle.begin(), [](unsigned char character) {
        return static_cast<char>(std::toupper(character));
    });
    return value.find(needle) != std::string::npos;
}

#ifdef _WIN32
std::wstring wide(std::string_view value) {
    if (value.empty()) {
        return {};
    }
    const int size = MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0);
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size);
    return result;
}

std::wstring quote_windows_argument(std::string_view argument) {
    const std::wstring input = wide(argument);
    if (input.empty()) {
        return L"\"\"";
    }
    if (input.find_first_of(L" \t\"") == std::wstring::npos) {
        return input;
    }

    std::wstring result{L"\""};
    std::size_t slash_count = 0;
    for (const wchar_t character : input) {
        if (character == L'\\') {
            ++slash_count;
        } else if (character == L'"') {
            result.append(slash_count * 2 + 1, L'\\');
            result += character;
            slash_count = 0;
        } else {
            result.append(slash_count, L'\\');
            slash_count = 0;
            result += character;
        }
    }
    result.append(slash_count * 2, L'\\');
    result += L'"';
    return result;
}

std::wstring command_line(const std::vector<std::string>& argv) {
    std::wstring result;
    for (const auto& argument : argv) {
        if (!result.empty()) {
            result += L' ';
        }
        result += quote_windows_argument(argument);
    }
    return result;
}

constexpr const char* kAuthorizedStopControl = "135";
constexpr const char* kHardenedServiceSddl =
    "D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCLCSWRPLORC;;;BA)(A;;CCLCSWLORC;;;IU)(A;;CCLCSWLORC;;;SU)";
constexpr const char* kDefaultServiceSddl =
    "D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)";

std::filesystem::path system_helper_temp_dir() {
    std::array<char, MAX_PATH> win_dir{};
    const UINT len = GetWindowsDirectoryA(win_dir.data(), static_cast<DWORD>(win_dir.size()));
    if (len > 0 && len < win_dir.size()) {
        std::filesystem::path win_temp = std::filesystem::path(std::string(win_dir.data(), len)) / "Temp";
        std::error_code ec;
        if (std::filesystem::is_directory(win_temp, ec)) {
            return win_temp;
        }
    }
    std::error_code ec;
    return std::filesystem::temp_directory_path(ec);
}

std::string short_windows_path(const std::filesystem::path& path) {
    const std::string raw = path.string();
    std::array<char, MAX_PATH> short_buf{};
    const DWORD short_len = GetShortPathNameA(raw.c_str(), short_buf.data(), static_cast<DWORD>(short_buf.size()));
    if (short_len > 0 && short_len < short_buf.size()) {
        return std::string(short_buf.data(), short_len);
    }
    return raw;
}

bool run_as_system_script(const std::string& script_body, std::string& error_out) {
    static std::atomic<unsigned long> seq{0};
    const std::string id = std::to_string(GetCurrentProcessId()) + "-" + std::to_string(++seq);
    const std::filesystem::path dir = system_helper_temp_dir();
    const std::filesystem::path cmd_path = dir / ("cmdiqo-svc-" + id + ".cmd");
    const std::filesystem::path done_path = dir / ("cmdiqo-svc-" + id + ".done");
    const std::string task_name = "CmdiqoSvcHelper-" + id;

    {
        std::ofstream script(cmd_path, std::ios::binary | std::ios::trunc);
        if (!script) {
            error_out = "Could not write temporary SYSTEM helper script " + cmd_path.string() + ".";
            return false;
        }
        script << "@echo off\r\n"
               << script_body << "\r\n"
               << "set \"CMDIQO_EC=%ERRORLEVEL%\"\r\n"
               << "> \"" << done_path.string() << "\" echo %CMDIQO_EC%\r\n"
               << "exit /b %CMDIQO_EC%\r\n";
        if (!script) {
            error_out = "Could not flush temporary SYSTEM helper script.";
            return false;
        }
    }

    const std::string short_cmd = short_windows_path(cmd_path);
    const std::string tr = (short_cmd.find(' ') == std::string::npos)
        ? ("cmd.exe /c " + short_cmd)
        : ("cmd.exe /c \"" + short_cmd + "\"");

    const CommandResult created = PlatformOperations::run({
        "schtasks.exe", "/Create", "/TN", task_name, "/TR", tr,
        "/SC", "ONCE", "/ST", "00:00", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"});
    bool ok = false;
    if (created.exit_code != 0) {
        error_out = "Could not create SYSTEM helper task: " + created.output;
    } else {
        const CommandResult started = PlatformOperations::run({"schtasks.exe", "/Run", "/TN", task_name});
        if (started.exit_code != 0) {
            error_out = "Could not start SYSTEM helper task: " + started.output;
        } else {
            constexpr int max_wait_ticks = 200;  // 20 seconds
            std::error_code ec;
            for (int tick = 0; tick < max_wait_ticks; ++tick) {
                if (std::filesystem::exists(done_path, ec)) {
                    ok = true;
                    break;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            if (!ok) {
                error_out = "Timed out waiting for SYSTEM helper task completion.";
            } else {
                std::ifstream done_in(done_path);
                int script_exit = 1;
                if (done_in >> script_exit) {
                    if (script_exit != 0) {
                        ok = false;
                        error_out = "SYSTEM helper script exited with code " + std::to_string(script_exit) + ".";
                    }
                }
            }
        }
        PlatformOperations::run({"schtasks.exe", "/Delete", "/TN", task_name, "/F"});
    }

    std::error_code ec;
    std::filesystem::remove(cmd_path, ec);
    std::filesystem::remove(done_path, ec);
    return ok;
}

bool unprotect_service_via_system(const std::string& service_name, std::string& error_out) {
    return run_as_system_script(
        "sc.exe sdset " + service_name + " \"" + kDefaultServiceSddl + "\"",
        error_out);
}

#endif

}  // namespace

InstallerPaths PlatformOperations::paths() {
    if (current_platform() == Platform::windows) {
        const std::filesystem::path install_dir = std::filesystem::path(environment("ProgramFiles", R"(C:\Program Files)")) / "Cmdiqo" / "Agent";
        const std::filesystem::path config_dir = std::filesystem::path(environment("ProgramData", R"(C:\ProgramData)")) / "Cmdiqo" / "Agent";
        return {
            .install_dir = install_dir,
            .binary_path = install_dir / "cmdiqo-agent.exe",
            .config_dir = config_dir,
            .config_file = config_dir / "config.json",
            .legacy_env_file = config_dir / "agent.env",
            .log_dir = config_dir / "logs",
            .service_file = {},
            .service_name = "CmdiqoAgent",
        };
    }
    if (current_platform() == Platform::linux) {
        return {
            .install_dir = "/usr/local/bin",
            .binary_path = "/usr/local/bin/cmdiqo-agent",
            .config_dir = "/etc/cmdiqo-agent",
            .config_file = "/etc/cmdiqo-agent/config.json",
            .legacy_env_file = "/etc/cmdiqo-agent/agent.env",
            .log_dir = "/var/log/cmdiqo-agent",
            .service_file = "/etc/systemd/system/cmdiqo-agent.service",
            .service_name = "cmdiqo-agent.service",
        };
    }
    return {};
}

bool PlatformOperations::is_elevated() {
#ifdef _WIN32
    BOOL elevated = FALSE;
    SID_IDENTIFIER_AUTHORITY authority = SECURITY_NT_AUTHORITY;
    PSID administrators = nullptr;
    if (AllocateAndInitializeSid(
            &authority, 2, SECURITY_BUILTIN_DOMAIN_RID, DOMAIN_ALIAS_RID_ADMINS,
            0, 0, 0, 0, 0, 0, &administrators) == 0) {
        return false;
    }
    const BOOL checked = CheckTokenMembership(nullptr, administrators, &elevated);
    FreeSid(administrators);
    return checked != 0 && elevated != FALSE;
#elif defined(__linux__)
    return geteuid() == 0;
#else
    return false;
#endif
}

bool PlatformOperations::has_graphical_session() {
    return !environment("DISPLAY").empty() || !environment("WAYLAND_DISPLAY").empty();
}

CommandResult PlatformOperations::run(const std::vector<std::string>& argv) {
    if (argv.empty()) {
        return {.exit_code = -1, .output = "Cannot run an empty command.", .started = false};
    }

#ifdef _WIN32
    SECURITY_ATTRIBUTES security{.nLength = sizeof(SECURITY_ATTRIBUTES), .lpSecurityDescriptor = nullptr, .bInheritHandle = TRUE};
    HANDLE output_read = nullptr;
    HANDLE output_write = nullptr;
    if (CreatePipe(&output_read, &output_write, &security, 0) == 0) {
        return {.exit_code = -1, .output = "CreatePipe failed.", .started = false};
    }
    SetHandleInformation(output_read, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = output_write;
    startup.hStdError = output_write;
    PROCESS_INFORMATION process{};
    std::wstring line = command_line(argv);
    const BOOL created = CreateProcessW(
        nullptr, line.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
        nullptr, nullptr, &startup, &process);
    CloseHandle(output_write);
    if (created == 0) {
        CloseHandle(output_read);
        return {.exit_code = -1, .output = "CreateProcessW failed with error " + std::to_string(GetLastError()) + ".", .started = false};
    }

    std::string output;
    std::array<char, 4096> buffer{};
    DWORD read = 0;
    while (ReadFile(output_read, buffer.data(), static_cast<DWORD>(buffer.size()), &read, nullptr) != 0 && read > 0) {
        if (output.size() < 64 * 1024) {
            output.append(buffer.data(), std::min<std::size_t>(read, 64 * 1024 - output.size()));
        }
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(output_read);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return {.exit_code = static_cast<int>(exit_code), .output = std::move(output), .started = true};
#elif defined(__linux__)
    int output_pipe[2]{};
    if (pipe(output_pipe) != 0) {
        return {.exit_code = -1, .output = "pipe failed: " + std::string(std::strerror(errno)), .started = false};
    }
    std::vector<char*> arguments;
    arguments.reserve(argv.size() + 1);
    for (const auto& argument : argv) {
        arguments.push_back(const_cast<char*>(argument.c_str()));
    }
    arguments.push_back(nullptr);
    const pid_t process = fork();
    if (process < 0) {
        close(output_pipe[0]);
        close(output_pipe[1]);
        return {.exit_code = -1, .output = "fork failed: " + std::string(std::strerror(errno)), .started = false};
    }
    if (process == 0) {
        dup2(output_pipe[1], STDOUT_FILENO);
        dup2(output_pipe[1], STDERR_FILENO);
        close(output_pipe[0]);
        close(output_pipe[1]);
        execvp(arguments.front(), arguments.data());
        _exit(127);
    }

    close(output_pipe[1]);
    std::string output;
    std::array<char, 4096> buffer{};
    ssize_t count = 0;
    while ((count = read(output_pipe[0], buffer.data(), buffer.size())) > 0) {
        if (output.size() < 64 * 1024) {
            output.append(buffer.data(), std::min<std::size_t>(static_cast<std::size_t>(count), 64 * 1024 - output.size()));
        }
    }
    close(output_pipe[0]);
    int status = 0;
    waitpid(process, &status, 0);
    const int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    return {.exit_code = exit_code, .output = std::move(output), .started = true};
#else
    return {.exit_code = -1, .output = "This platform is not supported.", .started = false};
#endif
}

ServiceStatus PlatformOperations::service_status(const InstallerPaths& paths) {
    if (!std::filesystem::exists(paths.binary_path)
        && (current_platform() == Platform::linux && !std::filesystem::exists(paths.service_file))) {
        return ServiceStatus::not_installed;
    }
    const CommandResult result = current_platform() == Platform::windows
        ? run({"sc.exe", "query", paths.service_name})
        : run({"systemctl", "is-active", paths.service_name});
    if (!result.started) {
        return ServiceStatus::not_installed;
    }
    if (current_platform() == Platform::windows) {
        if (contains_case_insensitive(result.output, "FAILED 1060")) {
            return ServiceStatus::not_installed;
        }
        return contains_case_insensitive(result.output, "RUNNING") ? ServiceStatus::running : ServiceStatus::stopped;
    }
    return result.exit_code == 0 && contains_case_insensitive(result.output, "ACTIVE")
        ? ServiceStatus::running
        : ServiceStatus::stopped;
}

OperationResult PlatformOperations::start_service(const InstallerPaths& paths) {
    if (service_status(paths) == ServiceStatus::running) {
        return OperationResult::success("Service " + paths.service_name + " is already running.");
    }
    const CommandResult result = current_platform() == Platform::windows
        ? run({"sc.exe", "start", paths.service_name})
        : run({"systemctl", "start", paths.service_name});
    if (result.exit_code != 0) {
        if (current_platform() == Platform::windows && contains_case_insensitive(result.output, "1056")) {
            return OperationResult::success("Service " + paths.service_name + " is already running.");
        }
        return OperationResult::failure("Could not start service " + paths.service_name + ": " + result.output);
    }
    if (current_platform() == Platform::windows) {
        for (int attempt = 0; attempt < 20; ++attempt) {
            if (service_status(paths) == ServiceStatus::running) {
                return OperationResult::success("Started service " + paths.service_name + ".");
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        return OperationResult::failure("Service start timed out for " + paths.service_name + ".");
    }
    return OperationResult::success("Started service " + paths.service_name + ".");
}

OperationResult PlatformOperations::stop_service(const InstallerPaths& paths) {
#ifdef _WIN32
    const CommandResult result = run({"sc.exe", "stop", paths.service_name});
    if (result.exit_code != 0
        && !contains_case_insensitive(result.output, "1062")
        && !contains_case_insensitive(result.output, "1060")) {
        std::string system_err;
        const std::string stop_script =
            "sc.exe control " + paths.service_name + " " + kAuthorizedStopControl + "\r\n"
            "if errorlevel 1 sc.exe stop " + paths.service_name;
        if (!run_as_system_script(stop_script, system_err)) {
            return OperationResult::failure(
                "Could not stop service " + paths.service_name + ": " + result.output
                + " (SYSTEM fallback failed: " + system_err + ")");
        }
    }
    constexpr int max_wait_seconds = 30;
    for (int attempt = 0; attempt <= max_wait_seconds; ++attempt) {
        const CommandResult status = run({"sc.exe", "query", paths.service_name});
        if (contains_case_insensitive(status.output, "STOPPED")
            || contains_case_insensitive(status.output, "FAILED 1060")) {
            return OperationResult::success("Stopped service " + paths.service_name + ".");
        }
        if (attempt < max_wait_seconds) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    }
    return OperationResult::failure(
        "Service did not respond to stop within 30 seconds: " + paths.service_name + ".");
#else
    const CommandResult result = run({"systemctl", "stop", paths.service_name});
    if (result.exit_code != 0) {
        return OperationResult::failure("Could not stop service " + paths.service_name + ": " + result.output);
    }
    return OperationResult::success("Stopped service " + paths.service_name + ".");
#endif
}

OperationResult PlatformOperations::restart_service(const InstallerPaths& paths) {
    if (current_platform() == Platform::windows) {
        if (const OperationResult stopped = stop_service(paths); !stopped.ok) {
            return stopped;
        }
        return start_service(paths);
    }
    if (current_platform() != Platform::linux) {
        return OperationResult::failure("This platform is not supported.");
    }
    const CommandResult result = run({"systemctl", "restart", paths.service_name});
    if (result.exit_code == 0) {
        return OperationResult::success("Restarted service " + paths.service_name + ".");
    }
    return OperationResult::failure("Could not restart service " + paths.service_name + ": " + result.output);
}

OperationResult PlatformOperations::register_service(const InstallerPaths& paths, const Logger& logger, bool tamper_protect) {
#ifdef _WIN32
    const std::string stop_opt_flag = tamper_protect ? "" : " --no-tamper-protect";
    const std::string service_command =
        "\"" + paths.binary_path.string() + "\" --service" + stop_opt_flag
        + " --config \"" + paths.config_file.string() + "\"";
    const CommandResult query = run({"sc.exe", "query", paths.service_name});
    const bool exists = !contains_case_insensitive(query.output, "FAILED 1060");
    CommandResult result = exists
        ? run({"sc.exe", "config", paths.service_name, "binPath=", service_command, "start=", "auto", "DisplayName=", "Cmdiqo LAN Remote Agent"})
        : run({"sc.exe", "create", paths.service_name, "binPath=", service_command, "start=", "auto", "DisplayName=", "Cmdiqo LAN Remote Agent"});
    if (exists && result.exit_code != 0) {
        std::string system_err;
        if (unprotect_service_via_system(paths.service_name, system_err)) {
            result = run({"sc.exe", "config", paths.service_name, "binPath=", service_command, "start=", "auto", "DisplayName=", "Cmdiqo LAN Remote Agent"});
        }
    }
    if (result.exit_code != 0) {
        return OperationResult::failure("Could not register Windows service: " + result.output);
    }
    const CommandResult description = run({
        "sc.exe", "description", paths.service_name,
        "Cmdiqo LAN Remote Agent reverse WebSocket connection service"});
    if (description.exit_code != 0) {
        logger.error("Windows service description update failed: " + description.output);
    }
    const CommandResult failure_actions = run({
        "sc.exe", "failure", paths.service_name, "reset=", "86400",
        "actions=", "restart/5000/restart/10000/restart/30000"});
    if (failure_actions.exit_code != 0) {
        logger.error("Windows service failure-action update failed: " + failure_actions.output);
    }
    const char* target_sddl = tamper_protect ? kHardenedServiceSddl : kDefaultServiceSddl;
    const CommandResult sd_res = run({"sc.exe", "sdset", paths.service_name, target_sddl});
    if (sd_res.exit_code != 0) {
        std::string system_err;
        if (!run_as_system_script(
                "sc.exe sdset " + paths.service_name + " \"" + std::string(target_sddl) + "\"",
                system_err)) {
            return OperationResult::failure(
                "Registered service, but failed to configure service permissions: "
                + sd_res.output + " (" + system_err + ")");
        }
    }
    return OperationResult::success("Registered Windows service " + paths.service_name + ".");
#else
    (void)tamper_protect;
    if (current_platform() == Platform::linux) {
        if (!std::filesystem::exists(paths.service_file)) {
            std::ofstream unit(paths.service_file, std::ios::binary | std::ios::trunc);
            if (!unit) {
                return OperationResult::failure("Could not write systemd unit " + paths.service_file.string() + ".");
            }
            unit << "[Unit]\n"
                 << "Description=Cmdiqo LAN Remote Agent\n"
                 << "After=network-online.target\n"
                 << "Wants=network-online.target\n\n"
                 << "[Service]\n"
                 << "Type=simple\n"
                 << "ExecStart=" << paths.binary_path.string() << " --config " << paths.config_file.string() << "\n"
                 << "EnvironmentFile=-" << paths.legacy_env_file.string() << "\n"
                 << "Restart=always\n"
                 << "RestartSec=5\n\n"
                 << "[Install]\n"
                 << "WantedBy=multi-user.target\n";
            if (!unit) {
                return OperationResult::failure("Could not finish writing systemd unit " + paths.service_file.string() + ".");
            }
        }
        const CommandResult reload = run({"systemctl", "daemon-reload"});
        const CommandResult enable = run({"systemctl", "enable", paths.service_name});
        if (reload.exit_code != 0 || enable.exit_code != 0) {
            return OperationResult::failure(
                "Could not enable systemd service: " + (reload.exit_code != 0 ? reload.output : enable.output));
        }
        return OperationResult::success("Registered and enabled systemd service " + paths.service_name + ".");
    }
    return OperationResult::failure("This platform is not supported.");
#endif
}

OperationResult PlatformOperations::unregister_service(const InstallerPaths& paths, const Logger& logger) {
    (void)logger;
#ifdef _WIN32
    const CommandResult result = run({"sc.exe", "delete", paths.service_name});
    if (result.exit_code != 0 && !contains_case_insensitive(result.output, "1060")) {
        std::string system_err;
        const std::string delete_script =
            "sc.exe sdset " + paths.service_name + " \"" + kDefaultServiceSddl + "\"\r\n"
            "sc.exe delete " + paths.service_name;
        if (!run_as_system_script(delete_script, system_err)) {
            return OperationResult::failure(
                "Could not remove Windows service: " + result.output
                + " (SYSTEM fallback failed: " + system_err + ")");
        }
    }
    return OperationResult::success("Removed Windows service " + paths.service_name + ".");
#else
    if (current_platform() == Platform::linux) {
        const bool service_file_exists = std::filesystem::exists(paths.service_file);
        const CommandResult disable = run({"systemctl", "disable", paths.service_name});
        std::error_code error;
        std::filesystem::remove(paths.service_file, error);
        const CommandResult reload = run({"systemctl", "daemon-reload"});
        if ((disable.exit_code != 0 && service_file_exists)
            || reload.exit_code != 0 || error) {
            return OperationResult::failure("Could not remove systemd service.");
        }
        return OperationResult::success("Removed systemd service " + paths.service_name + ".");
    }
    return OperationResult::failure("This platform is not supported.");
#endif
}

bool PlatformOperations::is_service_tamper_protected(const InstallerPaths& paths) {
#ifdef _WIN32
    const CommandResult result = run({"sc.exe", "qc", paths.service_name});
    if (result.exit_code != 0 || contains_case_insensitive(result.output, "FAILED 1060")) {
        return true;
    }
    if (contains_case_insensitive(result.output, "--no-tamper-protect")
        || contains_case_insensitive(result.output, "--allow-service-stop")) {
        return false;
    }
    return true;
#else
    (void)paths;
    return false;
#endif
}

OperationResult PlatformOperations::secure_config_file(const std::filesystem::path& config_file) {
#ifdef _WIN32
    const CommandResult result = run({
        "icacls.exe", config_file.string(), "/inheritance:r", "/grant:r",
        "BUILTIN\\Administrators:(F)", "NT AUTHORITY\\SYSTEM:(F)", "BUILTIN\\Users:(R)"});
    if (result.exit_code != 0) {
        return OperationResult::failure("Could not restrict configuration ACLs: " + result.output);
    }
    return OperationResult::success("Restricted configuration ACLs.");
#else
    (void)config_file;
    return OperationResult::success("Configuration permissions are managed by the platform.");
#endif
}

bool PlatformOperations::relaunch_elevated(const std::vector<std::string>& arguments, const Logger& logger) {
    if (is_elevated()) {
        return true;
    }
#ifdef _WIN32
    char executable[MAX_PATH]{};
    const DWORD size = GetModuleFileNameA(nullptr, executable, MAX_PATH);
    if (size == 0 || size == MAX_PATH) {
        logger.error("Could not determine installer executable for elevation.");
        return false;
    }
    std::wstring parameters;
    for (const auto& argument : arguments) {
        if (!parameters.empty()) {
            parameters += L' ';
        }
        parameters += quote_windows_argument(argument);
    }
    const auto result = ShellExecuteW(nullptr, L"runas", wide(executable).c_str(), parameters.c_str(), nullptr, SW_SHOWNORMAL);
    if (reinterpret_cast<INT_PTR>(result) <= 32) {
        logger.error("Windows elevation request was cancelled or failed.");
        return false;
    }
    return true;
#elif defined(__linux__)
    if (!std::filesystem::exists("/usr/bin/pkexec") && !std::filesystem::exists("/bin/pkexec")) {
        logger.error("pkexec is not installed. Use the documented sudo --cli fallback.");
        return false;
    }
    std::array<char, 4096> executable{};
    const ssize_t length = readlink("/proc/self/exe", executable.data(), executable.size() - 1);
    if (length <= 0) {
        logger.error("Could not determine installer executable for pkexec.");
        return false;
    }
    executable[static_cast<std::size_t>(length)] = '\0';
    std::vector<char*> command;
    command.push_back(const_cast<char*>("pkexec"));
    command.push_back(executable.data());
    for (const auto& argument : arguments) {
        command.push_back(const_cast<char*>(argument.c_str()));
    }
    command.push_back(nullptr);
    const pid_t child = fork();
    if (child < 0) {
        logger.error("Could not create pkexec process.");
        return false;
    }
    if (child == 0) {
        execvp(command.front(), command.data());
        _exit(127);
    }
    return true;
#else
    logger.error("Elevation is unavailable on this platform.");
    return false;
#endif
}

OperationResult PlatformOperations::run_elevated(const std::vector<std::string>& arguments, const Logger& logger) {
    if (is_elevated()) {
        const CommandResult res = run(arguments);
        if (res.exit_code != 0) {
            return OperationResult::failure("Operation failed: " + res.output);
        }
        return OperationResult::success(res.output.empty() ? "Operation completed." : res.output);
    }

    std::error_code temp_ec;
    const auto temp_dir = std::filesystem::temp_directory_path(temp_ec);
    const auto process_id = 
#ifdef _WIN32
        GetCurrentProcessId();
#else
        getpid();
#endif
    const std::filesystem::path result_file = (!temp_ec && !temp_dir.empty())
        ? (temp_dir / ("cmdiqo-res-" + std::to_string(process_id) + ".txt"))
        : std::filesystem::path{};

    std::vector<std::string> full_arguments = arguments;
    if (!result_file.empty()) {
        full_arguments.push_back("--result-file");
        full_arguments.push_back(result_file.string());
    }

    const auto read_result_file = [&result_file](int exit_code) -> OperationResult {
        if (!result_file.empty() && std::filesystem::exists(result_file)) {
            std::ifstream res_in(result_file);
            std::string res_text((std::istreambuf_iterator<char>(res_in)), std::istreambuf_iterator<char>());
            res_in.close();
            std::error_code rm_ec;
            std::filesystem::remove(result_file, rm_ec);
            while (!res_text.empty() && (res_text.back() == '\r' || res_text.back() == '\n')) {
                res_text.pop_back();
            }
            if (res_text.starts_with("OK:")) {
                return OperationResult::success(res_text.substr(3));
            }
            if (res_text.starts_with("ERROR:")) {
                return OperationResult::failure(res_text.substr(6));
            }
        }
        if (exit_code != 0) {
            return OperationResult::failure("Elevated operation failed with exit code " + std::to_string(exit_code) + ".");
        }
        return OperationResult::success("Operation completed successfully.");
    };

#ifdef _WIN32
    char executable[MAX_PATH]{};
    const DWORD size = GetModuleFileNameA(nullptr, executable, MAX_PATH);
    if (size == 0 || size == MAX_PATH) {
        logger.error("Could not determine installer executable for elevation.");
        return OperationResult::failure("Could not determine installer executable for elevation.");
    }
    std::wstring parameters;
    for (const auto& argument : full_arguments) {
        if (!parameters.empty()) {
            parameters += L' ';
        }
        parameters += quote_windows_argument(argument);
    }

    SHELLEXECUTEINFOW sei{};
    sei.cbSize = sizeof(sei);
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.lpVerb = L"runas";
    sei.lpFile = wide(executable).c_str();
    sei.lpParameters = parameters.c_str();
    sei.nShow = SW_HIDE;

    if (!ShellExecuteExW(&sei)) {
        const DWORD err = GetLastError();
        if (err == ERROR_CANCELLED) {
            return OperationResult::failure("Administrator permission was not granted (cancelled by user).");
        }
        logger.error("Windows elevation request failed with error " + std::to_string(err));
        return OperationResult::failure("Elevation request failed (error " + std::to_string(err) + ").");
    }

    DWORD exit_code = 0;
    if (sei.hProcess != nullptr) {
        WaitForSingleObject(sei.hProcess, INFINITE);
        GetExitCodeProcess(sei.hProcess, &exit_code);
        CloseHandle(sei.hProcess);
    }
    return read_result_file(static_cast<int>(exit_code));
#elif defined(__linux__)
    if (!std::filesystem::exists("/usr/bin/pkexec") && !std::filesystem::exists("/bin/pkexec")) {
        logger.error("pkexec is not installed. Use the documented sudo --cli fallback.");
        return OperationResult::failure("pkexec is not installed. Run the command with sudo in terminal.");
    }
    std::array<char, 4096> executable{};
    const ssize_t length = readlink("/proc/self/exe", executable.data(), executable.size() - 1);
    if (length <= 0) {
        logger.error("Could not determine installer executable for pkexec.");
        return OperationResult::failure("Could not determine installer executable.");
    }
    executable[static_cast<std::size_t>(length)] = '\0';
    std::vector<std::string> command;
    command.push_back("pkexec");
    command.push_back(executable.data());
    for (const auto& argument : full_arguments) {
        command.push_back(argument);
    }
    const CommandResult result = run(command);
    return read_result_file(result.exit_code);
#else
    logger.error("Elevation is unavailable on this platform.");
    return OperationResult::failure("Elevation is unavailable on this platform.");
#endif
}

}  // namespace cmdiqo::installer
