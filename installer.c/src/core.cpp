#include "cmdiqo/installer.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cctype>
#include <ctime>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <nlohmann/json.hpp>
#include <regex>
#include <sstream>
#include <system_error>
#include <utility>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#endif

namespace cmdiqo::installer {
using json = nlohmann::json;

namespace {

struct ParsedVersion {
    std::array<unsigned long long, 3> numbers{};
    bool development{false};
    std::string suffix;
};

std::string trim(std::string value) {
    const auto first = std::find_if_not(value.begin(), value.end(), [](unsigned char character) {
        return std::isspace(character) != 0;
    });
    const auto last = std::find_if_not(value.rbegin(), value.rend(), [](unsigned char character) {
        return std::isspace(character) != 0;
    }).base();
    return first >= last ? std::string{} : std::string(first, last);
}

ParsedVersion parse_version(std::string_view value) {
    std::string normalized = trim(std::string(value));
    if (!normalized.empty() && (normalized.front() == 'v' || normalized.front() == 'V')) {
        normalized.erase(normalized.begin());
    }

    ParsedVersion result;
    std::size_t position = 0;
    for (std::size_t index = 0; index < result.numbers.size() && position < normalized.size(); ++index) {
        const std::size_t start = position;
        while (position < normalized.size()
               && std::isdigit(static_cast<unsigned char>(normalized[position])) != 0) {
            ++position;
        }
        if (start == position) {
            break;
        }
        std::from_chars(
            normalized.data() + start,
            normalized.data() + position,
            result.numbers[index]);
        if (position < normalized.size() && normalized[position] == '.') {
            ++position;
            continue;
        }
        break;
    }

    if (position < normalized.size()) {
        result.suffix = normalized.substr(position);
        std::string lower_suffix = result.suffix;
        std::transform(lower_suffix.begin(), lower_suffix.end(), lower_suffix.begin(), [](unsigned char character) {
            return static_cast<char>(std::tolower(character));
        });
        result.development = lower_suffix.find("dev") != std::string::npos
            || lower_suffix.find("alpha") != std::string::npos
            || lower_suffix.find("beta") != std::string::npos
            || lower_suffix.find("rc") != std::string::npos;
    }
    return result;
}

std::string hostname() {
#ifdef _WIN32
    char buffer[256]{};
    DWORD length = static_cast<DWORD>(sizeof(buffer));
    return GetComputerNameA(buffer, &length) != 0 ? std::string(buffer, length) : "cmdiqo-agent";
#else
    std::array<char, 256> buffer{};
    return gethostname(buffer.data(), buffer.size()) == 0 ? std::string(buffer.data()) : "cmdiqo-agent";
#endif
}

std::string env_quote(std::string_view value) {
    std::string quoted{"\""};
    for (char character : value) {
        if (character == '\\' || character == '"' || character == '$' || character == '`') {
            quoted += '\\';
        }
        quoted += character;
    }
    quoted += '"';
    return quoted;
}

std::string timestamp() {
    const auto now = std::chrono::system_clock::now();
    const auto time = std::chrono::system_clock::to_time_t(now);
    std::tm local_time{};
#ifdef _WIN32
    localtime_s(&local_time, &time);
#else
    localtime_r(&time, &local_time);
#endif
    std::ostringstream output;
    output << std::put_time(&local_time, "%Y-%m-%d %H:%M:%S");
    return output.str();
}

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

}  // namespace

OperationResult OperationResult::success(std::string message) {
    return {.ok = true, .message = std::move(message)};
}

OperationResult OperationResult::failure(std::string message) {
    return {.ok = false, .message = std::move(message)};
}

Platform current_platform() {
#ifdef _WIN32
    return Platform::windows;
#elif defined(__linux__)
    return Platform::linux;
#else
    return Platform::unsupported;
#endif
}

std::string platform_name(Platform platform) {
    std::string arch;
#if defined(_M_X64) || defined(__x86_64__)
    arch = "x64";
#elif defined(_M_ARM64) || defined(__aarch64__)
    arch = "arm64";
#elif defined(__ARM_ARCH_6__) || defined(__ARM_ARCH_6J__) || defined(__ARM_ARCH_6K__) || defined(__ARM_ARCH_6Z__) || defined(__ARM_ARCH_6ZK__)
    arch = "armv6";
#elif defined(__arm__) || defined(_M_ARM)
    arch = "arm";
#elif defined(_M_IX86) || defined(__i386__)
    arch = "x86";
#endif

    switch (platform) {
        case Platform::windows:
            return arch.empty() ? "Windows" : ("Windows " + arch);
        case Platform::linux:
            return arch.empty() ? "Linux" : ("Linux " + arch);
        case Platform::unsupported:
            return "Unsupported";
    }
    return "Unsupported";
}

std::string payload_file_name(Platform platform) {
    switch (platform) {
        case Platform::windows:
            return "cmdiqo-agent-windows-x64.zip";
        case Platform::linux:
            return "cmdiqo-agent-linux-x64.zip";
        case Platform::unsupported:
            return {};
    }
    return {};
}

bool is_safe_zip_entry(std::string_view name) {
    if (name.empty() || name.find('\0') != std::string_view::npos || name.front() == '/' || name.front() == '\\') {
        return false;
    }
    if (name.size() >= 2 && std::isalpha(static_cast<unsigned char>(name[0])) != 0 && name[1] == ':') {
        return false;
    }

    std::size_t start = 0;
    while (start <= name.size()) {
        const std::size_t end = name.find_first_of("/\\", start);
        const std::string_view component = name.substr(start, end == std::string_view::npos ? name.size() - start : end - start);
        if (component == "..") {
            return false;
        }
        if (end == std::string_view::npos) {
            break;
        }
        start = end + 1;
    }
    return true;
}

VersionRelation compare_versions(std::string_view left, std::string_view right) {
    const ParsedVersion first = parse_version(left);
    const ParsedVersion second = parse_version(right);
    if (first.numbers < second.numbers) {
        return VersionRelation::older;
    }
    if (first.numbers > second.numbers) {
        return VersionRelation::newer;
    }
    if (first.development != second.development) {
        return first.development ? VersionRelation::older : VersionRelation::newer;
    }
    if (first.suffix < second.suffix) {
        return VersionRelation::older;
    }
    if (first.suffix > second.suffix) {
        return VersionRelation::newer;
    }
    return VersionRelation::same;
}

std::string version_relation_message(VersionRelation relation) {
    switch (relation) {
        case VersionRelation::older:
            return "Installed version is older than this installer.";
        case VersionRelation::same:
            return "Installed version matches this installer. Repair is available.";
        case VersionRelation::newer:
            return "Installed version is newer than this installer. Downgrade is available.";
    }
    return {};
}

std::optional<Endpoint> parse_hub_endpoint(std::string_view hub_url) {
    static const std::regex websocket_url(R"(^\s*(ws|wss)://(\[[0-9A-Fa-f:]+\]|[^/:?#\s]+)(?::([0-9]{1,5}))?(?:[/?#].*)?\s*$)");
    std::smatch match;
    const std::string url(hub_url);
    if (!std::regex_match(url, match, websocket_url)) {
        return std::nullopt;
    }

    unsigned long port = match[3].matched
        ? std::stoul(match[3].str())
        : (match[1].str() == "wss" ? 443UL : 80UL);
    if (port == 0 || port > 65535) {
        return std::nullopt;
    }
    std::string host = match[2].str();
    if (host.size() > 1 && host.front() == '[' && host.back() == ']') {
        host = host.substr(1, host.size() - 2);
    }
    return Endpoint{
        .host = std::move(host),
        .port = static_cast<unsigned short>(port),
        .secure = match[1].str() == "wss",
    };
}

OperationResult test_hub_tcp(std::string_view hub_url, int timeout_seconds) {
    const auto endpoint = parse_hub_endpoint(hub_url);
    if (!endpoint) {
        return OperationResult::failure("Hub URL must use ws:// or wss:// and contain a valid host and port.");
    }
    if (timeout_seconds <= 0) {
        return OperationResult::failure("TCP timeout must be positive.");
    }

#ifdef _WIN32
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
        return OperationResult::failure("Could not initialize Windows sockets.");
    }
#endif

