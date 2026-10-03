#pragma once

#include <string>
#include <string_view>

#ifndef CMDIQO_AGENT_VERSION
#define CMDIQO_AGENT_VERSION "1.0.0"
#endif

namespace cmdiqo::agent {

/**
 * @brief Retrieves the agent's semantic version string literal.
 * @return String view representing the version (e.g. "1.0.0" or "1.0.devel+6AB4BAC9").
 */
[[nodiscard]] inline std::string_view get_version() noexcept {
    return CMDIQO_AGENT_VERSION;
}

/**
 * @brief Retrieves the formatted agent banner version string.
 * @return Formatted string (e.g. "cmdiqo-agent 1.0.0").
 */
[[nodiscard]] inline std::string get_version_string() {
    return std::string("cmdiqo-agent ") + std::string(get_version());
}

}  // namespace cmdiqo::agent
