#pragma once

#include <nlohmann/json.hpp>

#include <algorithm>
#include <optional>
#include <string>

namespace cmdiqo::agent {

/**
 * @brief Initial handshake message payload sent by the agent upon connecting to the Hub.
 */
struct HelloMessage {
    std::string type{"hello"};            ///< Message type discriminator (always "hello").
    std::string name;                     ///< Agent hostname or user-defined alias.
    std::string version;                  ///< Agent binary semver string.
    std::string platform;                 ///< OS platform identifier (e.g. "Windows/10.0" or "Linux/6.8").
    std::string arch;                     ///< Machine architecture (e.g. "x86_64", "aarch64", "ARM64").
    bool agent_connection_notify{false};  ///< Whether to dispatch connection notifications to Slack.
    bool tamper_protect{false};           ///< Whether local Windows service tamper protection is enabled.
    bool auto_update{false};              ///< Whether automatic agent updates are enabled.
    bool update_capable{true};            ///< Whether this agent natively supports "type": "update" frames.

    /**
     * @brief Serializes the HelloMessage instance into a JSON object.
     * @return nlohmann::json representation.
     */
    [[nodiscard]] nlohmann::json to_json() const {
        nlohmann::json j = {
            {"type", type},
            {"name", name},
            {"version", version},
            {"platform", platform},
            {"agent_connection_notify", agent_connection_notify},
            {"tamper_protect", tamper_protect},
            {"auto_update", auto_update},
            {"update_capable", update_capable}
        };
        if (!arch.empty()) {
            j["arch"] = arch;
        }
        return j;
    }
};

/**
 * @brief Inbound command execution request dispatched by the Hub to the Agent.
 */
struct ExecRequest {
    std::string id;                   ///< Unique request UUID string.
    std::string type;                 ///< Message type discriminator (must be "exec").
    std::string kind{"shell"};        ///< Command dialect ("ps1" for PowerShell, "shell" for default OS shell).
    std::string cmd;                  ///< Command payload string to execute.
    std::optional<std::string> cwd;   ///< Optional working directory.
    int timeout{120};                 ///< Maximum execution timeout in seconds.

    /**
     * @brief Deserializes and validates an ExecRequest from a JSON object.
     * @param j Input JSON value.
     * @return std::optional<ExecRequest> containing parsed request, or std::nullopt if invalid.
     */
    static std::optional<ExecRequest> from_json(const nlohmann::json& j) {
        try {
            if (!j.is_object()) return std::nullopt;
            if (j.value("type", "") != "exec") return std::nullopt;

            ExecRequest req;
            req.id = j.value("id", "");
            req.type = "exec";
            req.kind = j.value("kind", "shell");
            req.cmd = j.value("cmd", "");
            int raw_timeout = j.value("timeout", 120);
            req.timeout = std::max(1, raw_timeout);

            if (j.contains("cwd") && !j["cwd"].is_null() && j["cwd"].is_string()) {
                std::string cwd_val = j["cwd"].get<std::string>();
                if (!cwd_val.empty()) {
                    req.cwd = cwd_val;
                }
            }

            return req;
        } catch (...) {
            return std::nullopt;
        }
    }
};

/**
 * @brief Inbound self-update request dispatched by the Hub to the Agent.
 */
struct UpdateRequest {
    std::string id;                   ///< Unique request UUID string.
    std::string type;                 ///< Message type discriminator (must be "update").
    std::string version{"latest"};    ///< Target version/tag ("latest" or e.g. "v0.37.0").
    std::string url;                  ///< Optional direct download URL resolved by Hub.
    int timeout{180};                 ///< Download/update timeout in seconds.

    /**
     * @brief Deserializes and validates an UpdateRequest from a JSON object.
     */
    static std::optional<UpdateRequest> from_json(const nlohmann::json& j) {
        try {
            if (!j.is_object()) return std::nullopt;
            if (j.value("type", "") != "update") return std::nullopt;

            UpdateRequest req;
            req.id = j.value("id", "");
            req.type = "update";
            if (j.contains("version") && j["version"].is_string()) {
                std::string ver = j["version"].get<std::string>();
                if (!ver.empty()) req.version = std::move(ver);
            }
            if (j.contains("url") && j["url"].is_string()) {
                req.url = j["url"].get<std::string>();
            }
            int raw_timeout = j.value("timeout", 180);
            req.timeout = std::max(10, raw_timeout);
            return req;
        } catch (...) {
            return std::nullopt;
        }
    }
};

/**
 * @brief Outbound execution result payload sent by the Agent back to the Hub.
 */
struct ResultResponse {
    std::string id;                   ///< Request UUID corresponding to the original ExecRequest.
    std::string type{"result"};       ///< Message type discriminator (always "result").
    bool ok{false};                   ///< Execution success indicator (true if exit code is 0 and no timeout).
    std::optional<int> code;          ///< Process exit returncode (null if execution timed out).
    std::string output;               ///< Captured stdout and stderr output stream.
    std::optional<std::string> error; ///< Error description or timeout message (null on clean success).

    /**
     * @brief Serializes the ResultResponse instance into a JSON object.
     * @return nlohmann::json representation matching the Cmdiqo Hub wire protocol.
     */
    [[nodiscard]] nlohmann::json to_json() const {
        nlohmann::json j{
            {"id", id},
            {"type", type},
            {"ok", ok},
            {"output", output}
        };

        if (code.has_value()) {
            j["code"] = *code;
        } else {
            j["code"] = nullptr;
        }

        if (error.has_value()) {
            j["error"] = *error;
        } else {
            j["error"] = nullptr;
        }

        return j;
    }
};

}  // namespace cmdiqo::agent