    addrinfo hints{};
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    addrinfo* addresses = nullptr;
    const std::string port = std::to_string(endpoint->port);
    const int lookup = getaddrinfo(endpoint->host.c_str(), port.c_str(), &hints, &addresses);
    if (lookup != 0) {
#ifdef _WIN32
        WSACleanup();
#endif
        return OperationResult::failure("Could not resolve Hub host " + endpoint->host + ".");
    }

    bool connected = false;
    for (addrinfo* candidate = addresses; candidate != nullptr && !connected; candidate = candidate->ai_next) {
        const auto socket_handle = socket(candidate->ai_family, candidate->ai_socktype, candidate->ai_protocol);
#ifdef _WIN32
        if (socket_handle == INVALID_SOCKET) {
            continue;
        }
        u_long non_blocking = 1;
        ioctlsocket(socket_handle, FIONBIO, &non_blocking);

        int res = connect(socket_handle, candidate->ai_addr, static_cast<int>(candidate->ai_addrlen));
        if (res == 0) {
            connected = true;
        } else if (WSAGetLastError() == WSAEWOULDBLOCK) {
            fd_set write_set;
            FD_ZERO(&write_set);
            FD_SET(socket_handle, &write_set);
            fd_set err_set;
            FD_ZERO(&err_set);
            FD_SET(socket_handle, &err_set);

            timeval tv{.tv_sec = timeout_seconds, .tv_usec = 0};
            int select_res = select(0, nullptr, &write_set, &err_set, &tv);
            if (select_res > 0 && FD_ISSET(socket_handle, &write_set)) {
                int sock_err = 0;
                int sock_err_len = sizeof(sock_err);
                if (getsockopt(socket_handle, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&sock_err), &sock_err_len) == 0 && sock_err == 0) {
                    connected = true;
                }
            }
        }
        closesocket(socket_handle);
#else
        if (socket_handle < 0) {
            continue;
        }
        int flags = fcntl(socket_handle, F_GETFL, 0);
        if (flags >= 0) {
            fcntl(socket_handle, F_SETFL, flags | O_NONBLOCK);
        }

