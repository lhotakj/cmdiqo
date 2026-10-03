#include "cmdiqo/agent/version.hpp"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("Version: Version string retrieval", "[version]") {
    auto version = cmdiqo::agent::get_version();
    REQUIRE(!version.empty());

    auto version_str = cmdiqo::agent::get_version_string();
    REQUIRE(version_str.rfind("cmdiqo-agent ", 0) == 0);
    REQUIRE(version_str.find(version) != std::string::npos);
}
