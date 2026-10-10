#pragma once

#include "cmdiqo/agent/logger.hpp"

#include <exception>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace cmdiqo::agent {

/**
 * @brief Represents a single validation issue encountered during agent configuration.
 */
struct ConfigIssue {
    std::string name;       ///< Name or category of the configuration parameter (e.g., "HUB_URL", "AGENT_TOKEN").
    std::string problem;    ///< Description of the specific problem detected.
    std::string resolution; ///< Actionable recommendation to resolve the configuration issue.
};

/**
 * @brief Exception thrown when agent preflight configuration validation fails.
 *
 * Contains a list of all identified configuration issues so the agent can format
 * a comprehensive diagnostic report before terminating.
 */
class ConfigurationError : public std::exception {
public:
    /**
     * @brief Constructs a ConfigurationError with a list of configuration issues.
     * @param issues Vector of detected configuration issues.
     */
    explicit ConfigurationError(std::vector<ConfigIssue> issues);

    /**
     * @brief Returns the error message summary.
     * @return Null-terminated explanatory string.
     */
    [[nodiscard]] const char* what() const noexcept override;

    /**
     * @brief Returns the collection of issues that caused this error.
     * @return Const reference to vector of ConfigIssue instances.
     */
    [[nodiscard]] const std::vector<ConfigIssue>& issues() const noexcept { return issues_; }

private:
    std::vector<ConfigIssue> issues_;
    std::string message_;
};

/**
 * @brief Comprehensive configuration structure for the Cmdiqo native agent.
 *
 * Encapsulates all runtime parameters resolved across CLI flags, environment variables,
 * optional JSON configuration files, and system defaults.
 */
struct AgentConfig {
    std::string hub_url;                  ///< Hub WebSocket URL (e.g., "ws://192.168.1.10:8765" or "wss://...").
    std::string token;                    ///< Shared secret authentication token.
    std::string name;                     ///< Agent hostname or custom identifier.
    double reconnect_delay{5.0};          ///< Initial reconnect delay in seconds (must be > 0.0).
    bool agent_connection_notify{false};  ///< Whether to trigger Slack notifications on connect/disconnect.
    std::string config_path;              ///< Path to the loaded JSON configuration file (if specified).
    LogLevel log_level{LogLevel::Info};   ///< Logging verbosity level.
    bool is_service{false};               ///< Whether the agent was invoked to run as a Windows Service.

    /**
     * @brief Loads and validates configuration from standard command-line arguments.
     * @param argc Argument count.
     * @param argv Argument vector.
     * @return Resolved and validated AgentConfig instance.
     * @throws ConfigurationError if required parameters are missing or invalid.
     */
    static AgentConfig load(int argc, const char* const* argv);

    /**
     * @brief Loads and validates configuration from a vector of string arguments.
     * @param args List of CLI argument strings.
     * @return Resolved and validated AgentConfig instance.
     * @throws ConfigurationError if required parameters are missing or invalid.
     */
    static AgentConfig load_from_args(const std::vector<std::string>& args);

    /**
     * @brief Formats a list of configuration issues into an exact 76-character visual diagnostic banner.
     * @param issues Vector of ConfigIssue instances.
     * @return Formatted multi-line diagnostic string ready for stderr output.
     */
    static std::string format_preflight_error(const std::vector<ConfigIssue>& issues);
};

/**
 * @brief Retrieves the local system hostname.
 * @return System hostname, or "localhost" if retrieval fails.
 */
[[nodiscard]] std::string get_system_hostname();

/**
 * @brief Retrieves the current OS platform identification string (e.g. "Windows/10.0" or "Linux/6.8").
 * @return Platform identifier string.
 */
[[nodiscard]] std::string get_platform_string();
[[nodiscard]] std::string get_arch_string();

}  // namespace cmdiqo::agent