        int res = connect(socket_handle, candidate->ai_addr, candidate->ai_addrlen);
        if (res == 0) {
            connected = true;
        } else if (errno == EINPROGRESS) {
            fd_set write_set;
            FD_ZERO(&write_set);
            FD_SET(socket_handle, &write_set);

            timeval tv{.tv_sec = timeout_seconds, .tv_usec = 0};
            int select_res = select(socket_handle + 1, nullptr, &write_set, nullptr, &tv);
            if (select_res > 0 && FD_ISSET(socket_handle, &write_set)) {
                int sock_err = 0;
                socklen_t sock_err_len = sizeof(sock_err);
                if (getsockopt(socket_handle, SOL_SOCKET, SO_ERROR, &sock_err, &sock_err_len) == 0 && sock_err == 0) {
                    connected = true;
                }
            }
        }
        close(socket_handle);
#endif
    }
    freeaddrinfo(addresses);
#ifdef _WIN32
    WSACleanup();
#endif

    if (!connected) {
        return OperationResult::failure(
            "Could not establish TCP connectivity to " + endpoint->host + ":" + std::to_string(endpoint->port) + ".");
    }
    return OperationResult::success(
        "TCP connectivity to " + endpoint->host + ":" + std::to_string(endpoint->port) + " succeeded.");
}

std::string config_to_json(const Config& config) {
    const json document{
        {"hub_url", config.hub_url},
        {"token", config.token},
        {"name", config.agent_name},
        {"reconnect_delay", 5.0},
        {"agent_connection_notify", config.agent_connection_notify},
    };
    return document.dump(2) + "\n";
}

