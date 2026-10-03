#include "cmdiqo/agent/config.hpp"

#include <catch2/catch_test_macros.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>

#if defined(_WIN32)
static void set_test_env(const char* name, const char* value) {
    _putenv_s(name, value);
}
static void unset_test_env(const char* name) {
    _putenv_s(name, "");
}
#else
static void set_test_env(const char* name, const char* value) {
    setenv(name, value, 1);
}
static void unset_test_env(const char* name) {
    unsetenv(name);
}
#endif

struct ScopedEnvVar {
    std::string name;
    ScopedEnvVar(std::string var_name, const std::string& value) : name(std::move(var_name)) {
        set_test_env(name.c_str(), value.c_str());
    }
    ~ScopedEnvVar() {
        unset_test_env(name.c_str());
    }
};

TEST_CASE("Config: Preflight validation requires hub_url and token", "[config]") {
    // Clear potential environment variables
    unset_test_env("HUB_URL");
    unset_test_env("AGENT_TOKEN");
    unset_test_env("AGENT_CONFIG");

    std::vector<std::string> args = {"cmdiqo-agent"};
    REQUIRE_THROWS_AS(cmdiqo::agent::AgentConfig::load_from_args(args), cmdiqo::agent::ConfigurationError);

    try {
        cmdiqo::agent::AgentConfig::load_from_args(args);
    } catch (const cmdiqo::agent::ConfigurationError& exc) {
        REQUIRE(exc.issues().size() >= 2);
        std::string report = cmdiqo::agent::AgentConfig::format_preflight_error(exc.issues());
        REQUIRE(report.find("[CMDIQO-AGENT] PREFLIGHT CONFIGURATION CHECK FAILED") != std::string::npos);
        REQUIRE(report.find("HUB_URL") != std::string::npos);
        REQUIRE(report.find("AGENT_TOKEN") != std::string::npos);
        REQUIRE(std::string(exc.what()).find("HUB_URL") != std::string::npos);
    }
}

TEST_CASE("Config: Valid CLI flags parse correctly", "[config]") {
    unset_test_env("HUB_URL");
    unset_test_env("AGENT_TOKEN");

    std::vector<std::string> args = {
        "cmdiqo-agent",
        "--hub-url", "ws://192.168.1.10:8765",
        "--token", "secret123",
        "--name", "custom-node",
        "--reconnect-delay", "2.5",
        "--agent-connection-notify",
        "--log-level", "DEBUG"
    };

    auto cfg = cmdiqo::agent::AgentConfig::load_from_args(args);
    REQUIRE(cfg.hub_url == "ws://192.168.1.10:8765");
    REQUIRE(cfg.token == "secret123");
    REQUIRE(cfg.name == "custom-node");
    REQUIRE(cfg.reconnect_delay == 2.5);
    REQUIRE(cfg.agent_connection_notify == true);
    REQUIRE(cfg.log_level == cmdiqo::agent::LogLevel::Debug);
    REQUIRE(cfg.is_service == false);
}

TEST_CASE("Config: Invalid WebSocket scheme throws preflight error", "[config]") {
    unset_test_env("HUB_URL");
    unset_test_env("AGENT_TOKEN");

    std::vector<std::string> args = {
        "cmdiqo-agent",
        "--hub-url", "http://192.168.1.10:8765",
        "--token", "secret123"
    };

    REQUIRE_THROWS_AS(cmdiqo::agent::AgentConfig::load_from_args(args), cmdiqo::agent::ConfigurationError);
}

TEST_CASE("Config: Environment variables fallback", "[config]") {
    ScopedEnvVar env_hub("HUB_URL", "wss://hub.example.com:8765");
    ScopedEnvVar env_tok("AGENT_TOKEN", "env-secret-token");
    ScopedEnvVar env_name("AGENT_NAME", "env-node-name");
    ScopedEnvVar env_delay("RECONNECT_DELAY", "3.5");
    ScopedEnvVar env_notify("AGENT_CONNECTION_NOTIFY", "true");
    ScopedEnvVar env_lvl("LOG_LEVEL", "WARNING");

    std::vector<std::string> args = {"cmdiqo-agent"};
    auto cfg = cmdiqo::agent::AgentConfig::load_from_args(args);

    REQUIRE(cfg.hub_url == "wss://hub.example.com:8765");
    REQUIRE(cfg.token == "env-secret-token");
    REQUIRE(cfg.name == "env-node-name");
    REQUIRE(cfg.reconnect_delay == 3.5);
    REQUIRE(cfg.agent_connection_notify == true);
    REQUIRE(cfg.log_level == cmdiqo::agent::LogLevel::Warning);
}

