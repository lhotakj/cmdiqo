#if defined(_WIN32)

#include "cmdiqo/agent/process.hpp"
#include "cmdiqo/agent/logger.hpp"

#include <windows.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <future>
#include <string>
#include <vector>

namespace cmdiqo::agent {

static std::wstring utf8_to_wide(std::string_view str) {
    if (str.empty()) return {};
    int size_needed = MultiByteToWideChar(CP_UTF8, 0, str.data(), static_cast<int>(str.size()), nullptr, 0);
    std::wstring result(size_needed, 0);
    MultiByteToWideChar(CP_UTF8, 0, str.data(), static_cast<int>(str.size()), result.data(), size_needed);
    return result;
}

static std::string base64_encode(const uint8_t* data, size_t len) {
    if (!data || len == 0) return {};
    static constexpr std::string_view table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((len + 2) / 3) * 4);
    for (size_t i = 0; i < len; i += 3) {
        uint32_t b = (static_cast<uint32_t>(data[i]) << 16);
        if (i + 1 < len) b |= (static_cast<uint32_t>(data[i + 1]) << 8);
        if (i + 2 < len) b |= static_cast<uint32_t>(data[i + 2]);
        out.push_back(table[(b >> 18) & 0x3F]);
        out.push_back(table[(b >> 12) & 0x3F]);
        out.push_back((i + 1 < len) ? table[(b >> 6) & 0x3F] : '=');
        out.push_back((i + 2 < len) ? table[b & 0x3F] : '=');
    }
    return out;
}

std::vector<std::string> build_argv(std::string_view kind, std::string_view cmd) {
    if (kind == "ps1") {
        std::wstring wide = utf8_to_wide(cmd);
        std::string b64 = base64_encode(
            reinterpret_cast<const uint8_t*>(wide.data()),
            wide.size() * sizeof(wchar_t)
        );
        return {"powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", b64};
    }
    return {"cmd.exe", "/c", std::string(cmd)};
}

static std::wstring build_command_line(const std::vector<std::string>& argv) {
    std::wstring cmdline;
    for (size_t i = 0; i < argv.size(); ++i) {
        if (i > 0) cmdline += L" ";
        std::wstring arg = utf8_to_wide(argv[i]);
        if (i == argv.size() - 1 && argv.size() > 2) {
            // For the last command payload, don't double quote if it's already an entire command block
            cmdline += arg;
        } else if (arg.find(L' ') != std::wstring::npos) {
            cmdline += L"\"" + arg + L"\"";
        } else {
            cmdline += arg;
        }
    }
    return cmdline;
}

ProcessResult execute_process(
    std::string_view kind,
    std::string_view cmd,
    const std::optional<std::string>& cwd,
    int timeout_seconds
) {
    int effective_timeout = (timeout_seconds > 0) ? timeout_seconds : 120;
    auto argv = build_argv(kind, cmd);
    std::wstring cmdline = build_command_line(argv);

    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = nullptr;

    HANDLE h_read_pipe = nullptr;
    HANDLE h_write_pipe = nullptr;
    if (!CreatePipe(&h_read_pipe, &h_write_pipe, &sa, 0)) {
        return {false, 1, "", "OS error: failed to create output pipe"};
    }
    SetHandleInformation(h_read_pipe, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = h_write_pipe;
    si.hStdError = h_write_pipe;
    si.hStdInput = nullptr;

    PROCESS_INFORMATION pi{};

    HANDLE h_job = CreateJobObjectW(nullptr, nullptr);
    if (h_job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli{};
        jeli.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(h_job, JobObjectExtendedLimitInformation, &jeli, sizeof(jeli));
    }

    std::wstring wide_cwd;
    const wchar_t* lp_cwd = nullptr;
    if (cwd.has_value() && !cwd->empty()) {
        wide_cwd = utf8_to_wide(*cwd);
        lp_cwd = wide_cwd.c_str();
    }

    DWORD creation_flags = CREATE_NO_WINDOW | CREATE_SUSPENDED;
    if (!CreateProcessW(
            nullptr,
            cmdline.data(),
            nullptr,
            nullptr,
            TRUE,
            creation_flags,
            nullptr,
            lp_cwd,
            &si,
            &pi)) {
        DWORD err = GetLastError();
        CloseHandle(h_read_pipe);
        CloseHandle(h_write_pipe);
        if (h_job) CloseHandle(h_job);
        if (err == ERROR_FILE_NOT_FOUND) {
            return {false, 127, "", "executable not found: CreateProcess failed"};
        }
        return {false, 1, "", "OS error: CreateProcessW error code " + std::to_string(err)};
    }

    // Assign to Job Object before resuming
    if (h_job) {
        AssignProcessToJobObject(h_job, pi.hProcess);
    }
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);

    // Parent must close write pipe so ReadFile reaches EOF when child finishes
    CloseHandle(h_write_pipe);

    // Read output in background async task to prevent pipe buffer deadlocks
    auto read_future = std::async(std::launch::async, [h_read_pipe]() -> std::string {
        std::string output;
        char buffer[4096];
        DWORD bytes_read = 0;
        while (ReadFile(h_read_pipe, buffer, sizeof(buffer), &bytes_read, nullptr) && bytes_read > 0) {
            output.append(buffer, bytes_read);
        }
        CloseHandle(h_read_pipe);
        return output;
    });

    DWORD wait_res = WaitForSingleObject(pi.hProcess, static_cast<DWORD>(effective_timeout * 1000));
    if (wait_res == WAIT_TIMEOUT) {
        if (h_job) {
            TerminateJobObject(h_job, static_cast<UINT>(-1));
        } else {
            TerminateProcess(pi.hProcess, static_cast<UINT>(-1));
        }
        CloseHandle(pi.hProcess);
        if (h_job) CloseHandle(h_job);
        read_future.wait_for(std::chrono::milliseconds(500));
        return {false, -1, "", "timed out after " + std::to_string(effective_timeout) + "s"};
    }

    DWORD exit_code = 0;
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    if (h_job) CloseHandle(h_job);

    std::string output = read_future.get();
    bool ok = (exit_code == 0);

    return {ok, static_cast<int>(exit_code), output, std::nullopt};
}

}  // namespace cmdiqo::agent

#endif