std::optional<Config> config_from_json(std::string_view document, std::string& error) {
    try {
        const json parsed = json::parse(document);
        if (!parsed.is_object()) {
            error = "Configuration JSON must be an object.";
            return std::nullopt;
        }
        Config config;

        auto get_string = [&](const std::initializer_list<const char*>& keys) -> std::optional<std::string> {
            for (const char* key : keys) {
                if (parsed.contains(key)) {
                    if (parsed[key].is_string()) {
                        return parsed[key].get<std::string>();
                    }
                    if (parsed[key].is_number() || parsed[key].is_boolean()) {
                        return parsed[key].dump();
                    }
                }
            }
            return std::nullopt;
        };

        if (auto hub = get_string({"hub_url", "hub", "hubUrl", "HUB_URL", "url"})) {
            config.hub_url = *hub;
        }
        if (auto tok = get_string({"token", "agent_token", "AGENT_TOKEN", "TOKEN", "secret", "agentToken", "token_key"})) {
            config.token = *tok;
        }
        if (auto nm = get_string({"name", "agent_name", "agentName", "AGENT_NAME", "NAME", "hostname"})) {
            config.agent_name = *nm;
        } else {
            config.agent_name = hostname();
        }

        for (const char* key : {"agent_connection_notify", "agentConnectionNotify", "AGENT_CONNECTION_NOTIFY", "notify", "agent_notify"}) {
            if (parsed.contains(key)) {
                if (parsed[key].is_boolean()) {
                    config.agent_connection_notify = parsed[key].get<bool>();
                    break;
                }
                if (parsed[key].is_string()) {
                    const std::string s = parsed[key].get<std::string>();
                    config.agent_connection_notify = (s == "true" || s == "1" || s == "yes");
                    break;
                }
                if (parsed[key].is_number_integer()) {
                    config.agent_connection_notify = (parsed[key].get<int>() != 0);
                    break;
                }
            }
        }

        if (!config.hub_url.empty() && !parse_hub_endpoint(config.hub_url)) {
            error = "Configuration contains an invalid Hub URL.";
            return std::nullopt;
        }
        if (config.agent_name.empty()) {
            config.agent_name = hostname();
        }
        return config;
    } catch (const json::exception& exception) {
        error = "Invalid configuration JSON: " + std::string(exception.what());
        return std::nullopt;
    }
}

