#include "cmdiqo/installer.hpp"

#include <catch2/catch_test_macros.hpp>
#include <fstream>
#include <sstream>

using namespace cmdiqo::installer;

TEST_CASE("semantic versions support v prefixes and development builds") {
    CHECK(compare_versions("v1.2.3", "1.2.3") == VersionRelation::same);
    CHECK(compare_versions("1.2.3-dev42", "1.2.3") == VersionRelation::older);
    CHECK(compare_versions("1.10.0", "1.9.9") == VersionRelation::newer);
    CHECK(compare_versions("0.9.devel+ABC", "0.9.0") == VersionRelation::older);
    CHECK(compare_versions("0.9.a1b2c3d4", "0.9.a1b2c3d4") == VersionRelation::same);
}

TEST_CASE("ZIP entry validation rejects traversal and absolute names") {
    CHECK(is_safe_zip_entry("cmdiqo-agent"));
    CHECK(is_safe_zip_entry("nested/version.txt"));
    CHECK_FALSE(is_safe_zip_entry("../cmdiqo-agent"));
    CHECK_FALSE(is_safe_zip_entry("nested/../../cmdiqo-agent"));
    CHECK_FALSE(is_safe_zip_entry("/etc/cmdiqo-agent/config.json"));
    CHECK_FALSE(is_safe_zip_entry(R"(C:\Windows\System32\file)"));
}

TEST_CASE("platform payload names are exact") {
    CHECK(payload_file_name(Platform::windows) == "cmdiqo-agent-windows-x64.zip");
    CHECK(payload_file_name(Platform::linux) == "cmdiqo-agent-linux-x64.zip");
    CHECK(payload_file_name(Platform::unsupported).empty());
}

TEST_CASE("configuration JSON preserves agent settings") {
    const Config original{
        .hub_url = "wss://hub.example.test:9443/ws",
        .token = "test-token",
        .agent_name = "qa-agent",
        .agent_connection_notify = true,
    };
    std::string error;
    const auto parsed = config_from_json(config_to_json(original), error);

    REQUIRE(parsed);
    CHECK(error.empty());
    CHECK(parsed->hub_url == original.hub_url);
    CHECK(parsed->token == original.token);
    CHECK(parsed->agent_name == original.agent_name);
    CHECK(parsed->agent_connection_notify);
}

TEST_CASE("configuration JSON supports alternate token and name keys") {
    std::string error;
    const auto parsed1 = config_from_json(R"({
        "hub_url": "ws://192.168.1.50:8765",
        "agent_token": "secret-token-123",
        "agent_name": "worker-node-1"
    })", error);
    REQUIRE(parsed1);
    CHECK(parsed1->token == "secret-token-123");
    CHECK(parsed1->agent_name == "worker-node-1");

    const auto parsed2 = config_from_json(R"({
        "hub": "wss://hub.remote.io:443",
        "AGENT_TOKEN": "caps-token-456",
        "notify": true
    })", error);
    REQUIRE(parsed2);
    CHECK(parsed2->token == "caps-token-456");
    CHECK(parsed2->agent_connection_notify);

    const auto parsed3 = config_from_json(R"({
        "secret": "secret-key-789"
    })", error);
    REQUIRE(parsed3);
    CHECK(parsed3->token == "secret-key-789");
}

TEST_CASE("Hub endpoint parsing supplies websocket defaults") {
    const auto plain = parse_hub_endpoint("ws://hub.example.test");
    const auto secure = parse_hub_endpoint("wss://[2001:db8::1]/socket");

    REQUIRE(plain);
    CHECK(plain->host == "hub.example.test");
    CHECK(plain->port == 80);
    CHECK_FALSE(plain->secure);
    REQUIRE(secure);
    CHECK(secure->host == "2001:db8::1");
    CHECK(secure->port == 443);
    CHECK(secure->secure);
    CHECK_FALSE(parse_hub_endpoint("https://hub.example.test"));
    CHECK_FALSE(parse_hub_endpoint("ws://hub.example.test:99999"));
}

TEST_CASE("prompt_config preserves default values on empty input") {
    const Config initial{
        .hub_url = "ws://10.0.0.1:8765",
        .token = "existing-secret",
        .agent_name = "default-node",
        .agent_connection_notify = false,
    };
    std::istringstream in("\n\n\n\n");
    std::ostringstream out;

    const Config result = prompt_config(initial, in, out);
    CHECK(result.hub_url == initial.hub_url);
    CHECK(result.token == initial.token);
    CHECK(result.agent_name == initial.agent_name);
    CHECK(result.agent_connection_notify == initial.agent_connection_notify);
    CHECK(out.str().find("ws://10.0.0.1:8765") != std::string::npos);
    CHECK(out.str().find("<keep existing>") != std::string::npos);
}

TEST_CASE("prompt_config updates all fields when user provides input") {
    const Config initial{
        .hub_url = "ws://192.168.1.10:8765",
        .token = "",
        .agent_name = "old-name",
        .agent_connection_notify = false,
    };
    std::istringstream in("wss://hub.production.net:443\nsuper-secret-token\nprod-agent-01\nyes\n");
    std::ostringstream out;

    const Config result = prompt_config(initial, in, out);
    CHECK(result.hub_url == "wss://hub.production.net:443");
    CHECK(result.token == "super-secret-token");
    CHECK(result.agent_name == "prod-agent-01");
    CHECK(result.agent_connection_notify);
}

