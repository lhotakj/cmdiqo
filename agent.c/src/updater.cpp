#include "cmdiqo/agent/updater.hpp"
#include "cmdiqo/agent/logger.hpp"
#include "cmdiqo/agent/process.hpp"
#include "cmdiqo/agent/version.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <sstream>
#include <thread>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <process.h>
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace cmdiqo::agent {
namespace {

constexpr const char* kGithubReleasesApi = "https://api.github.com/repos/lhotakj/cmdiqo/releases";
std::mutex g_update_mutex;

std::string ps_single_quote(std::string_view value) {
    std::string out;
    out.reserve(value.size());
    for (char ch : value) {
        if (ch == '\'') {
            out += "''";
        } else {
            out.push_back(ch);
        }
    }
    return out;
}

std::string sh_single_quote(std::string_view value) {
    std::string out;
    out.reserve(value.size());
    for (char ch : value) {
        if (ch == '\'') {
            out += "'\\''";
        } else {
            out.push_back(ch);
        }
    }
    return out;
}

bool read_digits(std::string_view& s, int& out) {
    if (s.empty() || !std::isdigit(static_cast<unsigned char>(s.front()))) {
        return false;
    }
    long val = 0;
    size_t consumed = 0;
    while (consumed < s.size() && std::isdigit(static_cast<unsigned char>(s[consumed]))) {
        val = val * 10 + (s[consumed] - '0');
        ++consumed;
    }
    s.remove_prefix(consumed);
    out = static_cast<int>(val);
    return true;
}

bool check_binary_header(const std::filesystem::path& path) {
    std::error_code ec;
    const auto sz = std::filesystem::file_size(path, ec);
    if (ec || sz < 102400) {
        return false;
    }
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) {
        return false;
    }
    std::array<unsigned char, 4> hdr{};
    ifs.read(reinterpret_cast<char*>(hdr.data()), static_cast<std::streamsize>(hdr.size()));
    if (ifs.gcount() < 4) {
        return false;
    }
#if defined(_WIN32)
    return hdr[0] == 'M' && hdr[1] == 'Z';
#else
    return hdr[0] == 0x7f && hdr[1] == 'E' && hdr[2] == 'L' && hdr[3] == 'F';
#endif
}

std::string fetch_github_release_json(std::string_view target_version, int timeout_seconds) {
    std::string url = std::string(kGithubReleasesApi) + "/latest";
    if (!target_version.empty() && target_version != "latest") {
        std::string tag(target_version);
        if (tag.front() != 'v' && tag.front() != 'V') {
            tag = "v" + tag;
        }
        url = std::string(kGithubReleasesApi) + "/tags/" + tag;
    }
    if (!is_valid_update_url(url)) {
        return {};
    }
#if defined(_WIN32)
    const std::string ps_cmd =
        "$ErrorActionPreference = 'Stop'; "
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        "(Invoke-WebRequest -Uri '" + ps_single_quote(url) + "' "
        "-Headers @{Accept='application/vnd.github+json'; 'User-Agent'='cmdiqo-agent-updater'} "
        "-UseBasicParsing -TimeoutSec " + std::to_string((std::min)(30, timeout_seconds)) + ").Content";
    ProcessResult res = execute_process("ps1", ps_cmd, std::nullopt, (std::min)(35, timeout_seconds));
#else
    const std::string sh_cmd =
        "if command -v curl >/dev/null 2>&1; then "
        "curl -fsSL --connect-timeout 15 --max-time 30 "
        "-H 'Accept: application/vnd.github+json' -H 'User-Agent: cmdiqo-agent-updater' '"
        + sh_single_quote(url) + "'; "
        "elif command -v wget >/dev/null 2>&1; then "
        "wget -qO- --header='Accept: application/vnd.github+json' --header='User-Agent: cmdiqo-agent-updater' '"
        + sh_single_quote(url) + "'; "
        "else exit 127; fi";
    ProcessResult res = execute_process("shell", sh_cmd, std::nullopt, std::min(35, timeout_seconds));
#endif
    return res.ok ? res.output : std::string{};
}

#if !defined(_WIN32)
std::filesystem::path current_linux_binary_path() {
    std::array<char, 4096> buf{};
    const ssize_t len = readlink("/proc/self/exe", buf.data(), buf.size() - 1);
    if (len > 0) {
        std::string raw(buf.data(), static_cast<size_t>(len));
        constexpr std::string_view deleted_suffix = " (deleted)";
        if (raw.size() > deleted_suffix.size()
            && raw.compare(raw.size() - deleted_suffix.size(), deleted_suffix.size(), deleted_suffix) == 0) {
            raw.resize(raw.size() - deleted_suffix.size());
        }
        return std::filesystem::path(raw);
    }
    return "/usr/local/bin/cmdiqo-agent";
}
#endif

}  // namespace