Config load_config(const InstallerPaths& paths) {
    Config config;
    config.agent_name = hostname();

    auto try_json = [&](const std::filesystem::path& path) -> bool {
        if (path.empty()) return false;
        std::error_code ec;
        if (!std::filesystem::is_regular_file(path, ec)) return false;
        std::ifstream file(path);
        if (!file) return false;
        std::stringstream contents;
        contents << file.rdbuf();
        std::string error;
        if (const auto parsed = config_from_json(contents.str(), error)) {
            if (!parsed->hub_url.empty()) config.hub_url = parsed->hub_url;
            if (!parsed->token.empty()) config.token = parsed->token;
            if (!parsed->agent_name.empty()) config.agent_name = parsed->agent_name;
            config.agent_connection_notify = parsed->agent_connection_notify;
            return true;
        }
        return false;
    };

    auto try_env = [&](const std::filesystem::path& path) -> bool {
        if (path.empty()) return false;
        std::error_code ec;
        if (!std::filesystem::is_regular_file(path, ec)) return false;
        std::ifstream env_file(path);
        if (!env_file) return false;
        bool found = false;
        std::string line;
        while (std::getline(env_file, line)) {
            const auto separator = line.find('=');
            if (separator == std::string::npos || line.empty() || line.front() == '#') {
                continue;
            }
            const std::string key = trim(line.substr(0, separator));
            std::string value = trim(line.substr(separator + 1));
            if (value.size() >= 2 && ((value.front() == '"' && value.back() == '"')
                                      || (value.front() == '\'' && value.back() == '\''))) {
                value = value.substr(1, value.size() - 2);
            }
            if (key == "HUB_URL" && !value.empty()) {
                config.hub_url = value;
                found = true;
            } else if ((key == "AGENT_TOKEN" || key == "TOKEN" || key == "SECRET") && !value.empty()) {
                config.token = value;
                found = true;
            } else if ((key == "AGENT_NAME" || key == "NAME") && !value.empty()) {
                config.agent_name = value;
                found = true;
            } else if (key == "AGENT_CONNECTION_NOTIFY") {
                config.agent_connection_notify = value == "true" || value == "1" || value == "yes";
                found = true;
            }
        }
        return found;
    };

    // 1. Environment variable AGENT_CONFIG
    if (const char* env_config = std::getenv("AGENT_CONFIG"); env_config != nullptr && *env_config != '\0') {
        if (try_json(env_config) && !config.token.empty()) {
            return config;
        }
    }

    // 2. Primary config file
    if (!paths.config_file.empty()) {
        if (try_json(paths.config_file) && !config.token.empty()) {
            return config;
        }
    }

    // 3. Config directory fallbacks
    if (!paths.config_dir.empty()) {
        if (try_json(paths.config_dir / "agent.json") && !config.token.empty()) return config;
        if (try_json(paths.config_dir / "cmdiqo.json") && !config.token.empty()) return config;
        if (try_env(paths.legacy_env_file) && !config.token.empty()) return config;
        if (try_env(paths.config_dir / "agent.env") && !config.token.empty()) return config;
    }

    // 4. Install directory fallbacks
    if (!paths.install_dir.empty()) {
        if (try_json(paths.install_dir / "config.json") && !config.token.empty()) return config;
        if (try_json(paths.install_dir / "agent.json") && !config.token.empty()) return config;
        if (try_env(paths.install_dir / "agent.env") && !config.token.empty()) return config;
    }

    // 5. Executable directory fallbacks
    const std::filesystem::path exe_dir = executable_directory();
    if (!exe_dir.empty()) {
        if (try_json(exe_dir / "config.json") && !config.token.empty()) return config;
        if (try_json(exe_dir / "agent.json") && !config.token.empty()) return config;
        if (try_env(exe_dir / "agent.env") && !config.token.empty()) return config;
    }

    // 6. Current working directory fallbacks
    if (try_json(std::filesystem::current_path() / "config.json") && !config.token.empty()) return config;
    if (try_json(std::filesystem::current_path() / "agent.json") && !config.token.empty()) return config;
    if (try_env(std::filesystem::current_path() / "agent.env") && !config.token.empty()) return config;

    // 7. Direct environment variables
    if (const char* val = std::getenv("AGENT_TOKEN"); val != nullptr && *val != '\0') {
        config.token = val;
    } else if (const char* val2 = std::getenv("TOKEN"); val2 != nullptr && *val2 != '\0') {
        config.token = val2;
    }
    if (const char* val = std::getenv("HUB_URL"); val != nullptr && *val != '\0') {
        config.hub_url = val;
    }
    if (const char* val = std::getenv("AGENT_NAME"); val != nullptr && *val != '\0') {
        config.agent_name = val;
    } else if (const char* val2 = std::getenv("NAME"); val2 != nullptr && *val2 != '\0') {
        config.agent_name = val2;
    }

    return config;
}

