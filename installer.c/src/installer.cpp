#include "cmdiqo/installer.hpp"

#include <array>
#include <chrono>
#include <fstream>
#include <regex>
#include <system_error>
#include <thread>
#include <utility>

#include <zip.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace cmdiqo::installer {
namespace {

#ifndef CMDIQO_INSTALLER_VERSION
#define CMDIQO_INSTALLER_VERSION "unknown"
#endif

std::filesystem::path executable_directory() {
#ifdef _WIN32
    std::array<char, 32768> executable{};
    const DWORD length = GetModuleFileNameA(nullptr, executable.data(), static_cast<DWORD>(executable.size()));
    return length == 0 ? std::filesystem::current_path() : std::filesystem::path(std::string(executable.data(), length)).parent_path();
#elif defined(__linux__)
    std::array<char, 4096> executable{};
    const ssize_t length = readlink("/proc/self/exe", executable.data(), executable.size() - 1);
    return length <= 0 ? std::filesystem::current_path() : std::filesystem::path(std::string(executable.data(), static_cast<std::size_t>(length))).parent_path();
#else
    return std::filesystem::current_path();
#endif
}

bool copy_zip_entry(zip_t* archive, zip_uint64_t index, const std::filesystem::path& destination, std::string& error) {
    zip_file_t* entry = zip_fopen_index(archive, index, 0);
    if (entry == nullptr) {
        error = zip_strerror(archive);
        return false;
    }
    std::ofstream output(destination, std::ios::binary | std::ios::trunc);
    if (!output) {
        zip_fclose(entry);
#ifdef _WIN32
        const DWORD windows_error = GetLastError();
        error = "Could not open " + destination.string() + " for writing (Windows error "
            + std::to_string(windows_error) + ").";
#else
        error = "Could not open " + destination.string() + " for writing.";
#endif
        return false;
    }

    std::array<char, 16 * 1024> buffer{};
    zip_int64_t count = 0;
    while ((count = zip_fread(entry, buffer.data(), buffer.size())) > 0) {
        output.write(buffer.data(), count);
        if (!output) {
            zip_fclose(entry);
            error = "Could not write " + destination.string() + ".";
            return false;
        }
    }
    if (count < 0) {
        error = zip_file_strerror(entry);
        zip_fclose(entry);
        return false;
    }
    if (zip_fclose(entry) != 0) {
        error = "Could not close extracted entry " + destination.string() + ".";
        return false;
    }
    return true;
}

#ifdef _WIN32
OperationResult wait_for_file_release(const std::filesystem::path& path) {
    constexpr int max_wait_seconds = 30;
    for (int attempt = 0; attempt <= max_wait_seconds; ++attempt) {
        HANDLE handle = CreateFileW(
            path.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            0,
            nullptr,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            CloseHandle(handle);
            return OperationResult::success("Existing agent executable is no longer in use.");
        }

        const DWORD windows_error = GetLastError();
        if (windows_error == ERROR_FILE_NOT_FOUND || windows_error == ERROR_PATH_NOT_FOUND) {
            return OperationResult::success("Existing agent executable is no longer present.");
        }
        if (attempt < max_wait_seconds) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }
        return OperationResult::failure(
            "Agent executable is still locked after waiting 30 seconds for the service to stop (Windows error "
            + std::to_string(windows_error) + ").");
    }
    return OperationResult::failure("Agent executable release check did not complete.");
}
#endif

std::optional<std::string> read_zip_text_entry(zip_t* archive, const char* name) {
    zip_stat_t information{};
    if (zip_stat(archive, name, 0, &information) != 0 || information.size > 4096) {
        return std::nullopt;
    }
    zip_file_t* entry = zip_fopen(archive, name, 0);
    if (entry == nullptr) {
        return std::nullopt;
    }
    std::string value(static_cast<std::size_t>(information.size), '\0');
    const zip_int64_t count = zip_fread(entry, value.data(), value.size());
    zip_fclose(entry);
    if (count < 0) {
        return std::nullopt;
    }
    value.resize(static_cast<std::size_t>(count));
    const std::smatch match = [] (const std::string& source) {
        std::smatch result;
        const std::regex expression(R"((\d+\.\d+(?:\.\d+)?(?:[-.+][A-Za-z0-9]+)*))");
        std::regex_search(source, result, expression);
        return result;
    }(value);
    return match.empty() ? std::nullopt : std::optional<std::string>(match.str(1));
}

bool is_extractable_entry(Platform platform, std::string_view entry) {
    if (platform == Platform::windows) {
        return entry == "cmdiqo-agent.exe" || entry == "version.txt" || entry == "cmdiqo.ico";
    }
    return entry == "cmdiqo-agent" || entry == "version.txt" || entry == "cmdiqo-agent.service";
}

}  // namespace

InstallerService::InstallerService(InstallerPaths paths)
    : paths_(std::move(paths)), logger_(paths_.log_dir / "installer.log") {}

