# Cmdiqo Native Agent (`agent.c`)

> **Component:** `agent.c` (Native ISO C++20 Standalone Agent)  
> **Binary Output:** `cmdiqo-agent` (Linux/macOS) / `cmdiqo-agent.exe` (Windows)  
> **Standard:** ISO C++20  
> **Dependencies:** `ixwebsocket`, `nlohmann-json`, `cli11`, `catch2`

---

## 1. Overview & Architecture

The **Cmdiqo Native Agent** (`agent.c`) is an outbound-only WebSocket client daemon designed for secure remote host automation. It connects directly to the Cmdiqo Bot Hub, performs authentication via Bearer token, receives execution requests (`type: "exec"`), spawns isolated host commands (PowerShell on Windows, Bash on Linux), streams output, and reports results (`type: "result"`).

```
   ┌─────────────────────────────────────────────────────────────┐
   │                  Cmdiqo Bot Hub (Server)                    │
   └──────────────────────────────┬──────────────────────────────┘
                                  │ Outbound TLS WebSocket (Bearer Token)
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │              Cmdiqo Native Agent (agent.c)                  │
   │                                                             │
   │  ┌────────────────┐     ┌────────────────────────────────┐  │
   │  │ AgentConfig    │     │ WebSocket Runner (IXWebSocket) │  │
   │  │ (CLI/ENV/JSON) │     │ - Ping/Pong Heartbeat (20s)    │  │
   │  │                │     │ - Auto-reconnect & Backoff     │  │
   │  └────────────────┘     └──────────────┬─────────────────┘  │
   │                                        │                    │
   │                                        ▼                    │
   │                         ┌────────────────────────────────┐  │
   │                         │ Process Engine (process_*.cpp) │  │
   │                         │ - Win32 Job Object / POSIX PG  │  │
   │                         │ - Timeout Watchdog Watcher     │  │
   │                         └──────────────┬─────────────────┘  │
   └────────────────────────────────────────┼────────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
         [powershell.exe / cmd.exe]                            [/bin/bash]
                 (Windows)                                       (Linux)
```

---

## 2. Key Features

- **Super-Lightweight Resource Footprint:** ~1–2 MB static release binary, verified **~1.1 MB RAM on Linux** and **~3.3 MB RAM on Windows**, with instant cold boot (<10 ms).
- **Process Tree Isolation:**
  - **Windows:** Process assigned to a Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
  - **Linux:** Process group created via `setpgid(0, 0)`; timeouts send `SIGKILL` to `-pid`.
- **Zero Antivirus Heuristics:** Clean PE binary signed via Authenticode with embedded manifest and version resources.
- **Robust Networking:** Built on `ixwebsocket` with OS-native TLS (SChannel on Windows, OpenSSL/mbedTLS on Linux), 20s ping/pong keepalives, and exponential backoff retry.
- **Windows Service & POSIX Daemon:** Native Windows Service Control Manager (SCM) integration (`--service`) and POSIX signal handling.

---

## 3. Configuration Reference

### 3.1 Precedence Hierarchy
Configuration parameters are evaluated in strict priority order:
$$\text{CLI Flags} > \text{Environment Variables} > \text{JSON Configuration File} > \text{System Defaults}$$

### 3.2 Command-Line Options
| Flag | Short | Description | Default |
| :--- | :--- | :--- | :--- |
| `--help` | `-h` | Display usage manual and exit | — |
| `--version` | `-v` | Display version string (`cmdiqo-agent <version>`) | — |
| `--hub-url` | `-u` | Hub WebSocket URL (`ws://...` or `wss://...`) | `HUB_URL` / JSON config |
| `--token` | `-t` | Shared `AGENT_TOKEN` secret | `AGENT_TOKEN` / JSON config |
| `--name` | `-n` | Agent target hostname | `AGENT_NAME` / system hostname |
| `--config` | `-c` | Path to JSON configuration file | `AGENT_CONFIG` |
| `--reconnect-delay` | `-d` | Initial reconnect delay in seconds (must be > 0.0) | `5.0` |
| `--log-level` | — | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `INFO` |
| `--agent-connection-notify` | — | Notify Slack when agent connects/disconnects | `false` |
| `--service` | — | Run as Windows Service under SCM (Windows only) | `false` |

### 3.3 Environment Variables
- `HUB_URL`: WebSocket endpoint (e.g. `ws://192.168.1.10:8765`).
- `AGENT_TOKEN`: Shared secret authorization token.
- `AGENT_NAME`: Host identifier or alias.
- `AGENT_CONFIG`: Path to JSON configuration file.
- `RECONNECT_DELAY`: Float reconnect delay in seconds (e.g. `5.0`).
- `AGENT_CONNECTION_NOTIFY`: Boolean (`true`, `1`, `yes`, `on`).
- `LOG_LEVEL`: Verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).

### 3.4 JSON Configuration File (`agent.json`)
```json
{
  "hub_url": "ws://192.168.1.10:8765",
  "token": "your-agent-token-here",
  "name": "AMD-Desktop",
  "reconnect_delay": 5.0,
  "agent_connection_notify": true
}
```

---

## 4. WebSocket Wire Protocol

### 4.1 Handshake (`type: "hello"`)
Sent immediately after WebSocket handshake:
```json
{
  "type": "hello",
  "name": "AMD-Desktop",
  "version": "1.0.0",
  "platform": "Windows/10.0",
  "agent_connection_notify": true
}
```

### 4.2 Execution Request (`type: "exec"`)
Received from the Hub:
```json
{
  "id": "c1f7b880-994b-486a-bc12-f470a1a09d3b",
  "type": "exec",
  "kind": "ps1",
  "cmd": "Get-Service -Name Spooler",
  "cwd": "C:\\Windows\\System32",
  "timeout": 30
}
```

### 4.3 Execution Result (`type: "result"`)
Returned by the Agent to the Hub:
```json
{
  "id": "c1f7b880-994b-486a-bc12-f470a1a09d3b",
  "type": "result",
  "ok": true,
  "code": 0,
  "output": "Status   Name               DisplayName\n------   ----               -----------\nRunning  Spooler            Print Spooler\n",
  "error": null
}
```

---

## 5. Building and Testing

### 5.1 Prerequisites
- CMake 3.24+
- C++20 compliant compiler (GCC 13+, Clang 16+, MSVC 19.34+, or MinGW-w64)
- Dependencies installed via `vcpkg` (`catch2`, `cli11`, `ixwebsocket`, `nlohmann-json`)

### 5.2 CMake Compilation
```bash
# Configure preset
cmake --preset linux-x64        # or windows-x64-mingw

# Build Release target
cmake --build --preset build-linux-x64 --config Release

# Run test suite
ctest --preset test-linux-x64 --output-on-failure
```

### 5.3 Unified Repository Build
```bash
# Windows
.\build.ps1 -Agent -Platform Windows -Config Release

# Linux
./build.sh --agent --platform linux64 --config Release
```

---

## 6. Service Installation

### 6.1 Windows (PowerShell Administrator)
```powershell
.\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "SECRET_TOKEN"
```
Logs are automatically written to `C:\ProgramData\Cmdiqo\Agent\logs\service.log`.

### 6.2 Linux (Systemd)
```bash
sudo ./install-service.sh --hub-url "ws://192.168.1.10:8765" --token "SECRET_TOKEN"
```
Logs are accessible via `journalctl -u cmdiqo-agent -f`.
