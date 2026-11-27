#include "cmdiqo/agent/ws_client.hpp"
#include "cmdiqo/agent/logger.hpp"
#include "cmdiqo/agent/process.hpp"
#include "cmdiqo/agent/protocol.hpp"
#include "cmdiqo/agent/updater.hpp"
#include "cmdiqo/agent/version.hpp"

#include <ixwebsocket/IXNetSystem.h>
#include <ixwebsocket/IXWebSocket.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <iomanip>
#include <memory>
#include <mutex>
#include <sstream>
#include <thread>

namespace cmdiqo::agent {

AgentRunner::AgentRunner(AgentConfig config) : config_(std::move(config)) {
    ix::initNetSystem();
}

void AgentRunner::run(std::atomic<bool>& stop_signal) {
    double delay = config_.reconnect_delay;

    std::thread auto_update_thread;
    if (config_.auto_update) {
        auto_update_thread = std::thread([cfg = config_, &stop_signal]() {
            constexpr auto initial_delay = std::chrono::seconds(30);
            constexpr auto check_interval = std::chrono::hours(6);
            auto wait_interruptibly = [&](std::chrono::seconds duration) -> bool {
                const auto deadline = std::chrono::steady_clock::now() + duration;
                while (!stop_signal.load() && std::chrono::steady_clock::now() < deadline) {
                    std::this_thread::sleep_for(std::chrono::seconds(1));
                }
                return !stop_signal.load();
            };
            if (!wait_interruptibly(initial_delay)) {
                return;
            }
            while (!stop_signal.load()) {
                Logger::instance().info("Auto-update check: querying GitHub Releases...");
                const UpdateOutcome outcome = perform_self_update(cfg, "latest", "", 180);
                Logger::instance().info("Auto-update result: ", outcome.message);
                if (outcome.ok && outcome.restart_scheduled) {
                    trigger_post_update_restart(stop_signal);
                    break;
                }
                if (!wait_interruptibly(check_interval)) {
                    break;
                }
            }
        });
    }

    while (!stop_signal.load()) {
        auto ws = std::make_shared<ix::WebSocket>();
        ws->setUrl(config_.hub_url);

        ix::WebSocketHttpHeaders headers;
        headers["Authorization"] = "Bearer " + config_.token;
        ws->setExtraHeaders(headers);
        ws->setPingInterval(20);

        // We control the reconnect loop to match Python's exponential backoff & logging
        ws->disableAutomaticReconnection();

        std::atomic<bool> is_connected{false};
        std::atomic<bool> connection_closed{false};
        std::string close_reason = "connection closed";

        std::weak_ptr<ix::WebSocket> weak_ws = ws;

        ws->setOnMessageCallback([weak_ws, &config = this->config_, &stop_signal, &is_connected, &delay, &close_reason, &connection_closed](const ix::WebSocketMessagePtr& msg) {
            auto ws_ptr = weak_ws.lock();
            if (!ws_ptr) {
                return;
            }

            if (msg->type == ix::WebSocketMessageType::Open) {
                is_connected.store(true);
                delay = config.reconnect_delay; // reset delay on connection

                HelloMessage hello{
                    .name = config.name,
                    .version = std::string(get_version()),
                    .platform = get_platform_string(),
                    .arch = get_arch_string(),
                    .agent_connection_notify = config.agent_connection_notify,
                    .tamper_protect = config.tamper_protect,
                    .auto_update = config.auto_update,
                    .update_capable = true
                };

                std::string hello_json = hello.to_json().dump();
                ws_ptr->send(hello_json);

                Logger::instance().info(
                    "Connected to ", config.hub_url,
                    " as '", config.name, "' (v", get_version(), ")"
                );
            } else if (msg->type == ix::WebSocketMessageType::Message) {
                try {
                    auto j = nlohmann::json::parse(msg->str);
                    if (auto upd = UpdateRequest::from_json(j); upd.has_value()) {
                        std::thread([upd = *upd, weak_ws, &config, &stop_signal]() {
                            const UpdateOutcome outcome = perform_self_update(
                                config, upd.version, upd.url, upd.timeout);
                            ResultResponse resp{
                                .id = upd.id,
                                .ok = outcome.ok,
                                .code = outcome.ok ? 0 : 1,
                                .output = outcome.message,
                                .error = outcome.ok ? std::nullopt : std::optional<std::string>(outcome.message)
                            };
                            if (auto target_ws = weak_ws.lock()) {
                                target_ws->send(resp.to_json().dump());
                            }
                            if (outcome.ok && outcome.restart_scheduled) {
                                trigger_post_update_restart(stop_signal);
                            }
                        }).detach();
                        return;
                    }
                    auto req = ExecRequest::from_json(j);
                    if (!req.has_value()) {
                        return;
                    }

                    // Dispatch execution to a background worker thread so that the
                    // IXWebSocket network loop remains completely responsive to
                    // server PING keepalive frames during long-running commands.
                    std::thread([req = *req, weak_ws]() {
                        std::string id_prefix = req.id.size() >= 8 ? req.id.substr(0, 8) : req.id;
                        std::string cwd_str = req.cwd.value_or("(default)");

                        Logger::instance().info(
                            "[", id_prefix, "] Received ", req.kind,
                            " execution request: ", req.cmd,
                            " (timeout=", req.timeout, "s, cwd=", cwd_str, ")"
                        );

                        ProcessResult res = execute_process(req.kind, req.cmd, req.cwd, req.timeout);

                        Logger::instance().info(
                            "[", id_prefix, "] Execution finished: ok=",
                            (res.ok ? "True" : "False"),
                            ", code=", res.returncode,
                            ", out_bytes=", res.output.size(),
                            ", error=", res.error.value_or("none")
                        );

                        ResultResponse resp{
                            .id = req.id,
                            .ok = res.ok,
                            .code = res.returncode,
                            .output = res.output,
                            .error = res.error
                        };

                        if (auto target_ws = weak_ws.lock()) {
                            target_ws->send(resp.to_json().dump());
                        }
                    }).detach();
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

        ws->start();

        // Active connection loop
        while (!stop_signal.load() && !connection_closed.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        ws->stop();

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

    if (auto_update_thread.joinable()) {
        auto_update_thread.join();
    }
}

}  // namespace cmdiqo::agent