std::optional<SemverTuple> parse_semver(std::string_view version) {
    while (!version.empty() && std::isspace(static_cast<unsigned char>(version.front()))) {
        version.remove_prefix(1);
    }
    if (!version.empty() && (version.front() == 'v' || version.front() == 'V')) {
        version.remove_prefix(1);
    }
    SemverTuple parsed{};
    if (!read_digits(version, parsed.major)) {
        return std::nullopt;
    }
    if (version.empty() || version.front() != '.') {
        return std::nullopt;
    }
    version.remove_prefix(1);
    if (!read_digits(version, parsed.minor)) {
        return std::nullopt;
    }
    if (version.empty()) {
        return parsed;
    }
    if (version.front() != '.') {
        return std::nullopt;
    }
    version.remove_prefix(1);
    if (version.rfind("devel", 0) == 0) {
        parsed.is_dev = true;
        parsed.patch = -1;
        return parsed;
    }
    if (!read_digits(version, parsed.patch)) {
        return std::nullopt;
    }
    return parsed;
}

bool is_newer_version(std::string_view current, std::string_view target) {
    const auto cur = parse_semver(current);
    const auto tgt = parse_semver(target);
    if (!cur || !tgt) {
        return current != target;
    }
    if (tgt->major != cur->major) return tgt->major > cur->major;
    if (tgt->minor != cur->minor) return tgt->minor > cur->minor;
    return tgt->patch > cur->patch;
}

std::string release_asset_name(std::string_view arch) {
    const std::string resolved = arch.empty() ? get_arch_string() : std::string(arch);
    if (resolved == "win64") {
        return "cmdiqo-agent-windows-x64-setup.exe";
    }
    if (resolved == "arm64") {
        return "cmdiqo-agent-linux-arm64";
    }
    if (resolved == "armv6") {
        return "cmdiqo-agent-linux-armv6";
    }
    return "cmdiqo-agent";
}

bool is_valid_update_url(std::string_view url) {
    constexpr std::string_view https_prefix = "https://";
    if (url.rfind(https_prefix, 0) != 0) {
        return false;
    }
    for (char ch : url) {
        unsigned char uch = static_cast<unsigned char>(ch);
        if (std::iscntrl(uch) || std::isspace(uch)) {
            return false;
        }
        if (ch == '\'' || ch == '"' || ch == ';' || ch == '&' || ch == '|' || ch == '`' || ch == '$') {
            return false;
        }
    }
    std::string_view rest = url.substr(https_prefix.size());
    const size_t slash = rest.find('/');
    const std::string_view host = (slash == std::string_view::npos) ? rest : rest.substr(0, slash);
    return host == "github.com"
        || host == "api.github.com"
        || host == "objects.githubusercontent.com"
        || host == "release-assets.githubusercontent.com";
}