const InstallerPaths& InstallerService::paths() const noexcept {
    return paths_;
}

std::optional<std::string> InstallerService::installed_version() const {
    const std::filesystem::path version_file = paths_.install_dir / "version.txt";
    std::ifstream input(version_file);
    std::string line;
    if (input && std::getline(input, line)) {
        static const std::regex version(R"((\d+\.\d+(?:\.\d+)?(?:[-.+][A-Za-z0-9]+)*))");
        std::smatch match;
        if (std::regex_search(line, match, version)) {
            return match.str(1);
        }
    }
    if (!std::filesystem::exists(paths_.binary_path)) {
        return std::nullopt;
    }
    const CommandResult result = PlatformOperations::run({paths_.binary_path.string(), "--version"});
    static const std::regex version(R"((\d+\.\d+(?:\.\d+)?(?:[-.]?[A-Za-z0-9]+)?))");
    std::smatch match;
    return result.exit_code == 0 && std::regex_search(result.output, match, version)
        ? std::optional<std::string>(match.str(1))
        : std::nullopt;
}

std::string InstallerService::bundled_version(const std::filesystem::path& payload) const {
    int error = 0;
    zip_t* archive = zip_open(payload.string().c_str(), ZIP_RDONLY, &error);
    if (archive == nullptr) {
        return CMDIQO_INSTALLER_VERSION;
    }
    const auto version = read_zip_text_entry(archive, "version.txt");
    zip_close(archive);
    return version.value_or(CMDIQO_INSTALLER_VERSION);
}

std::optional<std::filesystem::path> InstallerService::locate_payload(
    const std::optional<std::filesystem::path>& explicit_payload) const {
    const std::string payload_name = payload_file_name(current_platform());
    if (payload_name.empty()) {
        return std::nullopt;
    }
    if (explicit_payload) {
        return explicit_payload->filename() == payload_name && std::filesystem::is_regular_file(*explicit_payload)
            ? explicit_payload
            : std::nullopt;
    }

    const std::filesystem::path directory = executable_directory();
    for (const auto& candidate : {
             directory / payload_name,
             directory / "payloads" / payload_name,
             std::filesystem::current_path() / payload_name,
         }) {
        if (std::filesystem::is_regular_file(candidate)) {
            return candidate;
        }
    }
    return std::nullopt;
}

void InstallerService::report(const ProgressCallback& progress, std::string_view message) const {
    logger_.info(message);
    if (progress) {
        progress(message);
    }
}

OperationResult InstallerService::extract_payload(
    const std::filesystem::path& payload,
    const ProgressCallback& progress) {
    const Platform platform = current_platform();
    const std::string expected_payload = payload_file_name(platform);
    if (payload.filename() != expected_payload) {
        return OperationResult::failure("Refusing payload with unexpected name. Expected " + expected_payload + ".");
    }
    int error = 0;
    zip_t* archive = zip_open(payload.string().c_str(), ZIP_RDONLY, &error);
    if (archive == nullptr) {
        zip_error_t zip_error;
        zip_error_init_with_code(&zip_error, error);
        const std::string detail = zip_error_strerror(&zip_error);
        zip_error_fini(&zip_error);
        return OperationResult::failure("Could not open payload: " + detail);
    }

    bool has_binary = false;
    const zip_int64_t entries = zip_get_num_entries(archive, 0);
    if (entries < 0) {
        const std::string detail = zip_strerror(archive);
        zip_close(archive);
        return OperationResult::failure("Could not enumerate payload entries: " + detail);
    }
    for (zip_uint64_t index = 0; index < static_cast<zip_uint64_t>(entries); ++index) {
        const char* entry = zip_get_name(archive, index, 0);
        if (entry == nullptr || !is_safe_zip_entry(entry)) {
            zip_close(archive);
            return OperationResult::failure("Payload contains an unsafe ZIP entry and was rejected.");
        }
    }

    std::error_code filesystem_error;
    std::filesystem::create_directories(paths_.install_dir, filesystem_error);
    if (filesystem_error) {
        zip_close(archive);
        return OperationResult::failure("Could not create installation directory: " + filesystem_error.message());
    }

    for (zip_uint64_t index = 0; index < static_cast<zip_uint64_t>(entries); ++index) {
        const char* raw_entry = zip_get_name(archive, index, 0);
        const std::string entry(raw_entry == nullptr ? "" : raw_entry);
        if (!is_extractable_entry(platform, entry)) {
            continue;
        }
        std::filesystem::path target;
        if (entry == "cmdiqo-agent" || entry == "cmdiqo-agent.exe") {
            target = paths_.binary_path;
            has_binary = true;
        } else if (entry == "version.txt") {
            target = paths_.install_dir / "version.txt";
        } else if (entry == "cmdiqo-agent.service") {
            target = paths_.service_file;
        } else {
            target = paths_.install_dir / entry;
        }
        std::filesystem::create_directories(target.parent_path(), filesystem_error);
        if (filesystem_error) {
            zip_close(archive);
            return OperationResult::failure("Could not create extraction directory: " + filesystem_error.message());
        }
        std::string copy_error;
        report(progress, "Extracting " + entry + ".");
        if (!copy_zip_entry(archive, index, target, copy_error)) {
            zip_close(archive);
            return OperationResult::failure("Could not extract " + entry + ": " + copy_error);
        }
    }
    zip_close(archive);
    if (!has_binary || !std::filesystem::is_regular_file(paths_.binary_path)) {
        return OperationResult::failure("Payload does not contain the expected agent executable.");
    }

    if (platform == Platform::linux) {
        std::filesystem::permissions(
            paths_.binary_path,
            std::filesystem::perms::owner_read | std::filesystem::perms::owner_write | std::filesystem::perms::owner_exec
                | std::filesystem::perms::group_read | std::filesystem::perms::group_exec
                | std::filesystem::perms::others_read | std::filesystem::perms::others_exec,
            std::filesystem::perm_options::replace,
            filesystem_error);
        if (filesystem_error) {
            return OperationResult::failure("Could not mark agent executable: " + filesystem_error.message());
        }
    }
    return OperationResult::success("Extracted platform payload.");
}