OperationResult validate_existing_config(
    const InstallerPaths& paths,
    const std::optional<std::filesystem::path>& custom_config) {
    std::vector<std::filesystem::path> candidates;
    if (custom_config && !custom_config->empty()) {
        candidates.push_back(*custom_config);
    }
    if (const char* env_config = std::getenv("AGENT_CONFIG"); env_config != nullptr && *env_config != '\0') {
        candidates.push_back(env_config);
    }
    if (!paths.config_file.empty()) {
        candidates.push_back(paths.config_file);
    }
    if (!paths.config_dir.empty()) {
        candidates.push_back(paths.config_dir / "agent.json");
        candidates.push_back(paths.config_dir / "cmdiqo.json");
        if (!paths.legacy_env_file.empty()) {
            candidates.push_back(paths.legacy_env_file);
        }
        candidates.push_back(paths.config_dir / "agent.env");
    }
    if (!paths.install_dir.empty()) {
        candidates.push_back(paths.install_dir / "config.json");
        candidates.push_back(paths.install_dir / "agent.json");
        candidates.push_back(paths.install_dir / "agent.env");
    }

    std::filesystem::path found_file;
    for (const auto& candidate : candidates) {
        std::error_code ec;
        if (std::filesystem::is_regular_file(candidate, ec)) {
            found_file = candidate;
            break;
        }
    }

    if (found_file.empty()) {
        return OperationResult::failure("Cannot upgrade: No existing configuration found. Use --install or --fresh for initial installation.");
    }

    Config config;
    if (found_file.extension() == ".env") {
        std::ifstream env_file(found_file);
        if (!env_file) {
            return OperationResult::failure("Cannot upgrade: Could not open configuration file " + found_file.string() + ".");
        }
        std::string line;
        while (std::getline(env_file, line)) {
            const auto separator = line.find('=');
            if (separator == std::string::npos || line.empty() || line.front() == '#') {
                continue;
            }
            const std::string key = trim(line.substr(0, separator));
            std::string value = trim(line.substr(separator + 1));
            if (value.size() >= 2 && ((value.front() == '"' && value.back() == '"')
                                      || (value.front() == '\'' && value.back() == '\''))) {
                value = value.substr(1, value.size() - 2);
            }
            if (key == "HUB_URL" && !value.empty()) {
                config.hub_url = value;
            } else if ((key == "AGENT_TOKEN" || key == "TOKEN" || key == "SECRET") && !value.empty()) {
                config.token = value;
            } else if ((key == "AGENT_NAME" || key == "NAME") && !value.empty()) {
                config.agent_name = value;
            } else if (key == "AGENT_CONNECTION_NOTIFY") {
                config.agent_connection_notify = value == "true" || value == "1" || value == "yes";
            }
        }
    } else {
        std::ifstream json_file(found_file);
        if (!json_file) {
            return OperationResult::failure("Cannot upgrade: Could not open configuration file " + found_file.string() + ".");
        }
        std::stringstream contents;
        contents << json_file.rdbuf();
        std::string error;
        const auto parsed = config_from_json(contents.str(), error);
        if (!parsed) {
            return OperationResult::failure("Cannot upgrade: Existing configuration file " + found_file.string() + " is invalid: " + error);
        }
        config = *parsed;
    }

    if (config.token.empty()) {
        return OperationResult::failure("Cannot upgrade: Existing configuration is missing required AGENT_TOKEN in " + found_file.string() + ".");
    }
    if (config.hub_url.empty()) {
        return OperationResult::failure("Cannot upgrade: Existing configuration is missing required HUB_URL in " + found_file.string() + ".");
    }
    if (!parse_hub_endpoint(config.hub_url)) {
        return OperationResult::failure("Cannot upgrade: Existing configuration has invalid HUB_URL '" + config.hub_url + "' in " + found_file.string() + ".");
    }
    if (config.agent_name.empty()) {
        return OperationResult::failure("Cannot upgrade: Existing configuration is missing required AGENT_NAME in " + found_file.string() + ".");
    }

    return OperationResult::success("Existing configuration is valid.");
}