TEST_CASE("prompt_config handles notification boolean parsing") {
    const Config initial{
        .hub_url = "ws://127.0.0.1:8765",
        .token = "tok",
        .agent_name = "agent",
        .agent_connection_notify = true,
    };
    {
        std::istringstream in("\n\n\nno\n");
        std::ostringstream out;
        const Config res = prompt_config(initial, in, out);
        CHECK_FALSE(res.agent_connection_notify);
    }
    {
        std::istringstream in("\n\n\n1\n");
        std::ostringstream out;
        const Config res = prompt_config(initial, in, out);
        CHECK(res.agent_connection_notify);
    }
}

TEST_CASE("PlatformOperations paths provides valid config paths") {
    const InstallerPaths paths = PlatformOperations::paths();
    CHECK_FALSE(paths.config_file.empty());
    CHECK_FALSE(paths.config_dir.empty());
    CHECK(paths.config_file.filename() == "config.json");
}

TEST_CASE("PlatformOperations run_elevated returns result for empty or invalid command when elevated or not") {
    // If elevated, running with empty arguments should fail gracefully
    if (PlatformOperations::is_elevated()) {
        const auto res = PlatformOperations::run_elevated({});
        CHECK_FALSE(res.ok);
    }
}

TEST_CASE("InstallerService bundled_version returns default or embedded version") {
    InstallerService service;
    const std::string version = service.bundled_version();
    CHECK_FALSE(version.empty());
}

TEST_CASE("InstallerService install_or_upgrade fails when not elevated") {
    if (!PlatformOperations::is_elevated()) {
        InstallerService service;
        const Config config;
        const auto res = service.install_or_upgrade(config);
        CHECK_FALSE(res.ok);
        CHECK(res.message.find("privileges") != std::string::npos);
    }
}

TEST_CASE("validate_existing_config fails when no configuration exists") {
    InstallerPaths paths;
    paths.config_file = std::filesystem::temp_directory_path() / "nonexistent_cmdiqo_config.json";
    paths.config_dir = std::filesystem::temp_directory_path() / "nonexistent_dir";
    paths.legacy_env_file = std::filesystem::temp_directory_path() / "nonexistent_legacy.env";
    paths.install_dir = std::filesystem::temp_directory_path() / "nonexistent_install";

    const auto res = validate_existing_config(paths);
    CHECK_FALSE(res.ok);
    CHECK(res.message.find("No existing configuration found") != std::string::npos);
}

TEST_CASE("validate_existing_config validates token and hub_url") {
    const auto temp_dir = std::filesystem::temp_directory_path() / "cmdiqo_test_cfg";
    std::filesystem::create_directories(temp_dir);

    InstallerPaths paths;
    paths.config_file = temp_dir / "config.json";
    paths.config_dir = temp_dir;

    // 1. Missing token
    {
        std::ofstream out(paths.config_file);
        out << R"({"hub_url": "ws://127.0.0.1:8765", "token": "", "name": "test-agent"})";
    }
    auto res = validate_existing_config(paths);
    CHECK_FALSE(res.ok);
    CHECK(res.message.find("AGENT_TOKEN") != std::string::npos);

    // 2. Invalid hub URL
    {
        std::ofstream out(paths.config_file);
        out << R"({"hub_url": "invalid://not-ws", "token": "tok123", "name": "test-agent"})";
    }
    res = validate_existing_config(paths);
    CHECK_FALSE(res.ok);
    CHECK((res.message.find("Hub URL") != std::string::npos || res.message.find("HUB_URL") != std::string::npos));

    // 3. Valid JSON config
    {
        std::ofstream out(paths.config_file);
        out << R"({"hub_url": "wss://hub.example.com:443", "token": "tok123", "name": "test-agent"})";
    }
    res = validate_existing_config(paths);
    CHECK(res.ok);

    // 4. Valid .env config
    paths.config_file = temp_dir / "nonexistent.json";
    paths.legacy_env_file = temp_dir / "agent.env";
    {
        std::ofstream out(paths.legacy_env_file);
        out << "HUB_URL=\"ws://10.0.0.1:8765\"\nAGENT_TOKEN=\"secret-tok\"\nAGENT_NAME=\"env-agent\"\n";
    }
    res = validate_existing_config(paths);
    CHECK(res.ok);

    std::error_code ec;
    std::filesystem::remove_all(temp_dir, ec);
}

TEST_CASE("InstallerService upgrade fails when not elevated or no installation in place") {
    InstallerPaths paths;
    paths.binary_path = std::filesystem::temp_directory_path() / "nonexistent_binary.exe";
    paths.install_dir = std::filesystem::temp_directory_path() / "nonexistent_install";
    paths.config_file = std::filesystem::temp_directory_path() / "nonexistent_cfg.json";

    InstallerService service(paths);
    const auto res = service.upgrade();
    CHECK_FALSE(res.ok);
    if (!PlatformOperations::is_elevated()) {
        CHECK(res.message.find("privileges") != std::string::npos);
    } else {
        CHECK(res.message.find("No existing installation found") != std::string::npos);
    }
}

TEST_CASE("platform_name includes OS platform and architecture") {
    const std::string name = platform_name(current_platform());
    CHECK_FALSE(name.empty());
#if defined(_WIN32)
    CHECK(name.find("Windows") != std::string::npos);
#elif defined(__linux__)
    CHECK(name.find("Linux") != std::string::npos);
#endif
}
