#pragma once

#include <filesystem>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace cmdiqo::installer {

enum class Platform {
    windows,
    linux,
    unsupported,
};

enum class VersionRelation {
    older,
    same,
    newer,
};

enum class ServiceStatus {
    running,
    stopped,
    not_installed,
};

struct Config {
    std::string hub_url{"ws://192.168.1.10:8765"};
    std::string token;
    std::string agent_name;
    bool agent_connection_notify{false};
};

struct Endpoint {
    std::string host;
    unsigned short port{};
    bool secure{};
};

struct InstallerPaths {
    std::filesystem::path install_dir;
    std::filesystem::path binary_path;
    std::filesystem::path config_dir;
    std::filesystem::path config_file;
    std::filesystem::path legacy_env_file;
    std::filesystem::path log_dir;
    std::filesystem::path service_file;
    std::string service_name;
};

struct CommandResult {
    int exit_code{-1};
    std::string output;
    bool started{false};
};

struct OperationResult {
    bool ok{false};
    std::string message;

    static OperationResult success(std::string message);
    static OperationResult failure(std::string message);
};

using ProgressCallback = std::function<void(std::string_view)>;

Platform current_platform();
std::string platform_name(Platform platform);
std::string payload_file_name(Platform platform);
bool is_safe_zip_entry(std::string_view name);

VersionRelation compare_versions(std::string_view left, std::string_view right);
std::string version_relation_message(VersionRelation relation);
std::optional<Endpoint> parse_hub_endpoint(std::string_view hub_url);
OperationResult test_hub_tcp(std::string_view hub_url, int timeout_seconds = 3);

Config load_config(const InstallerPaths& paths);
OperationResult save_config(const InstallerPaths& paths, const Config& config);
std::string config_to_json(const Config& config);
std::optional<Config> config_from_json(std::string_view document, std::string& error);

class Logger {
public:
    explicit Logger(std::filesystem::path file_path = {});
    void info(std::string_view message) const;
    void error(std::string_view message) const;

private:
    void write(std::string_view level, std::string_view message) const;
    std::filesystem::path file_path_;
};

class PlatformOperations {
public:
    static InstallerPaths paths();
    static bool is_elevated();
    static bool has_graphical_session();
    static CommandResult run(const std::vector<std::string>& argv);
    static ServiceStatus service_status(const InstallerPaths& paths);
    static OperationResult start_service(const InstallerPaths& paths);
    static OperationResult stop_service(const InstallerPaths& paths);
    static OperationResult restart_service(const InstallerPaths& paths);
    static OperationResult register_service(const InstallerPaths& paths, const Logger& logger);
    static OperationResult unregister_service(const InstallerPaths& paths, const Logger& logger);
    static OperationResult secure_config_file(const std::filesystem::path& config_file);
    static bool relaunch_elevated(const std::vector<std::string>& arguments, const Logger& logger);
};

class InstallerService {
public:
    explicit InstallerService(InstallerPaths paths = PlatformOperations::paths());

    const InstallerPaths& paths() const noexcept;
    std::optional<std::string> installed_version() const;
    std::string bundled_version(const std::filesystem::path& payload) const;
    std::optional<std::filesystem::path> locate_payload(
        const std::optional<std::filesystem::path>& explicit_payload) const;

    OperationResult install_or_upgrade(
        const Config& config,
        const std::filesystem::path& payload,
        const ProgressCallback& progress = {});
    OperationResult uninstall(bool purge_config, const ProgressCallback& progress = {});

private:
    void report(const ProgressCallback& progress, std::string_view message) const;
    OperationResult extract_payload(const std::filesystem::path& payload, const ProgressCallback& progress);

    InstallerPaths paths_;
    Logger logger_;
};

}  // namespace cmdiqo::installer
