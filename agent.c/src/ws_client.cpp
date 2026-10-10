#include "cmdiqo/agent/ws_client.hpp"
#include "cmdiqo/agent/logger.hpp"
#include "cmdiqo/agent/process.hpp"
#include "cmdiqo/agent/protocol.hpp"
#include "cmdiqo/agent/version.hpp"

#include <ixwebsocket/IXNetSystem.h>
#include <ixwebsocket/IXWebSocket.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <thread>

namespace cmdiqo::agent {

AgentRunner::AgentRunner(AgentConfig config) : config_(std::move(config)) {
    ix::initNetSystem();
}

void AgentRunner::run(std::atomic<bool>& stop_signal) {
    double delay = config_.reconnect_delay;

    while (!stop_signal.load()) {
        ix::WebSocket ws;
        ws.setUrl(config_.hub_url);

        ix::WebSocketHttpHeaders headers;
        headers["Authorization"] = "Bearer " + config_.token;
        ws.setExtraHeaders(headers);
        ws.setPingInterval(20);

        // We control the reconnect loop to match Python's exponential backoff & logging
        ws.disableAutomaticReconnection();

        std::atomic<bool> is_connected{false};
        std::atomic<bool> connection_closed{false};
        std::string close_reason = "connection closed";

        ws.setOnMessageCallback([&](const ix::WebSocketMessagePtr& msg) {
            if (msg->type == ix::WebSocketMessageType::Open) {
                is_connected.store(true);
                delay = config_.reconnect_delay; // reset delay on connection

                HelloMessage hello{
                    .name = config_.name,
                    .version = std::string(get_version()),
                    .platform = get_platform_string(),
                    .arch = get_arch_string(),
                    .agent_connection_notify = config_.agent_connection_notify
                };

                std::string hello_json = hello.to_json().dump();
                ws.send(hello_json);

                Logger::instance().info(
                    "Connected to ", config_.hub_url,
                    " as '", config_.name, "' (v", get_version(), ")"
                );
            } else if (msg->type == ix::WebSocketMessageType::Message) {
                try {
                    auto j = nlohmann::json::parse(msg->str);
                    auto req = ExecRequest::from_json(j);
                    if (!req.has_value()) {
                        return;
                    }

                    std::string id_prefix = req->id.size() >= 8 ? req->id.substr(0, 8) : req->id;
                    std::string cwd_str = req->cwd.value_or("(default)");

                    Logger::instance().info(
                        "[", id_prefix, "] Received ", req->kind,
                        " execution request: ", req->cmd,
                        " (timeout=", req->timeout, "s, cwd=", cwd_str, ")"
                    );

                    ProcessResult res = execute_process(req->kind, req->cmd, req->cwd, req->timeout);

                    Logger::instance().info(
                        "[", id_prefix, "] Execution finished: ok=",
                        (res.ok ? "True" : "False"),
                        ", code=", res.returncode,
                        ", out_bytes=", res.output.size(),
                        ", error=", res.error.value_or("none")
                    );

                    ResultResponse resp{
                        .id = req->id,
                        .ok = res.ok,
                        .code = res.returncode,
                        .output = res.output,
                        .error = res.error
                    };

                    ws.send(resp.to_json().dump());
                } catch (const std::exception& exc) {
                    Logger::instance().warning("Failed to process message: ", exc.what());
                }
            } else if (msg->type == ix::WebSocketMessageType::Close) {
                close_reason = msg->closeInfo.reason.empty() ? "remote endpoint closed connection" : msg->closeInfo.reason;
                connection_closed.store(true);
            } else if (msg->type == ix::WebSocketMessageType::Error) {
                close_reason = msg->errorInfo.reason;
                connection_closed.store(true);
            }
        });

        ws.start();

        // Active connection loop
        while (!stop_signal.load() && !connection_closed.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        ws.stop();

        if (stop_signal.load()) {
            break;
        }

        std::ostringstream ss;
        ss << std::fixed << std::setprecision(1) << delay;
        Logger::instance().warning("Connection lost: ", close_reason, "; retrying in ", ss.str(), "s");

        // Interruptible reconnect sleep
        auto sleep_start = std::chrono::steady_clock::now();
        while (!stop_signal.load()) {
            auto elapsed = std::chrono::duration<double>(
                               std::chrono::steady_clock::now() - sleep_start)
                               .count();
            if (elapsed >= delay) {
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }

        // Exponential backoff
        delay = std::min(60.0, delay * 1.5);
    }
}

}  // namespace cmdiqo::agent
