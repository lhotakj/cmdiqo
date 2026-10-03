#include "cmdiqo/agent/logger.hpp"

#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <string>

TEST_CASE("Logger: Log level parsing and string conversion", "[logger]") {
    REQUIRE(cmdiqo::agent::parse_log_level("DEBUG") == cmdiqo::agent::LogLevel::Debug);
    REQUIRE(cmdiqo::agent::parse_log_level("debug") == cmdiqo::agent::LogLevel::Debug);
    REQUIRE(cmdiqo::agent::parse_log_level("DeBuG") == cmdiqo::agent::LogLevel::Debug);

    REQUIRE(cmdiqo::agent::parse_log_level("INFO") == cmdiqo::agent::LogLevel::Info);
    REQUIRE(cmdiqo::agent::parse_log_level("info") == cmdiqo::agent::LogLevel::Info);

    REQUIRE(cmdiqo::agent::parse_log_level("WARNING") == cmdiqo::agent::LogLevel::Warning);
    REQUIRE(cmdiqo::agent::parse_log_level("WARN") == cmdiqo::agent::LogLevel::Warning);
    REQUIRE(cmdiqo::agent::parse_log_level("warning") == cmdiqo::agent::LogLevel::Warning);

    REQUIRE(cmdiqo::agent::parse_log_level("ERROR") == cmdiqo::agent::LogLevel::Error);
    REQUIRE(cmdiqo::agent::parse_log_level("error") == cmdiqo::agent::LogLevel::Error);

    REQUIRE(cmdiqo::agent::parse_log_level("CRITICAL") == cmdiqo::agent::LogLevel::Critical);
    REQUIRE(cmdiqo::agent::parse_log_level("FATAL") == cmdiqo::agent::LogLevel::Critical);
    REQUIRE(cmdiqo::agent::parse_log_level("critical") == cmdiqo::agent::LogLevel::Critical);

    REQUIRE(cmdiqo::agent::parse_log_level("UNRECOGNIZED") == cmdiqo::agent::LogLevel::Info);

    REQUIRE(cmdiqo::agent::log_level_to_string(cmdiqo::agent::LogLevel::Debug) == "DEBUG");
    REQUIRE(cmdiqo::agent::log_level_to_string(cmdiqo::agent::LogLevel::Info) == "INFO");
    REQUIRE(cmdiqo::agent::log_level_to_string(cmdiqo::agent::LogLevel::Warning) == "WARNING");
    REQUIRE(cmdiqo::agent::log_level_to_string(cmdiqo::agent::LogLevel::Error) == "ERROR");
    REQUIRE(cmdiqo::agent::log_level_to_string(cmdiqo::agent::LogLevel::Critical) == "CRITICAL");
}

TEST_CASE("Logger: Singleton instance and level configuration", "[logger]") {
    auto& logger = cmdiqo::agent::Logger::instance();
    logger.set_level(cmdiqo::agent::LogLevel::Debug);
    REQUIRE(logger.level() == cmdiqo::agent::LogLevel::Debug);

    logger.set_level(cmdiqo::agent::LogLevel::Error);
    REQUIRE(logger.level() == cmdiqo::agent::LogLevel::Error);

    logger.set_level(cmdiqo::agent::LogLevel::Info);
    REQUIRE(logger.level() == cmdiqo::agent::LogLevel::Info);
}

TEST_CASE("Logger: File logging enable, append, and close", "[logger]") {
    std::filesystem::path log_path = std::filesystem::temp_directory_path() / "cmdiqo_test_logs" / "test_agent.log";
    auto& logger = cmdiqo::agent::Logger::instance();

    logger.enable_file_logging(log_path);
    logger.set_level(cmdiqo::agent::LogLevel::Debug);

    logger.info("Test info message payload 12345");
    logger.debug("Test debug message payload 67890");
    logger.close_file_logging();

    REQUIRE(std::filesystem::exists(log_path));

    std::ifstream ifs(log_path);
    std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
    ifs.close();

    REQUIRE(content.find("[cmdiqo-agent]") != std::string::npos);
    REQUIRE(content.find("Test info message payload 12345") != std::string::npos);
    REQUIRE(content.find("Test debug message payload 67890") != std::string::npos);

    std::filesystem::remove_all(log_path.parent_path());
}
