#pragma once

#include "cmdiqo/agent/config.hpp"

#include <atomic>
#include <optional>
#include <string>
#include <string_view>

namespace cmdiqo::agent {

/**
 * @brief Parsed three-part semantic version for agent update comparisons.
 */
struct SemverTuple {
    int major{0};
    int minor{0};
    int patch{0};
    bool is_dev{false};
};

/**
 * @brief Result returned by a native self-update check or installation attempt.
 */
struct UpdateOutcome {
    bool ok{false};
    bool restart_scheduled{false};
    std::string message;
};

/**
 * @brief Parses version strings such as "v0.37.55", "0.37.0", or "0.37.devel+ABC".
 */
[[nodiscard]] std::optional<SemverTuple> parse_semver(std::string_view version);

/**
 * @brief Returns true when target represents a strictly newer version than current.
 */
[[nodiscard]] bool is_newer_version(std::string_view current, std::string_view target);

/**
 * @brief Returns the GitHub Release asset filename matching this OS and architecture.
 */
[[nodiscard]] std::string release_asset_name(std::string_view arch = {});

/**
 * @brief Validates that a download URL is a safe GitHub HTTPS release/API URL with no shell metacharacters.
 */
[[nodiscard]] bool is_valid_update_url(std::string_view url);

/**
 * @brief Downloads (from explicit URL or GitHub Releases) and stages/applies an agent update.
 *
 * On Windows, schedules a detached NT AUTHORITY\SYSTEM task running
 * `cmdiqo-agent-windows-x64-setup.exe --cli --upgrade` (outside the agent's Job Object).
 * On Linux, verifies ELF magic & `--version` output, atomically replaces the binary,
 * and prepares a detached service restart.
 */
UpdateOutcome perform_self_update(
    const AgentConfig& config,
    std::string_view target_version = "latest",
    std::string_view explicit_url = "",
    int timeout_seconds = 180);

/**
 * @brief Executes detached service restart after sending the WebSocket update response.
 */
void trigger_post_update_restart(std::atomic<bool>& stop_signal);

}  // namespace cmdiqo::agent
