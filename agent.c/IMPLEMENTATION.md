# Cmdiqo Native Agent Implementation Guide (`IMPLEMENTATION.md`)

> **Component:** `agent.c` (Native C++20 Standalone Agent)  
> **Binary Name:** `cmdiqo-agent` (Linux/macOS) / `cmdiqo-agent.exe` (Windows)  
> **Standard:** ISO C++20  
> **Target OS:** Windows 10/11/Server, Linux (x86_64, ARM64), macOS

---

## 1. Architectural Overview

The native Cmdiqo Agent is an outbound-only WebSocket client that connects securely to the Cmdiqo Bot Hub. It receives JSON execution commands (`type: "exec"`), executes host-level shell operations (`PowerShell` on Windows, `Bash` on Linux), streams combined stdout/stderr, and returns execution status JSON (`type: "result"`).

```
   ┌─────────────────────────────────────────────────────────────┐
   │                  Cmdiqo Bot Hub (Server)                    │
   └──────────────────────────────┬──────────────────────────────┘
                                  │ Outbound WSS Connection (Bearer Token)
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │              Cmdiqo Native Agent (agent.c)                  │
   │                                                             │
   │  ┌────────────────┐     ┌────────────────────────────────┐  │
   │  │ AgentConfig    │     │ WebSocket Runner (IXWebSocket) │  │
   │  │ (CLI/ENV/JSON) │     │ - Ping/Pong Heartbeat (20s)    │  │
   │  └────────────────┘     │ - Auto-reconnect & Backoff     │  │
   │                         └──────────────┬─────────────────┘  │
   │                                        │                    │
   │                                        ▼                    │
   │                         ┌────────────────────────────────┐  │
   │                         │ Process Engine (process_*.cpp) │  │
   │                         │ - Win32 Job Object / POSIX PG  │  │
   │                         │ - Timeout Watchdog Cancellation│  │
   │                         └──────────────┬─────────────────┘  │
   └────────────────────────────────────────┼────────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
         [powershell.exe / cmd.exe]                            [/bin/bash]
                 (Windows)                                       (Linux)
```

---

## 2. Core Modules & Component Breakdown

### 2.1 Configuration & Diagnostic Engine (`config.hpp` / `config.cpp`)
* **Precedence Order:** `CLI Arguments > Environment Variables > JSON Config File > System Hostname`.
* **Preflight Validation:** Verifies WebSocket scheme (`ws://` or `wss://`), presence of `token`, positive reconnect delay, and valid JSON file paths.
* **Diagnostic Report:** Formats configuration errors into an exact 76-character visual diagnostic banner printed to `stderr` with exit code `2`.

### 2.2 Structured Logging Engine (`logger.hpp` / `logger.cpp`)
* **Thread-Safety:** Atomic log level threshold and mutex-synchronized console (`stdout` / `stderr`) and optional persistent file logging.
* **Timestamp Formatting:** High-precision millisecond timestamps matching Python standard logging:
  `YYYY-MM-DD HH:MM:SS,mmm LEVEL [cmdiqo-agent] message`
* **Log Levels:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
* **Directory Safety:** Automatic creation of parent directories for log file outputs.

### 2.3 Process Execution & Isolation (`process.hpp`, `process_win32.cpp`, `process_linux.cpp`)
* **Windows Isolation (Win32):**
  * Spawns child using `CreateProcessW` in suspended state.
  * Attaches process to a Windows Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
  * Asynchronously drains standard output pipe to prevent OS buffer deadlocks.
  * If execution exceeds the request `timeout`, the entire Job Object is terminated cleanly.
* **Linux Isolation (POSIX):**
  * Spawns child using `pipe()` + `fork()`.
  * Calls `setpgid(0, 0)` in child to form a distinct process group.
  * If execution exceeds `timeout`, sends `kill(-child_pid, SIGKILL)` to kill the entire process tree.

### 2.4 WebSocket & Protocol Client (`ws_client.hpp` / `ws_client.cpp`, `protocol.hpp`)
* **Engine:** Built on `ixwebsocket` with native OS TLS (SChannel on Windows, OpenSSL/mbedTLS on Linux).
* **Handshake:** Sends initial `hello` frame upon connection containing hostname, agent version, OS platform, and connection notification flag.
* **Heartbeat:** 20-second keepalive ping/pong prevents NAT drops.
* **Exponential Backoff:** Retries dropped connections starting at `reconnect_delay` (default 5.0s) up to a 60.0s ceiling (`delay = min(60.0, delay * 1.5)`).

### 2.5 Windows Service & Daemon Integration (`service.hpp`, `service_win32.cpp`, `service_linux.cpp`)
* **SCM Registration:** Invoked via `--service` flag. Dispatches `SERVICE_TABLE_ENTRYW` to Windows Service Control Manager.
* **Lifecycle Reporting:** Reports `SERVICE_START_PENDING`, `SERVICE_RUNNING`, `SERVICE_STOP_PENDING`, and `SERVICE_STOPPED`.
* **Persistent Logging:** Automatically routes service logs to `C:\ProgramData\Cmdiqo\Agent\logs\service.log`.
* **POSIX Signals:** Intercepts `SIGINT` and `SIGTERM` for graceful shutdown and ignores `SIGPIPE`.

---

## 3. Test Architecture & Coverage Matrix

The native agent test suite is built on **Catch2 v3** (`tests/`) and covers all components:

| Test Suite | File | Covered Scenarios |
| :--- | :--- | :--- |
| **Config Tests** | `tests/config_tests.cpp` | Required CLI options, env var fallbacks, JSON config files, corrupt JSON handling, precedence resolution (CLI > ENV > JSON > Hostname), delay validation, boolean string variants, preflight banner formatting. |
| **Protocol Tests** | `tests/protocol_tests.cpp` | `HelloMessage` JSON serialization, `ExecRequest` valid & invalid deserialization, defaults, timeout clamping, `ResultResponse` success/error/timeout serialization. |
| **Process Tests** | `tests/process_tests.cpp` | `build_argv` platform mapping, echo execution, non-zero exit codes, custom working directories, timeout watchdog process tree cancellation. |
| **Logger Tests** | `tests/logger_tests.cpp` | Case-insensitive level parsing, string conversion, singleton level mutability, file logging stream appending, and directory creation. |
| **Version Tests** | `tests/version_tests.cpp` | Dynamic semver extraction and formatted version banner verification. |

---

## 4. Building and Testing

### 4.1 Dependencies (`vcpkg.json`)
* `catch2` (testing)
* `cli11` (CLI argument parsing)
* `ixwebsocket` (WebSocket & TLS)
* `nlohmann-json` (JSON serialization)

### 4.2 Building with CMake
```bash
# Configure
cmake --preset linux-x64 # or windows-x64-mingw on MinGW

# Build Release binary
cmake --build --preset build-linux-x64 --config Release

# Run tests
ctest --preset test-linux-x64 --output-on-failure
```
