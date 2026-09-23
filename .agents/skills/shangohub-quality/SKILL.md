---
name: shangohub-quality
description: Quality guidelines, SonarQube rules, testing standards, and command security workflows for ShangoHub.
---

# ShangoHub Quality, SonarQube & Testing Skill

This skill defines the quality engineering standards, SonarQube quality gate compliance, testing workflows, and command extension protocols for **ShangoHub**.

---

## 1. SonarQube Clean Code & Security Standards

All contributions to ShangoHub must satisfy SonarQube Quality Gates (Zero Vulnerabilities, Zero Security Hotspots, Zero Critical Smells, and >80% Test Coverage).

### Security Requirements
- **Constant-Time Cryptographic Comparison (CWE-208 / SonarQube S5804):**
  Never compare authentication tokens or secrets using standard equality operators (`==` or `!=`). Always use `hmac.compare_digest(expected, provided)`.
- **Command Injection Prevention (CWE-78):**
  - Never allow arbitrary shell metacharacters in parameter substitutions.
  - All command triggers must compile placeholder regexes using strict identifier charsets: `(?P<name>[a-zA-Z0-9_\-\.]+)`.
  - Validate parameters with `validate_params(params)` before formatting execution strings.
- **Docker Container Non-Root Security (SonarQube S6437):**
  - Production containers must run as a non-root user when possible.
  - When raw network capabilities are required for ARP scanning (`arp-scan`), assign targeted Linux capabilities (`CAP_NET_RAW`, `CAP_NET_ADMIN`) rather than full container root privileges.

### Reliability & Resilience Standards
- **Explicit Exception Handling (SonarQube S5754 / S112):**
  Avoid bare `except Exception:`. Specify concrete exception types (e.g. `(TimeoutError, json.JSONDecodeError, websockets.ConnectionClosed)`). When catching generic exceptions at top-level event loops, always log with `log.exception(...)` and provide informative user-facing diagnostics.
- **WebSocket Keepalive Ping/Pong:**
  Because the bot and agents operate behind NAT and stateful firewalls, every WebSocket server and client connection **must** configure `ping_interval=20` and `ping_timeout=20` to prevent silent TCP connection drops.
- **Disconnect Cleanup:**
  If an agent drops connection while executing a command, the hub must immediately cancel and fail any pending `asyncio.Future` waiting on that agent, rather than letting the caller hang until timeout.

### Maintainability Standards
- **No Import-Time Side Effects:**
  Modules must never read `os.environ` or load filesystem assets at the top level. Use dataclasses (`BotConfig`, `AgentConfig`) and factory functions (`create_bot_app()`, `run_agent()`).
- **Standardized Logging (SonarQube S106):**
  Never use `print()` for diagnostic or operational output. Use Python's standard `logging.getLogger(...)`.
- **Static Typing:**
  All functions, methods, and parameters must include explicit Python type annotations (`mypy` / SonarQube typed code rules).

---

## 2. Testing Framework & Coverage Workflows

### Running Tests Locally
```bash
# Run full test suite with coverage report
python -m pytest -v --cov=bot --cov=agent --cov-report=term-missing --cov-report=xml:coverage.xml
```

### Coverage Thresholds
- **Line Coverage:** >= 80%
- **Branch Coverage:** >= 75%
- All new features and command handlers must include unit tests.

### Test Structure
- `tests/test_commands.py`: Command YAML loading, regex generation, placeholder extraction, and parameter injection validation.
- `tests/test_devices.py`: Parsing of `arp-scan` and `ip neigh` outputs, ASCII table formatting, and fallback handling.
- `tests/test_hub.py`: Asynchronous WebSocket server tests, token validation, agent registry, command dispatch, and disconnect cleanup.
- `tests/test_agent.py`: Agent execution engine (`execute`), platform argv generation (`build_argv`), timeout handling, and result schema.
- `tests/test_app.py`: Slack message authorization (`ALLOWED_USERS`, `ALLOWED_CHANNELS`), help text formatting, and slash command routing.

---

## 3. Command Extension Runbook

When adding a new command in `config/commands.yaml`:

1. **Trigger Definition:**
   - Use clear prefixes: `/lan` for local network queries, `/home` for host operations.
   - Use `{param}` for dynamic arguments (e.g. `/home docker restart {service}`).
2. **Backend Kind:**
   - `kind: python`: For internal bot logic. Must define `handler: "module.submodule:function"`. The function receives `params: dict[str, str]` and must return a string.
   - `kind: shell`: Executes locally in the container or on a Linux agent.
   - `kind: ps1`: Executes PowerShell on a Windows agent. Must specify `target: "agent_name"`.
3. **Execution Limits:**
   - Always define an explicit `timeout` in seconds (e.g., `timeout: 180`).
4. **Validation:**
   - Write a corresponding unit test in `tests/test_commands.py` verifying that the trigger regex matches valid inputs and rejects malicious characters.

---

## 4. SonarQube Scanner Configuration

The repository uses `sonar-project.properties` for continuous inspection:
```properties
sonar.projectKey=ShangoHub
sonar.projectName=ShangoHub
sonar.sources=bot,agent
sonar.tests=tests
sonar.python.coverage.reportPaths=coverage.xml
sonar.python.version=3.12,3.13
```
