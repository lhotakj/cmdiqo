#include "cmdiqo/agent/protocol.hpp"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("Protocol: HelloMessage serialization", "[protocol]") {
    cmdiqo::agent::HelloMessage hello{
        .name = "node1",
        .version = "1.0.0",
        .platform = "Linux/6.8",
        .agent_connection_notify = true
    };

    auto j = hello.to_json();
    REQUIRE(j["type"] == "hello");
    REQUIRE(j["name"] == "node1");
    REQUIRE(j["version"] == "1.0.0");
    REQUIRE(j["platform"] == "Linux/6.8");
    REQUIRE(j["agent_connection_notify"] == true);

    hello.agent_connection_notify = false;
    hello.arch = "x86_64";
    auto j_with_arch = hello.to_json();
    REQUIRE(j_with_arch["agent_connection_notify"] == false);
    REQUIRE(j_with_arch["arch"] == "x86_64");
}

TEST_CASE("Protocol: ExecRequest deserialization valid", "[protocol]") {
    nlohmann::json j = {
        {"id", "req-123"},
        {"type", "exec"},
        {"kind", "ps1"},
        {"cmd", "Get-Process"},
        {"cwd", "C:\\Temp"},
        {"timeout", 30}
    };

    auto req = cmdiqo::agent::ExecRequest::from_json(j);
    REQUIRE(req.has_value());
    REQUIRE(req->id == "req-123");
    REQUIRE(req->type == "exec");
    REQUIRE(req->kind == "ps1");
    REQUIRE(req->cmd == "Get-Process");
    REQUIRE(req->cwd.has_value());
    REQUIRE(*req->cwd == "C:\\Temp");
    REQUIRE(req->timeout == 30);
}

TEST_CASE("Protocol: ExecRequest deserialization defaults and edge cases", "[protocol]") {
    // Missing kind defaults to "shell", missing timeout defaults to 120
    nlohmann::json j_minimal = {
        {"id", "req-minimal"},
        {"type", "exec"},
        {"cmd", "echo hello"}
    };

    auto req = cmdiqo::agent::ExecRequest::from_json(j_minimal);
    REQUIRE(req.has_value());
    REQUIRE(req->id == "req-minimal");
    REQUIRE(req->kind == "shell");
    REQUIRE(req->cmd == "echo hello");
    REQUIRE(req->cwd == std::nullopt);
    REQUIRE(req->timeout == 120);

    // Negative timeout clamped to at least 1
    nlohmann::json j_neg_timeout = {
        {"id", "req-neg"},
        {"type", "exec"},
        {"cmd", "test"},
        {"timeout", -5}
    };
    auto req_neg = cmdiqo::agent::ExecRequest::from_json(j_neg_timeout);
    REQUIRE(req_neg.has_value());
    REQUIRE(req_neg->timeout >= 1);

    // Invalid JSON forms return nullopt
    REQUIRE(!cmdiqo::agent::ExecRequest::from_json(nlohmann::json::array()).has_value());
    REQUIRE(!cmdiqo::agent::ExecRequest::from_json(nlohmann::json("string_not_obj")).has_value());
    REQUIRE(!cmdiqo::agent::ExecRequest::from_json(nlohmann::json{{"type", "not_exec"}}).has_value());
}

TEST_CASE("Protocol: ResultResponse serialization success and error states", "[protocol]") {
    // 1. Success response
    cmdiqo::agent::ResultResponse resp_ok{
        .id = "req-123",
        .ok = true,
        .code = 0,
        .output = "test output\n",
        .error = std::nullopt
    };

    auto j_ok = resp_ok.to_json();
    REQUIRE(j_ok["id"] == "req-123");
    REQUIRE(j_ok["type"] == "result");
    REQUIRE(j_ok["ok"] == true);
    REQUIRE(j_ok["code"] == 0);
    REQUIRE(j_ok["output"] == "test output\n");
    REQUIRE(j_ok["error"].is_null());

    // 2. Failure response with error code and description
    cmdiqo::agent::ResultResponse resp_err{
        .id = "req-456",
        .ok = false,
        .code = 127,
        .output = "",
        .error = "executable not found"
    };

    auto j_err = resp_err.to_json();
    REQUIRE(j_err["id"] == "req-456");
    REQUIRE(j_err["ok"] == false);
    REQUIRE(j_err["code"] == 127);
    REQUIRE(j_err["error"] == "executable not found");

    // 3. Timeout response (code is null/optional)
    cmdiqo::agent::ResultResponse resp_timeout{
        .id = "req-789",
        .ok = false,
        .code = std::nullopt,
        .output = "",
        .error = "timed out after 10s"
    };

    auto j_timeout = resp_timeout.to_json();
    REQUIRE(j_timeout["id"] == "req-789");
    REQUIRE(j_timeout["ok"] == false);
    REQUIRE(j_timeout["code"].is_null());
    REQUIRE(j_timeout["error"] == "timed out after 10s");
}
