#include "cmdiqo/agent/updater.hpp"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("Updater: Semver parsing and comparison", "[updater]") {
    using namespace cmdiqo::agent;

    auto v1 = parse_semver("v0.36.5");
    REQUIRE(v1.has_value());
    CHECK(v1->major == 0);
    CHECK(v1->minor == 36);
    CHECK(v1->patch == 5);
    CHECK_FALSE(v1->is_dev);

    auto vdev = parse_semver("0.37.devel+6AB4BAC9");
    REQUIRE(vdev.has_value());
    CHECK(vdev->major == 0);
    CHECK(vdev->minor == 37);
    CHECK(vdev->is_dev);

    CHECK(is_newer_version("0.36.5", "v0.37.0"));
    CHECK(is_newer_version("0.37.4", "0.37.5"));
    CHECK(is_newer_version("0.37.devel+6AB4BAC9", "0.37.1"));
    CHECK_FALSE(is_newer_version("0.37.5", "0.37.5"));
    CHECK_FALSE(is_newer_version("0.38.0", "0.37.9"));
}

TEST_CASE("Updater: Asset naming and URL safety validation", "[updater]") {
    using namespace cmdiqo::agent;

    CHECK(release_asset_name("win64") == "cmdiqo-agent-windows-x64-setup.exe");
    CHECK(release_asset_name("linux64") == "cmdiqo-agent");
    CHECK(release_asset_name("arm64") == "cmdiqo-agent-linux-arm64");
    CHECK(release_asset_name("armv6") == "cmdiqo-agent-linux-armv6");

    CHECK(is_valid_update_url("https://github.com/lhotakj/cmdiqo/releases/download/v0.37.0/cmdiqo-agent"));
    CHECK(is_valid_update_url("https://api.github.com/repos/lhotakj/cmdiqo/releases/latest"));
    CHECK(is_valid_update_url("https://objects.githubusercontent.com/github-production-release-asset/123"));

    CHECK_FALSE(is_valid_update_url("http://github.com/lhotakj/cmdiqo/releases/latest"));
    CHECK_FALSE(is_valid_update_url("https://evil.example.com/payload.exe"));
    CHECK_FALSE(is_valid_update_url("https://github.com/a;rm -rf /"));
    CHECK_FALSE(is_valid_update_url("https://github.com/a'b"));
}
