#if !defined(_WIN32)

#include "cmdiqo/agent/service.hpp"
#include "cmdiqo/agent/logger.hpp"

#include <csignal>

namespace cmdiqo::agent {

static std::atomic<bool>* g_posix_stop_signal = nullptr;

static void signal_handler(int sig) {
    if (sig == SIGINT || sig == SIGTERM) {
        Logger::instance().info("Received signal ", sig, " (stopping agent)...");
        if (g_posix_stop_signal) {
            g_posix_stop_signal->store(true);
        }
    }
}

void setup_posix_signals(std::atomic<bool>& stop_signal) {
    g_posix_stop_signal = &stop_signal;
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);
    std::signal(SIGPIPE, SIG_IGN);
}

}  // namespace cmdiqo::agent

#endif