UpdateOutcome perform_self_update(
    const AgentConfig& config,
    std::string_view target_version,
    std::string_view explicit_url,
    int timeout_seconds) {
    std::unique_lock<std::mutex> lock(g_update_mutex, std::try_to_lock);
    if (!lock.owns_lock()) {
        return {false, false, "An agent update is already in progress."};
    }

    const std::string current_ver(get_version());
    std::string resolved_version = (target_version.empty() || target_version == "latest")
        ? std::string{}
        : std::string(target_version);
    std::string download_url(explicit_url);

    if (download_url.empty()) {
        const std::string raw_json = fetch_github_release_json(target_version, timeout_seconds);
        if (raw_json.empty()) {
            return {false, false, "Failed to query GitHub Releases metadata."};
        }
        try {
            const auto j = nlohmann::json::parse(raw_json);
            const std::string tag_name = j.value("tag_name", "");
            if (resolved_version.empty() && !tag_name.empty()) {
                resolved_version = tag_name;
            }
            const std::string expected_asset = release_asset_name();
            if (j.contains("assets") && j["assets"].is_array()) {
                for (const auto& asset : j["assets"]) {
                    if (asset.is_object() && asset.value("name", "") == expected_asset) {
                        download_url = asset.value("browser_download_url", "");
                        break;
                    }
                }
            }
        } catch (const std::exception& exc) {
            return {false, false, std::string("Failed parsing release metadata: ") + exc.what()};
        }
    }

    if (download_url.empty() || !is_valid_update_url(download_url)) {
        return {false, false, "Missing or untrusted GitHub release asset URL."};
    }

    if ((target_version.empty() || target_version == "latest")
        && !resolved_version.empty()
        && !is_newer_version(current_ver, resolved_version)) {
        return {
            true,
            false,
            "ALREADY_UP_TO_DATE: Running v" + current_ver + " (latest is " + resolved_version + ")."
        };
    }

    const std::string display_target = resolved_version.empty() ? std::string(target_version) : resolved_version;

#if defined(_WIN32)
    std::error_code ec;
    const std::filesystem::path temp_dir = std::filesystem::temp_directory_path(ec);
    const std::filesystem::path setup_exe = temp_dir / "cmdiqo-agent-self-update.exe";
    const std::filesystem::path helper_cmd = temp_dir / "cmdiqo-agent-self-update.cmd";

    const std::string dl_ps =
        "$ErrorActionPreference = 'Stop'; "
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        "Invoke-WebRequest -Uri '" + ps_single_quote(download_url) + "' "
        "-OutFile '" + ps_single_quote(setup_exe.string()) + "' "
        "-UseBasicParsing -TimeoutSec " + std::to_string((std::max)(30, timeout_seconds));
    const ProcessResult dl_res = execute_process("ps1", dl_ps, std::nullopt, timeout_seconds);
    if (!dl_res.ok) {
        std::filesystem::remove(setup_exe, ec);
        return {false, false, "Download failed: " + (dl_res.error.value_or(dl_res.output))};
    }
    if (!check_binary_header(setup_exe)) {
        std::filesystem::remove(setup_exe, ec);
        return {false, false, "Downloaded setup executable failed integrity header check."};
    }

    std::string extra_flags;
    if (config.allow_service_stop) {
        extra_flags += " --no-tamper-protect";
    }
    if (config.auto_update) {
        extra_flags += " --auto-update";
    }

    {
        std::ofstream cmd_out(helper_cmd, std::ios::binary | std::ios::trunc);
        if (!cmd_out) {
            std::filesystem::remove(setup_exe, ec);
            return {false, false, "Could not write temporary upgrade script."};
        }
        cmd_out << "@echo off\r\n"
                << "ping -n 3 127.0.0.1 >nul\r\n"
                << "\"" << setup_exe.string() << "\" --cli --upgrade" << extra_flags << "\r\n"
                << "del /f /q \"" << setup_exe.string() << "\" \"" << helper_cmd.string() << "\"\r\n";
    }

    const std::string task_name = "CmdiqoAgentSelfUpdate-" + std::to_string(_getpid());
    const std::string sched_ps =
        "$ErrorActionPreference = 'Stop'; "
        "$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c \"" + ps_single_quote(helper_cmd.string()) + "\"'; "
        "$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\\SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        "Register-ScheduledTask -TaskName '" + ps_single_quote(task_name) + "' -Action $action -Principal $principal -Settings $settings -Force | Out-Null; "
        "Start-ScheduledTask -TaskName '" + ps_single_quote(task_name) + "'";
    const ProcessResult sched_res = execute_process("ps1", sched_ps, std::nullopt, 30);
    if (!sched_res.ok) {
        std::filesystem::remove(setup_exe, ec);
        std::filesystem::remove(helper_cmd, ec);
        return {false, false, "Failed scheduling upgrade task: " + sched_res.error.value_or(sched_res.output)};
    }

    Logger::instance().info("Scheduled Windows self-update to ", display_target);
    return {
        true,
        false,
        "UPDATE_SCHEDULED: Windows upgrade (" + current_ver + " -> " + display_target + ") queued; service restarting."
    };
#else
    (void)config;
    const std::filesystem::path bin_dest = current_linux_binary_path();
    const std::filesystem::path tmp_bin = std::filesystem::path(bin_dest.string() + ".new." + std::to_string(getpid()));

    const std::string dl_sh =
        "set -e; "
        "if command -v curl >/dev/null 2>&1; then "
        "curl -fsSL --connect-timeout 15 --max-time " + std::to_string(timeout_seconds)
        + " '" + sh_single_quote(download_url) + "' -o '" + sh_single_quote(tmp_bin.string()) + "'; "
        "elif command -v wget >/dev/null 2>&1; then "
        "wget -qO '" + sh_single_quote(tmp_bin.string()) + "' '" + sh_single_quote(download_url) + "'; "
        "else echo 'Neither curl nor wget found' >&2; exit 127; fi";
    const ProcessResult dl_res = execute_process("shell", dl_sh, std::nullopt, timeout_seconds);
    std::error_code ec;
    if (!dl_res.ok) {
        std::filesystem::remove(tmp_bin, ec);
        return {false, false, "Download failed: " + dl_res.error.value_or(dl_res.output)};
    }
    if (!check_binary_header(tmp_bin)) {
        std::filesystem::remove(tmp_bin, ec);
        return {false, false, "Downloaded Linux binary failed ELF integrity check."};
    }
    if (chmod(tmp_bin.c_str(), 0755) != 0) {
        std::filesystem::remove(tmp_bin, ec);
        return {false, false, "Failed setting executable permissions on updated binary."};
    }
    const ProcessResult ver_check = execute_process("shell", "'" + sh_single_quote(tmp_bin.string()) + "' --version", std::nullopt, 10);
    if (!ver_check.ok) {
        std::filesystem::remove(tmp_bin, ec);
        return {false, false, "Downloaded binary failed --version sanity check: " + ver_check.output};
    }
    std::filesystem::rename(tmp_bin, bin_dest, ec);
    if (ec) {
        std::filesystem::remove(tmp_bin, ec);
        return {false, false, "Failed replacing " + bin_dest.string() + ": " + ec.message()};
    }

    Logger::instance().info("Replaced Linux agent binary for update to ", display_target);
    return {
        true,
        true,
        "UPDATE_APPLIED: Replaced binary (" + current_ver + " -> " + display_target + "); restarting service."
    };
#endif
}

void trigger_post_update_restart(std::atomic<bool>& stop_signal) {
#if !defined(_WIN32)
    std::thread([&stop_signal]() {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        execute_process(
            "shell",
            "if command -v systemd-run >/dev/null 2>&1; then "
            "systemd-run --quiet --on-active=1s systemctl restart cmdiqo-agent.service 2>/dev/null || true; "
            "elif command -v systemctl >/dev/null 2>&1; then "
            "nohup sh -c 'sleep 1 && systemctl restart cmdiqo-agent.service' >/dev/null 2>&1 & fi",
            std::nullopt,
            10);
        std::this_thread::sleep_for(std::chrono::seconds(2));
        stop_signal.store(true);
    }).detach();
#else
    (void)stop_signal;
#endif
}

}  // namespace cmdiqo::agent
