#include "cmdiqo/installer.hpp"

#include <catch2/catch_test_macros.hpp>

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
