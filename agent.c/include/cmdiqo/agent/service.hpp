#pragma once

#include "cmdiqo/agent/config.hpp"

#include <atomic>
#include <functional>
#include <string_view>

namespace cmdiqo::agent {

/**
 * @brief Callback function type invoked as the core worker loop under Windows SCM or POSIX daemon.
 *
 * Accepts a reference to an atomic boolean stop flag. The worker should monitor this flag
 * and exit cleanly when set to true.
 */
using ServiceWorkerFn = std::function<void(std::atomic<bool>& stop_signal)>;

#if defined(_WIN32)
/**
 * @brief Dispatches and executes the agent lifecycle under the Windows Service Control Manager (SCM).
 *
 * Registers service control handlers, reports state transitions (START_PENDING, RUNNING, STOP_PENDING, STOPPED),
 * configures automated file logging to `C:\ProgramData\Cmdiqo\Agent\logs\service.log`, and executes the worker.
 *
 * @param service_name Wide string service name registered in SCM (e.g., L"CmdiqoAgent").
 * @param worker Worker function containing the main agent execution loop.
 */
void run_as_windows_service(std::wstring_view service_name, const ServiceWorkerFn& worker);
#endif

/**
 * @brief Configures POSIX signal handlers (SIGINT, SIGTERM) to notify the stop flag and ignores SIGPIPE.
 * @param stop_signal Atomic boolean flag set to true when termination signals are received.
 */
void setup_posix_signals(std::atomic<bool>& stop_signal);

}  // namespace cmdiqo::agent
