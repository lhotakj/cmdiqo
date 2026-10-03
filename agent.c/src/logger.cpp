#include "cmdiqo/agent/logger.hpp"

#include <chrono>
#include <ctime>
#include <iomanip>
#include <iostream>

namespace cmdiqo::agent {

LogLevel parse_log_level(std::string_view str) noexcept {
    std::string s;
    s.reserve(str.size());
    for (char c : str) {
        s.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    }

    if (s == "DEBUG") return LogLevel::Debug;
    if (s == "INFO") return LogLevel::Info;
    if (s == "WARNING" || s == "WARN") return LogLevel::Warning;
    if (s == "ERROR") return LogLevel::Error;
    if (s == "CRITICAL" || s == "FATAL") return LogLevel::Critical;

    return LogLevel::Info;
}

std::string_view log_level_to_string(LogLevel level) noexcept {
    switch (level) {
        case LogLevel::Debug: return "DEBUG";
        case LogLevel::Info: return "INFO";
        case LogLevel::Warning: return "WARNING";
        case LogLevel::Error: return "ERROR";
        case LogLevel::Critical: return "CRITICAL";
    }
    return "INFO";
}

Logger& Logger::instance() {
    static Logger logger;
    return logger;
}

Logger::~Logger() {
    close_file_logging();
}

void Logger::set_level(LogLevel level) noexcept {
    level_.store(level);
}

LogLevel Logger::level() const noexcept {
    return level_.load();
}

void Logger::enable_file_logging(const std::filesystem::path& file_path) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_stream_.is_open()) {
        file_stream_.close();
    }
    if (file_path.has_parent_path()) {
        std::filesystem::create_directories(file_path.parent_path());
    }
    file_stream_.open(file_path, std::ios::out | std::ios::app);
    file_logging_enabled_ = file_stream_.is_open();
}

void Logger::close_file_logging() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (file_stream_.is_open()) {
        file_stream_.close();
    }
    file_logging_enabled_ = false;
}

void Logger::log(LogLevel level, std::string_view message) {
    if (level < level_.load()) {
        return;
    }

    auto now = std::chrono::system_clock::now();
    auto now_time_t = std::chrono::system_clock::to_time_t(now);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  now.time_since_epoch()) % 1000;

    std::tm tm_buf{};
#if defined(_WIN32)
    localtime_s(&tm_buf, &now_time_t);
#else
    localtime_r(&now_time_t, &tm_buf);
#endif

    std::ostringstream ss;
    ss << std::put_time(&tm_buf, "%Y-%m-%d %H:%M:%S") << ','
       << std::setfill('0') << std::setw(3) << ms.count() << " "
       << log_level_to_string(level) << " [cmdiqo-agent] "
       << message << "\n";

    std::string formatted = ss.str();

    std::lock_guard<std::mutex> lock(mutex_);
    if (level >= LogLevel::Error) {
        std::cerr << formatted << std::flush;
    } else {
        std::cout << formatted << std::flush;
    }

    if (file_logging_enabled_ && file_stream_.is_open()) {
        file_stream_ << formatted << std::flush;
    }
}

}  // namespace cmdiqo::agent
