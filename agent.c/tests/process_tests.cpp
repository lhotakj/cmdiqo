#include "cmdiqo/agent/process.hpp"

#include <catch2/catch_test_macros.hpp>
#include <filesystem>

TEST_CASE("Process: build_argv mapping", "[process]") {
    auto ps_argv = cmdiqo::agent::build_argv("ps1", "Get-Process");
    REQUIRE(!ps_argv.empty());
#if defined(_WIN32)
    REQUIRE(ps_argv[0] == "powershell.exe");
#else
    REQUIRE(ps_argv[0] == "pwsh");
#endif

    auto sh_argv = cmdiqo::agent::build_argv("shell", "uptime");
    REQUIRE(!sh_argv.empty());
#if defined(_WIN32)
    REQUIRE(sh_argv[0] == "cmd.exe");
#else
    REQUIRE(sh_argv[0] == "/bin/bash");
#endif
}

TEST_CASE("Process: Simple echo execution", "[process]") {
    auto res = cmdiqo::agent::execute_process("shell", "echo test_output_123", std::nullopt, 10);
    REQUIRE(res.ok == true);
    REQUIRE(res.returncode == 0);
    REQUIRE(res.output.find("test_output_123") != std::string::npos);
    REQUIRE(res.error == std::nullopt);
}

TEST_CASE("Process: Non-zero exit code execution", "[process]") {
#if defined(_WIN32)
    auto res = cmdiqo::agent::execute_process("shell", "exit 42", std::nullopt, 10);
#else
    auto res = cmdiqo::agent::execute_process("shell", "exit 42", std::nullopt, 10);
#endif
    REQUIRE(res.ok == false);
    REQUIRE(res.returncode == 42);
}

TEST_CASE("Process: Custom working directory execution", "[process]") {
    std::filesystem::path temp_dir = std::filesystem::temp_directory_path();
#if defined(_WIN32)
    auto res = cmdiqo::agent::execute_process("shell", "cd", temp_dir.string(), 10);
#else
    auto res = cmdiqo::agent::execute_process("shell", "pwd", temp_dir.string(), 10);
#endif
    REQUIRE(res.ok == true);
    REQUIRE(res.returncode == 0);
    REQUIRE(!res.output.empty());
}

TEST_CASE("Process: Timeout watchdog cancellation", "[process]") {
#if defined(_WIN32)
    // ping -n 5 127.0.0.1 takes ~4 seconds; with 1s timeout it must cancel
    auto res = cmdiqo::agent::execute_process("shell", "ping -n 5 127.0.0.1 >nul", std::nullopt, 1);
#else
    auto res = cmdiqo::agent::execute_process("shell", "sleep 5", std::nullopt, 1);
#endif
    REQUIRE(res.ok == false);
    REQUIRE(res.returncode == -1);
    REQUIRE(res.error.has_value());
    REQUIRE(res.error->find("timed out") != std::string::npos);
}

#if defined(_WIN32)
TEST_CASE("Process: PowerShell execution with quotes", "[process]") {
    auto res = cmdiqo::agent::execute_process("ps1", "Write-Output \"test_quoted_output_456\"", std::nullopt, 10);
    REQUIRE(res.ok == true);
    REQUIRE(res.returncode == 0);
    REQUIRE(res.output.find("test_quoted_output_456") != std::string::npos);
}
#endif