OperationResult InstallerService::install_or_upgrade(
    const Config& config,
    const std::filesystem::path& payload,
    const ProgressCallback& progress) {
    if (!PlatformOperations::is_elevated()) {
        return OperationResult::failure("Installation requires Administrator/root privileges.");
    }
    report(progress, "Installer elevation check passed.");
    report(progress, "Saving configuration.");
    if (const OperationResult saved = save_config(paths_, config); !saved.ok) {
        logger_.error(saved.message);
        return saved;
    }
    if (PlatformOperations::service_status(paths_) == ServiceStatus::running) {
        report(progress, "Stopping currently running service.");
        if (const OperationResult stopped = PlatformOperations::stop_service(paths_); !stopped.ok) {
            logger_.error(stopped.message);
            return stopped;
        }
        report(progress, "Service stop confirmed.");
    }
#ifdef _WIN32
    report(progress, "Waiting for the agent executable to be released.");
    const OperationResult released = wait_for_file_release(paths_.binary_path);
    if (!released.ok) {
        logger_.error(released.message);
        return released;
    }
    report(progress, released.message);
#endif
    report(progress, "Validating and extracting " + payload.filename().string() + ".");
    if (const OperationResult extracted = extract_payload(payload, progress); !extracted.ok) {
        logger_.error(extracted.message);
        return extracted;
    }
    report(progress, "Registering system service.");
    if (const OperationResult registered = PlatformOperations::register_service(paths_, logger_); !registered.ok) {
        logger_.error(registered.message);
        return registered;
    }
    report(progress, "Starting system service.");
    if (const OperationResult started = PlatformOperations::start_service(paths_); !started.ok) {
        logger_.error(started.message);
        return started;
    }
    report(progress, "Installation completed.");
    return OperationResult::success("Cmdiqo Agent was installed and started.");
}

OperationResult InstallerService::uninstall(bool purge_config, const ProgressCallback& progress) {
    if (!PlatformOperations::is_elevated()) {
        return OperationResult::failure("Uninstall requires Administrator/root privileges.");
    }
    if (PlatformOperations::service_status(paths_) == ServiceStatus::running) {
        report(progress, "Stopping system service.");
        if (const OperationResult stopped = PlatformOperations::stop_service(paths_); !stopped.ok) {
            logger_.error(stopped.message);
            return stopped;
        }
    }
    report(progress, "Removing system service.");
    if (const OperationResult removed = PlatformOperations::unregister_service(paths_, logger_); !removed.ok) {
        logger_.error(removed.message);
        return removed;
    }

    std::error_code error;
    std::filesystem::remove(paths_.binary_path, error);
    if (error) {
        return OperationResult::failure("Could not remove agent executable: " + error.message());
    }
    std::filesystem::remove(paths_.install_dir / "version.txt", error);
    if (error) {
        return OperationResult::failure("Could not remove agent version file: " + error.message());
    }
    if (purge_config) {
        report(progress, "Purging configuration and logs.");
        std::filesystem::remove_all(paths_.config_dir, error);
        if (error) {
            return OperationResult::failure("Could not purge configuration: " + error.message());
        }
        if (paths_.log_dir != paths_.config_dir) {
            std::filesystem::remove_all(paths_.log_dir, error);
            if (error) {
                return OperationResult::failure("Could not purge logs: " + error.message());
            }
        }
        return OperationResult::success("Cmdiqo Agent and its configuration were removed.");
    }
    report(progress, "Uninstall completed.");
    return OperationResult::success("Cmdiqo Agent was removed; configuration was retained.");
}

}  // namespace cmdiqo::installer