OperationResult save_config(const InstallerPaths& paths, const Config& config) {
    if (!parse_hub_endpoint(config.hub_url)) {
        return OperationResult::failure("Hub URL must use ws:// or wss:// and contain a valid host and port.");
    }
    if (config.agent_name.empty()) {
        return OperationResult::failure("Agent name cannot be empty.");
    }

    std::error_code error;
    std::filesystem::create_directories(paths.config_dir, error);
    if (error) {
        return OperationResult::failure("Could not create configuration directory: " + error.message());
    }
    std::filesystem::create_directories(paths.log_dir, error);
    if (error) {
        return OperationResult::failure("Could not create log directory: " + error.message());
    }

    {
        std::ofstream output(paths.config_file, std::ios::binary | std::ios::trunc);
        if (!output) {
            return OperationResult::failure("Could not write " + paths.config_file.string() + ".");
        }
        output << config_to_json(config);
        if (!output) {
            return OperationResult::failure("Could not finish writing " + paths.config_file.string() + ".");
        }
    }

    if (current_platform() == Platform::linux) {
        std::filesystem::permissions(
            paths.config_file,
            std::filesystem::perms::owner_read | std::filesystem::perms::owner_write,
            std::filesystem::perm_options::replace,
            error);
        if (error) {
            return OperationResult::failure("Could not secure " + paths.config_file.string() + ": " + error.message());
        }
        std::ofstream output(paths.legacy_env_file, std::ios::binary | std::ios::trunc);
        if (!output) {
            return OperationResult::failure("Could not write " + paths.legacy_env_file.string() + ".");
        }
        output << "HUB_URL=" << env_quote(config.hub_url) << '\n'
               << "AGENT_TOKEN=" << env_quote(config.token) << '\n'
               << "AGENT_NAME=" << env_quote(config.agent_name) << '\n'
               << "AGENT_CONNECTION_NOTIFY=" << (config.agent_connection_notify ? "true" : "false") << '\n';
        if (!output) {
            return OperationResult::failure("Could not finish writing " + paths.legacy_env_file.string() + ".");
        }
        std::filesystem::permissions(
            paths.legacy_env_file,
            std::filesystem::perms::owner_read | std::filesystem::perms::owner_write,
            std::filesystem::perm_options::replace,
            error);
        if (error) {
            return OperationResult::failure("Could not secure " + paths.legacy_env_file.string() + ": " + error.message());
        }
    }

    if (current_platform() == Platform::windows) {
        if (const OperationResult secured = PlatformOperations::secure_config_file(paths.config_file); !secured.ok) {
            return secured;
        }
    }

    if (!paths.install_dir.empty() && paths.install_dir != paths.config_dir && std::filesystem::is_directory(paths.install_dir)) {
        std::error_code ec;
        const std::filesystem::path target = paths.install_dir / "config.json";
        std::filesystem::copy_file(paths.config_file, target, std::filesystem::copy_options::overwrite_existing, ec);
        if (!ec && current_platform() == Platform::windows) {
            PlatformOperations::secure_config_file(target);
        }
    }

    return OperationResult::success("Saved agent configuration.");
}

Config prompt_config(const Config& initial, std::istream& in, std::ostream& out) {
    Config config = initial;
    std::string line;

    out << "Enter Hub WebSocket URL [Default: " << config.hub_url << "]: " << std::flush;
    if (std::getline(in, line)) {
        const std::string trimmed = trim(line);
        if (!trimmed.empty()) {
            config.hub_url = trimmed;
        }
    }

    if (!config.token.empty()) {
        out << "Enter AGENT_TOKEN shared secret [Default: <keep existing>]: " << std::flush;
    } else {
        out << "Enter AGENT_TOKEN shared secret: " << std::flush;
    }
    if (std::getline(in, line)) {
        const std::string trimmed = trim(line);
        if (!trimmed.empty()) {
            config.token = trimmed;
        }
    }

    out << "Enter agent name [Default: " << config.agent_name << "]: " << std::flush;
    if (std::getline(in, line)) {
        const std::string trimmed = trim(line);
        if (!trimmed.empty()) {
            config.agent_name = trimmed;
        }
    }

    const std::string default_notify = config.agent_connection_notify ? "yes" : "no";
    out << "Enable Slack notification on agent connect/disconnect? (yes/no) [Default: "
        << default_notify << "]: " << std::flush;
    if (std::getline(in, line)) {
        const std::string trimmed = trim(line);
        if (!trimmed.empty()) {
            std::string lower = trimmed;
            std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) {
                return static_cast<char>(std::tolower(c));
            });
            config.agent_connection_notify = (lower == "yes" || lower == "y" || lower == "true" || lower == "1");
        }
    }

    return config;
}

Logger::Logger(std::filesystem::path file_path) : file_path_(std::move(file_path)) {}

void Logger::info(std::string_view message) const {
    write("INFO", message);
}

void Logger::error(std::string_view message) const {
    write("ERROR", message);
}

void Logger::write(std::string_view level, std::string_view message) const {
    const std::string line = timestamp() + " [" + std::string(level) + "] " + std::string(message);
    std::clog << line << '\n';
    if (file_path_.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::create_directories(file_path_.parent_path(), error);
    if (error) {
        return;
    }
    std::ofstream output(file_path_, std::ios::app);
    if (output) {
        output << line << '\n';
    }
}

}  // namespace cmdiqo::installer
