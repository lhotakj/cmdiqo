#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace cmdiqo::agent {

/**
 * @brief Represents the result of a subprocess execution.
 */
struct ProcessResult {
    bool ok{false};                     ///< True if the process completed with exit code 0 and within timeout.
    int returncode{0};                  ///< Process exit code (or -1 if execution timed out).
    std::string output;                 ///< Combined stdout and stderr captured during execution.
    std::optional<std::string> error;   ///< Descriptive error string if execution failed or timed out.
};

/**
 * @brief Builds the platform-specific argv vector for command execution.
 *
 * Maps execution dialect to platform binaries:
 * - On Windows: "ps1" -> `["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd]`;
 *               "shell" -> `["cmd.exe", "/c", cmd]`.
 * - On Linux:   "ps1" -> `["pwsh", "-NoProfile", "-NonInteractive", "-Command", cmd]`;
 *               "shell" -> `["/bin/bash", "-c", cmd]`.
 *
 * @param kind Dialect identifier ("ps1" for PowerShell, "shell" for default shell).
 * @param cmd Command string to execute.
 * @return Vector of command-line argument strings.
 */
[[nodiscard]] std::vector<std::string> build_argv(std::string_view kind, std::string_view cmd);

/**
 * @brief Spawns and supervises a subprocess with timeout watchdog and process tree isolation.
 *
 * On Windows:
 * - Creates child process suspended and assigns it to a Job Object configured with
 *   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` to ensure all spawned child subprocesses are terminated.
 * - Asynchronously drains the stdout/stderr pipe in a background thread to prevent deadlocks.
 * - Terminates the entire Job Object if execution exceeds the specified timeout.
 *
 * On Linux:
 * - Forks and establishes a new process group via `setpgid(0, 0)`.
 * - Asynchronously drains pipe output.
 * - Sends `SIGKILL` to the entire process group (`-pid`) if timeout expires.
 *
 * @param kind Dialect identifier ("ps1" or "shell").
 * @param cmd Command string to execute.
 * @param cwd Optional working directory for the child process.
 * @param timeout_seconds Execution timeout in seconds (defaults to 120).
 * @return ProcessResult containing ok status, return code, combined output, and error description.
 */
ProcessResult execute_process(
    std::string_view kind,
    std::string_view cmd,
    const std::optional<std::string>& cwd = std::nullopt,
    int timeout_seconds = 120
);

}  // namespace cmdiqo::agent
