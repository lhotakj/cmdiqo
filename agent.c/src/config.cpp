#include "cmdiqo/agent/config.hpp"
#include "cmdiqo/agent/version.hpp"

#include <CLI/CLI.hpp>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>

#if defined(_WIN32)
#include <windows.h>
#else
#include <sys/utsname.h>
#include <unistd.h>
#endif

namespace cmdiqo::agent {

ConfigurationError::ConfigurationError(std::vector<ConfigIssue> issues)
    : issues_(std::move(issues)) {
    std::ostringstream ss;
    for (size_t i = 0; i < issues_.size(); ++i) {
        if (i > 0) ss << "; ";
        ss << issues_[i].name << ": " << issues_[i].problem;
    }
    message_ = ss.str();
}

const char* ConfigurationError::what() const noexcept {
    return message_.c_str();
}

static std::optional<std::string> get_env_var(const char* name) {
#if defined(_MSC_VER)
    char* val = nullptr;
    size_t len = 0;
    if (_dupenv_s(&val, &len, name) == 0 && val != nullptr) {
        std::string res(val);
        free(val);
        if (!res.empty()) return res;
    }
    return std::nullopt;
#else
    const char* val = std::getenv(name);
    if (val && *val) {
        return std::string(val);
    }
    return std::nullopt;
#endif
}

std::string get_system_hostname() {
#if defined(_WIN32)
    char buffer[MAX_COMPUTERNAME_LENGTH + 1];
    DWORD size = sizeof(buffer);
    if (GetComputerNameA(buffer, &size)) {
        return std::string(buffer, size);
    }
    return "localhost";
#else
    char buffer[256];
    if (gethostname(buffer, sizeof(buffer)) == 0) {
        buffer[sizeof(buffer) - 1] = '\0';
        return std::string(buffer);
    }
    return "localhost";
#endif
}

std::string get_platform_string() {
#if defined(_WIN32)
    OSVERSIONINFOEXW osvi{};
    osvi.dwOSVersionInfoSize = sizeof(osvi);
    return "Windows/10.0";
#else
    struct utsname buf{};
    if (uname(&buf) == 0) {
        return std::string(buf.sysname) + "/" + std::string(buf.release);
    }
    return "Linux/Generic";
#endif
}

std::string AgentConfig::format_preflight_error(const std::vector<ConfigIssue>& issues) {
    const size_t width = 76;
    const std::string sep(width, '=');

    std::string title = "[CMDIQO-AGENT] PREFLIGHT CONFIGURATION CHECK FAILED";
    size_t left_pad = (width > title.size()) ? (width - title.size()) / 2 : 0;
    std::string centered_title = std::string(left_pad, ' ') + title;

    std::ostringstream ss;
    ss << sep << "\n"
       << centered_title << "\n"
       << sep << "\n"
       << "The agent cannot start because required configuration parameters are missing\n"
       << "or invalid. Please review the following issues:\n\n";

    for (const auto& issue : issues) {
        ss << "  * " << issue.name << ":\n"
           << "      Problem:    " << issue.problem << "\n"
           << "      Resolution: " << issue.resolution << "\n\n";
    }

    ss << "Quick setup alternatives:\n"
       << "  - CLI:  cmdiqo-agent --hub-url ws://<HUB_IP>:8765 --token <AGENT_TOKEN>\n"
       << "  - Env:  set HUB_URL=ws://<HUB_IP>:8765 && set AGENT_TOKEN=<AGENT_TOKEN>\n"
       << "  - Help: cmdiqo-agent --help\n"
       << sep;

    return ss.str();
}

AgentConfig AgentConfig::load(int argc, const char* const* argv) {
    std::vector<std::string> args;
    args.reserve(argc > 1 ? static_cast<size_t>(argc - 1) : 0);
    for (int i = 1; i < argc; ++i) {
        args.emplace_back(argv[i]);
    }
    return load_from_args(args);
}

AgentConfig AgentConfig::load_from_args(const std::vector<std::string>& args) {
    CLI::App app{"cmdiqo-agent: LAN command agent"};
    app.set_version_flag("-v,--version", std::string(get_version_string()));

    std::optional<std::string> cli_hub_url;
    std::optional<std::string> cli_token;
    std::optional<std::string> cli_name;
    std::optional<std::string> cli_config;
    std::optional<double> cli_reconnect_delay;
    std::optional<std::string> cli_log_level;
    bool cli_agent_notify{false};
    bool cli_service{false};

    app.add_option("-u,--hub-url", cli_hub_url, "Hub WebSocket URL (e.g. ws://192.168.1.10:8765)");
    app.add_option("-t,--token", cli_token, "Shared AGENT_TOKEN secret");
    app.add_option("-n,--name", cli_name, "Agent hostname / target name");
    app.add_option("-c,--config", cli_config, "Path to config JSON file");
    app.add_option("-d,--reconnect-delay", cli_reconnect_delay, "Initial reconnect delay in seconds");
    app.add_option("--log-level", cli_log_level, "Logging level (DEBUG, INFO, WARNING, ERROR)");
    app.add_flag("--agent-connection-notify", cli_agent_notify, "Enable Slack notification on agent connect/disconnect");
    app.add_flag("--service", cli_service, "Run as native Windows Service under SCM");

    // Allow unrecognized flags to match python parse_known_args
    app.allow_extras();

    std::vector<std::string> reversed_args = args;
    std::reverse(reversed_args.begin(), reversed_args.end());
    try {
        app.parse(reversed_args);
    } catch (const CLI::ParseError& e) {
        // If version or help was requested, exit cleanly
        if (e.get_exit_code() == 0) {
            std::exit(app.exit(e));
        }
        throw ConfigurationError({{
            "CLI_ARGUMENTS",
            e.what(),
            "Run 'cmdiqo-agent --help' to inspect valid command-line flags"
        }});
    }

    std::vector<ConfigIssue> issues;
    nlohmann::json file_cfg = nlohmann::json::object();

    // 1. Config file check
    std::string config_path;
    if (cli_config.has_value() && !cli_config->empty()) {
        config_path = *cli_config;
    } else if (auto env_cfg = get_env_var("AGENT_CONFIG"); env_cfg.has_value()) {
        config_path = *env_cfg;
    }

    if (!config_path.empty()) {
        if (!std::filesystem::exists(config_path)) {
            issues.push_back({
                "CONFIG_FILE",
                "Configuration file not found at '" + config_path + "'",
                "Ensure file exists or check path passed via --config / AGENT_CONFIG"
            });
        } else {
            try {
                std::ifstream ifs(config_path);
                if (!ifs.is_open()) {
                    throw std::runtime_error("Permission denied or failed to open file");
                }
                ifs >> file_cfg;
            } catch (const std::exception& exc) {
                issues.push_back({
                    "CONFIG_FILE",
                    "Failed to read/parse JSON in '" + config_path + "': " + exc.what(),
                    "Check file permissions and ensure the file is valid JSON"
                });
            }
        }
    }

    // 2. Hub URL
    std::string hub_url;
    if (cli_hub_url.has_value() && !cli_hub_url->empty()) {
        hub_url = *cli_hub_url;
    } else if (auto env_hub = get_env_var("HUB_URL"); env_hub.has_value()) {
        hub_url = *env_hub;
    } else if (file_cfg.contains("hub_url") && file_cfg["hub_url"].is_string()) {
        hub_url = file_cfg["hub_url"].get<std::string>();
    }

    if (hub_url.empty()) {
        issues.push_back({
            "HUB_URL",
            "Hub WebSocket URL is required",
            "Provide via --hub-url, HUB_URL environment variable, or in config file"
        });
    } else if (hub_url.rfind("ws://", 0) != 0 && hub_url.rfind("wss://", 0) != 0) {
        issues.push_back({
            "HUB_URL",
            "Invalid WebSocket scheme for '" + hub_url + "'",
            "Must begin with ws:// (unencrypted) or wss:// (encrypted), e.g. ws://192.168.1.10:8765"
        });
    }

    // 3. Token
    std::string token;
    if (cli_token.has_value() && !cli_token->empty()) {
        token = *cli_token;
    } else if (auto env_tok = get_env_var("AGENT_TOKEN"); env_tok.has_value()) {
        token = *env_tok;
    } else if (file_cfg.contains("token") && file_cfg["token"].is_string()) {
        token = file_cfg["token"].get<std::string>();
    }

    if (token.empty()) {
        issues.push_back({
            "AGENT_TOKEN",
            "Shared secret token is required",
            "Provide via --token, AGENT_TOKEN environment variable, or in config file"
        });
    }

    // 4. Reconnect delay
    double reconnect_delay = 5.0;
    if (cli_reconnect_delay.has_value()) {
        reconnect_delay = *cli_reconnect_delay;
    } else if (auto env_delay = get_env_var("RECONNECT_DELAY"); env_delay.has_value()) {
        try {
            reconnect_delay = std::stod(*env_delay);
        } catch (...) {
            issues.push_back({
                "RECONNECT_DELAY",
                "Invalid reconnect delay value '" + *env_delay + "'",
                "Must be a positive number of seconds (e.g. 5.0)"
            });
        }
    } else if (file_cfg.contains("reconnect_delay") && file_cfg["reconnect_delay"].is_number()) {
        reconnect_delay = file_cfg["reconnect_delay"].get<double>();
    }

    if (reconnect_delay <= 0.0) {
        issues.push_back({
            "RECONNECT_DELAY",
            "Invalid reconnect delay value '" + std::to_string(reconnect_delay) + "'",
            "Must be a positive number of seconds (e.g. 5.0)"
        });
        reconnect_delay = 5.0;
    }

    if (!issues.empty()) {
        throw ConfigurationError(std::move(issues));
    }

    // 5. Agent connection notify
    bool agent_notify = cli_agent_notify;
    if (auto env_notify = get_env_var("AGENT_NOTIFICATION_NOTIFY"); env_notify.has_value()) {
        std::string v = *env_notify;
        std::transform(v.begin(), v.end(), v.begin(), ::tolower);
        agent_notify = (v == "true" || v == "1" || v == "yes" || v == "on");
    } else if (auto env_conn = get_env_var("AGENT_CONNECTION_NOTIFY"); env_conn.has_value()) {
        std::string v = *env_conn;
        std::transform(v.begin(), v.end(), v.begin(), ::tolower);
        agent_notify = (v == "true" || v == "1" || v == "yes" || v == "on");
    } else if (file_cfg.contains("agent_connection_notify")) {
        if (file_cfg["agent_connection_notify"].is_boolean()) {
            agent_notify = file_cfg["agent_connection_notify"].get<bool>();
        } else if (file_cfg["agent_connection_notify"].is_string()) {
            std::string v = file_cfg["agent_connection_notify"].get<std::string>();
            std::transform(v.begin(), v.end(), v.begin(), ::tolower);
            agent_notify = (v == "true" || v == "1" || v == "yes" || v == "on");
        }
    }

    // 6. Name
    std::string name;
    if (cli_name.has_value() && !cli_name->empty()) {
        name = *cli_name;
    } else if (auto env_name = get_env_var("AGENT_NAME"); env_name.has_value()) {
        name = *env_name;
    } else if (file_cfg.contains("name") && file_cfg["name"].is_string()) {
        name = file_cfg["name"].get<std::string>();
    } else {
        name = get_system_hostname();
    }

    // 7. Log level
    LogLevel log_lvl = LogLevel::Info;
    if (cli_log_level.has_value()) {
        log_lvl = parse_log_level(*cli_log_level);
    } else if (auto env_lvl = get_env_var("LOG_LEVEL"); env_lvl.has_value()) {
        log_lvl = parse_log_level(*env_lvl);
    }

    Logger::instance().set_level(log_lvl);

    return AgentConfig{
        .hub_url = hub_url,
        .token = token,
        .name = name,
        .reconnect_delay = reconnect_delay,
        .agent_connection_notify = agent_notify,
        .config_path = config_path,
        .log_level = log_lvl,
        .is_service = cli_service
    };
}

}  // namespace cmdiqo::agent
