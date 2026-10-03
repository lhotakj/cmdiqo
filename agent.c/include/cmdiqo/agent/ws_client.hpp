#pragma once

#include "cmdiqo/agent/config.hpp"

#include <atomic>

namespace cmdiqo::agent {

/**
 * @brief Asynchronous WebSocket execution runner for the native Cmdiqo agent.
 *
 * Establishes an outbound TLS/WSS connection to the Cmdiqo Bot Hub with Bearer token authentication,
 * transmits the initial `hello` handshake frame, maintains 20-second ping/pong keepalives,
 * executes inbound command requests, streams result payloads, and handles automatic reconnection
 * with exponential backoff.
 */
class AgentRunner {
public:
    /**
     * @brief Constructs an AgentRunner with the specified agent configuration.
     * @param config Validated AgentConfig instance.
     */
    explicit AgentRunner(AgentConfig config);

    /**
     * @brief Destructor.
     */
    ~AgentRunner() = default;

    /**
     * @brief Starts the blocking WebSocket event and reconnect loop.
     *
     * Runs continuously until the provided stop_signal atomic boolean is set to true.
     *
     * @param stop_signal Atomic boolean flag that requests termination when set to true.
     */
    void run(std::atomic<bool>& stop_signal);

private:
    AgentConfig config_;
};

}  // namespace cmdiqo::agent
