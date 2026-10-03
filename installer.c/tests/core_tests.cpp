#include "cmdiqo/installer.hpp"

#include <catch2/catch_test_macros.hpp>
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