TEST_CASE("Config: JSON configuration file loading", "[config]") {
    unset_test_env("HUB_URL");
    unset_test_env("AGENT_TOKEN");

    std::filesystem::path temp_cfg = std::filesystem::temp_directory_path() / "test_cmdiqo_agent.json";
    {
        std::ofstream ofs(temp_cfg);
        ofs << R"({
            "hub_url": "ws://10.0.0.99:8765",
            "token": "json-token-99",
            "name": "json-agent-node",
            "reconnect_delay": 4.0,
            "agent_connection_notify": true
        })";
    }

    std::vector<std::string> args = {
        "cmdiqo-agent",
        "--config", temp_cfg.string()
    };

    auto cfg = cmdiqo::agent::AgentConfig::load_from_args(args);
    REQUIRE(cfg.hub_url == "ws://10.0.0.99:8765");
    REQUIRE(cfg.token == "json-token-99");
    REQUIRE(cfg.name == "json-agent-node");
    REQUIRE(cfg.reconnect_delay == 4.0);
    REQUIRE(cfg.agent_connection_notify == true);

    std::filesystem::remove(temp_cfg);
}

TEST_CASE("Config: Missing or corrupt JSON config file errors", "[config]") {
    unset_test_env("HUB_URL");
    unset_test_env("AGENT_TOKEN");

    std::vector<std::string> missing_args = {
        "cmdiqo-agent",
        "--config", "nonexistent_cmdiqo_config_file_99999.json"
    };
    REQUIRE_THROWS_AS(cmdiqo::agent::AgentConfig::load_from_args(missing_args), cmdiqo::agent::ConfigurationError);

    std::filesystem::path corrupt_cfg = std::filesystem::temp_directory_path() / "test_corrupt_agent.json";
    {
        std::ofstream ofs(corrupt_cfg);
        ofs << "{not valid json syntax";
    }

    std::vector<std::string> corrupt_args = {
        "cmdiqo-agent",
        "--config", corrupt_cfg.string()
    };
    REQUIRE_THROWS_AS(cmdiqo::agent::AgentConfig::load_from_args(corrupt_args), cmdiqo::agent::ConfigurationError);

    std::filesystem::remove(corrupt_cfg);
}

TEST_CASE("Config: Precedence CLI > ENV > JSON", "[config]") {
    ScopedEnvVar env_hub("HUB_URL", "ws://env-hub:8765");
    ScopedEnvVar env_tok("AGENT_TOKEN", "env-token");

    std::filesystem::path temp_cfg = std::filesystem::temp_directory_path() / "test_precedence_agent.json";
    {
        std::ofstream ofs(temp_cfg);
        ofs << R"({
            "hub_url": "ws://json-hub:8765",
            "token": "json-token",
            "name": "json-name"
        })";
    }

    // CLI overrides ENV and JSON
    std::vector<std::string> args = {
        "cmdiqo-agent",
        "--config", temp_cfg.string(),
        "--hub-url", "ws://cli-hub:8765",
        "--name", "cli-name"
    };

    auto cfg = cmdiqo::agent::AgentConfig::load_from_args(args);
    REQUIRE(cfg.hub_url == "ws://cli-hub:8765"); // CLI wins
    REQUIRE(cfg.token == "env-token");            // ENV wins over JSON
    REQUIRE(cfg.name == "cli-name");             // CLI wins over JSON

    std::filesystem::remove(temp_cfg);
}

TEST_CASE("Config: Invalid and negative reconnect delay fallback", "[config]") {
    ScopedEnvVar env_hub("HUB_URL", "ws://127.0.0.1:8765");
    ScopedEnvVar env_tok("AGENT_TOKEN", "secret");

    std::vector<std::string> args = {
        "cmdiqo-agent",
        "--reconnect-delay", "-10.0"
    };

    REQUIRE_THROWS_AS(cmdiqo::agent::AgentConfig::load_from_args(args), cmdiqo::agent::ConfigurationError);
}

TEST_CASE("Config: Connection notify parsing string variants", "[config]") {
    ScopedEnvVar env_hub("HUB_URL", "ws://127.0.0.1:8765");
    ScopedEnvVar env_tok("AGENT_TOKEN", "secret");

    {
        ScopedEnvVar env_notify("AGENT_CONNECTION_NOTIFY", "1");
        auto cfg = cmdiqo::agent::AgentConfig::load_from_args({"cmdiqo-agent"});
        REQUIRE(cfg.agent_connection_notify == true);
    }
    {
        ScopedEnvVar env_notify("AGENT_CONNECTION_NOTIFY", "yes");
        auto cfg = cmdiqo::agent::AgentConfig::load_from_args({"cmdiqo-agent"});
        REQUIRE(cfg.agent_connection_notify == true);
    }
    {
        ScopedEnvVar env_notify("AGENT_CONNECTION_NOTIFY", "0");
        auto cfg = cmdiqo::agent::AgentConfig::load_from_args({"cmdiqo-agent"});
        REQUIRE(cfg.agent_connection_notify == false);
    }
}

TEST_CASE("Config: Platform string and system hostname helpers", "[config]") {
    std::string hostname = cmdiqo::agent::get_system_hostname();
    REQUIRE(!hostname.empty());

    std::string platform = cmdiqo::agent::get_platform_string();
    REQUIRE(!platform.empty());
}
