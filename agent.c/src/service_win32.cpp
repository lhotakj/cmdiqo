#if defined(_WIN32)

#include "cmdiqo/agent/service.hpp"
#include "cmdiqo/agent/logger.hpp"

#include <windows.h>
#include <shlobj.h>

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <iostream>

namespace cmdiqo::agent {

static SERVICE_STATUS_HANDLE g_status_handle = nullptr;
static std::atomic<bool> g_service_stop_requested{false};
static ServiceWorkerFn g_worker_fn;
static std::wstring g_service_name_str;

static void set_service_status(DWORD state, DWORD win32_exit_code = NO_ERROR, DWORD wait_hint = 0) {
    if (!g_status_handle) return;

    SERVICE_STATUS status{};
    status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    status.dwCurrentState = state;
    status.dwControlsAccepted = (state == SERVICE_RUNNING) ? (SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN) : 0;
    status.dwWin32ExitCode = win32_exit_code;
    status.dwServiceSpecificExitCode = 0;
    status.dwCheckPoint = 0;
    status.dwWaitHint = wait_hint;

    SetServiceStatus(g_status_handle, &status);
}

static DWORD WINAPI service_ctrl_handler(DWORD dwControl, DWORD dwEventType, LPVOID lpEventData, LPVOID lpContext) {
    (void)dwEventType;
    (void)lpEventData;
    (void)lpContext;

    if (dwControl == SERVICE_CONTROL_STOP || dwControl == SERVICE_CONTROL_SHUTDOWN) {
        Logger::instance().info("Received stop/shutdown signal from Windows Service Control Manager.");
        set_service_status(SERVICE_STOP_PENDING, NO_ERROR, 5000);
        g_service_stop_requested.store(true);
        return NO_ERROR;
    }
    if (dwControl == SERVICE_CONTROL_INTERROGATE) {
        return NO_ERROR;
    }
    return ERROR_CALL_NOT_IMPLEMENTED;
}

static void WINAPI service_main(DWORD argc, LPWSTR* argv) {
    (void)argc;
    (void)argv;

    g_status_handle = RegisterServiceCtrlHandlerExW(g_service_name_str.c_str(), service_ctrl_handler, nullptr);
    if (!g_status_handle) {
        Logger::instance().error("RegisterServiceCtrlHandlerExW failed.");
        return;
    }

    set_service_status(SERVICE_START_PENDING, NO_ERROR, 3000);
    set_service_status(SERVICE_RUNNING);
    Logger::instance().info("Cmdiqo Windows Service is active and reported RUNNING to SCM.");

    try {
        if (g_worker_fn) {
            g_worker_fn(g_service_stop_requested);
        }
    } catch (const std::exception& exc) {
        Logger::instance().critical("Exception in Windows service worker loop: ", exc.what());
    }

    Logger::instance().info("Cmdiqo Windows Service terminated; reporting STOPPED to SCM.");
    set_service_status(SERVICE_STOPPED);
}

void run_as_windows_service(std::wstring_view service_name, const ServiceWorkerFn& worker) {
    g_service_name_str = service_name;
    g_worker_fn = worker;

    char program_data[MAX_PATH];
    std::filesystem::path log_path = "C:\\ProgramData\\Cmdiqo\\Agent\\logs\\service.log";
    if (SUCCEEDED(SHGetFolderPathA(nullptr, CSIDL_COMMON_APPDATA, nullptr, 0, program_data))) {
        log_path = std::filesystem::path(program_data) / "Cmdiqo" / "Agent" / "logs" / "service.log";
    }
    Logger::instance().enable_file_logging(log_path);
    Logger::instance().info("Starting CmdiqoAgent service dispatcher (logs: ", log_path.string(), ")...");

    SERVICE_TABLE_ENTRYW dispatch_table[] = {
        {const_cast<LPWSTR>(g_service_name_str.c_str()), service_main},
        {nullptr, nullptr}
    };

    if (!StartServiceCtrlDispatcherW(dispatch_table)) {
        DWORD err = GetLastError();
        Logger::instance().error("StartServiceCtrlDispatcherW failed with Win32 error ", err);
        if (err == 1063 /* ERROR_FAILED_SERVICE_CONTROLLER_CONNECT */) {
            std::cerr << "[ERROR] This program was invoked with --service, but it was not started\n"
                      << "by the Windows Service Control Manager (SCM).\n\n"
                      << "  * To run interactively: omit the --service flag.\n"
                      << "  * To install as a service: run install-service.ps1 as Administrator.\n\n";
            std::exit(1);
        }
        std::exit(static_cast<int>(err));
    }
}

void setup_posix_signals(std::atomic<bool>& stop_signal) {
    (void)stop_signal;
}

}  // namespace cmdiqo::agent

#endif
