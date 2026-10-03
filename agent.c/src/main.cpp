#include "cmdiqo/agent/config.hpp"
#include "cmdiqo/agent/logger.hpp"
#include "cmdiqo/agent/service.hpp"
#include "cmdiqo/agent/version.hpp"
#include "cmdiqo/agent/ws_client.hpp"

#include <atomic>
#include <iostream>

#if defined(_WIN32)
#include <windows.h>
#endif

static std::atomic<bool> g_shutdown_requested{false};

#if defined(_WIN32)
static BOOL WINAPI console_ctrl_handler(DWORD ctrl_type) {
    if (ctrl_type == CTRL_C_EVENT || ctrl_type == CTRL_BREAK_EVENT || ctrl_type == CTRL_CLOSE_EVENT) {
        cmdiqo::agent::Logger::instance().info("Agent stopped by user (Ctrl+C).");
        g_shutdown_requested.store(true);
        return TRUE;
    }
    return FALSE;
}
#endif

int main(int argc, char* argv[]) {
    try {
        auto config = cmdiqo::agent::AgentConfig::load(argc, argv);

        if (config.is_service) {
#if defined(_WIN32)
            cmdiqo::agent::run_as_windows_service(L"CmdiqoAgent", [config](std::atomic<bool>& stop_signal) {
                cmdiqo::agent::AgentRunner runner(config);
                runner.run(stop_signal);
            });
            return 0;
#else
            std::cerr << "[ERROR] The --service option is only supported on Windows.\n";
            return 1;
#endif
        }

#if defined(_WIN32)
        SetConsoleCtrlHandler(console_ctrl_handler, TRUE);
#else
        cmdiqo::agent::setup_posix_signals(g_shutdown_requested);
#endif

        cmdiqo::agent::AgentRunner runner(config);
        runner.run(g_shutdown_requested);
        return 0;

    } catch (const cmdiqo::agent::ConfigurationError& exc) {
        std::cerr << cmdiqo::agent::AgentConfig::format_preflight_error(exc.issues()) << "\n";
        return 2;
    } catch (const std::exception& exc) {
        cmdiqo::agent::Logger::instance().critical("Fatal agent runtime error: ", exc.what());
        return 1;
    }
}
