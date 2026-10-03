#pragma once

#include <atomic>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>

namespace cmdiqo::agent {

/**
 * @brief Log verbosity levels matching standard logging frameworks.
 */
enum class LogLevel {
    Debug = 0,    ///< Detailed diagnostic messages for development and troubleshooting.
    Info = 1,     ///< General operational events (connections, command dispatch, lifecycle).
    Warning = 2,  ///< Non-fatal unexpected events or transient connection drops.
    Error = 3,    ///< Recoverable command failures or execution errors.
    Critical = 4  ///< Unrecoverable errors requiring immediate termination.
};

/**
 * @brief Parses a string into a LogLevel enum value (case-insensitive).
 * @param str Input string representation (e.g., "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL").
 * @return Corresponding LogLevel enum value, or LogLevel::Info if unrecognized.
 */
[[nodiscard]] LogLevel parse_log_level(std::string_view str) noexcept;

/**
 * @brief Converts a LogLevel enum value to its uppercase string representation.
 * @param level LogLevel enum value.
 * @return String view of the level name (e.g., "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").
 */
[[nodiscard]] std::string_view log_level_to_string(LogLevel level) noexcept;

/**
 * @brief Thread-safe structured logging engine for the Cmdiqo agent.
 *
 * Provides thread-safe console output (stdout for Info/Debug/Warning, stderr for Error/Critical)
 * and optional persistent file logging. Log line format matches Python standard logging:
 * `YYYY-MM-DD HH:MM:SS,mmm LEVEL [cmdiqo-agent] message`
 */
class Logger {
public:
    /**
     * @brief Accesses the singleton Logger instance.
     * @return Reference to the global Logger.
     */
    static Logger& instance();

    /**
     * @brief Sets the minimum logging threshold level.
     * @param level New minimum LogLevel.
     */
    void set_level(LogLevel level) noexcept;

    /**
     * @brief Gets the current logging threshold level.
     * @return Current LogLevel.
     */
    [[nodiscard]] LogLevel level() const noexcept;

    /**
     * @brief Enables persistent file logging to the specified file path.
     * @param file_path Path to the log file (parent directories are created automatically).
     */
    void enable_file_logging(const std::filesystem::path& file_path);

    /**
     * @brief Flushes and closes the active file logging stream.
     */
    void close_file_logging();

    /**
     * @brief Logs a raw message at the specified log level.
     * @param level Severity level.
     * @param message Message payload text.
     */
    void log(LogLevel level, std::string_view message);

    /**
     * @brief Logs formatted arguments at DEBUG level.
     * @tparam Args Variadic streamable types.
     * @param args Arguments to concatenate.
     */
    template <typename... Args>
    void debug(const Args&... args) {
        if (level_.load() <= LogLevel::Debug) {
            log(LogLevel::Debug, format_args(args...));
        }
    }

    /**
     * @brief Logs formatted arguments at INFO level.
     * @tparam Args Variadic streamable types.
     * @param args Arguments to concatenate.
     */
    template <typename... Args>
    void info(const Args&... args) {
        if (level_.load() <= LogLevel::Info) {
            log(LogLevel::Info, format_args(args...));
        }
    }

    /**
     * @brief Logs formatted arguments at WARNING level.
     * @tparam Args Variadic streamable types.
     * @param args Arguments to concatenate.
     */
    template <typename... Args>
    void warning(const Args&... args) {
        if (level_.load() <= LogLevel::Warning) {
            log(LogLevel::Warning, format_args(args...));
        }
    }

    /**
     * @brief Logs formatted arguments at ERROR level.
     * @tparam Args Variadic streamable types.
     * @param args Arguments to concatenate.
     */
    template <typename... Args>
    void error(const Args&... args) {
        if (level_.load() <= LogLevel::Error) {
            log(LogLevel::Error, format_args(args...));
        }
    }

    /**
     * @brief Logs formatted arguments at CRITICAL level.
     * @tparam Args Variadic streamable types.
     * @param args Arguments to concatenate.
     */
    template <typename... Args>
    void critical(const Args&... args) {
        if (level_.load() <= LogLevel::Critical) {
            log(LogLevel::Critical, format_args(args...));
        }
    }

private:
    Logger() = default;
    ~Logger();
    Logger(const Logger&) = delete;
    Logger& operator=(const Logger&) = delete;

    template <typename... Args>
    std::string format_args(const Args&... args) {
        std::ostringstream ss;
        (ss << ... << args);
        return ss.str();
    }

    std::atomic<LogLevel> level_{LogLevel::Info};
    std::mutex mutex_;
    std::ofstream file_stream_;
    bool file_logging_enabled_{false};
};

}  // namespace cmdiqo::agent
