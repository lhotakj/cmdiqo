---
name: cmdiqo-quality
description: Quality guidelines, SonarQube rules, testing standards, and command security workflows for Cmdiqo.
---

# Cmdiqo Quality, SonarQube & Testing Skill

This skill defines the quality engineering standards, SonarQube quality gate compliance, testing workflows, and command extension protocols for **Cmdiqo**.

---

## 1. SonarQube Clean Code & Security Standards

All contributions to Cmdiqo must satisfy SonarQube Quality Gates (Zero Vulnerabilities, Zero Security Hotspots, Zero Critical Smells, and >80% Test Coverage).

### Security Requirements
- **Constant-Time Cryptographic Comparison (CWE-208 / SonarQube S5804):**
  Never compare authentication tokens or secrets using standard equality operators (`==` or `!=`). Always use `hmac.compare_digest(expected, provided)`.
- **Command Injection Prevention (CWE-78):**
  - Never allow arbitrary shell metacharacters in parameter substitutions.
  - All command triggers must compile placeholder regexes using strict identifier charsets: `(?P<name>[a-zA-Z0-9_\-\.]+)`.
  - Validate parameters with `validate_params(params)` before formatting execution strings.
- **Safe Parameter Substitution (`format_params`):**
  - Never use raw Python string `.format(**params)` on command lines that may contain PowerShell string format braces (`{0}`, `{1}`, `{4:yyyy-MM-dd HH:mm:ss}`) or script blocks (`{$_.Status -eq 'Running'}`).
  - Always use `format_params(template, params)` which substitutes only validated parameter keys (`[a-zA-Z_][a-zA-Z0-9_]*`), preserving PowerShell syntax intact.
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
# Run linter
python -m ruff check bot agent development tests

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
- `tests/test_app.py`: Slack message authorization, help formatting, Windows/Linux dispatch routing, and `format_params` safety.
- `tests/test_slacksim.py`: Slack console emulator, mrkdwn ANSI formatting (bold, italics, code blocks in light gray, emojis), and CLI options.
- `tests/test_line_reader.py`: Terminal line reader with Up/Down command history, Esc line deletion, and cross-platform key parsing.
- `tests/test_parental.py`: Cross-platform hosts file editing, domain filtering, and DNS cache flushing workflows (Windows & Linux).
- `tests/test_run.py`: Multi-process supervisor and signal termination lifecycle.
- `tests/test_version.py`: Dynamic semver resolution, GitHub CI run numbers, hex timestamp dev versioning, and Windows PE version manifests.

### Interactive Verification via `slacksim`
Run commands in the terminal emulator before deploying:
```bash
python run.py sim                     # Interactive console REPL (Up/Down history, Esc clear)
python run.py sim -c "/host AMD uptime" # Non-interactive single-command execution
```

---

## 3. Command Extension Runbook

When adding a new command in `config/commands.yaml`:

1. **Trigger & Host Targeting:**
   - Use clear prefixes: `/lan` for local network queries, `/host` for host and remote operations (e.g. `/host {host} service restart {name}`).
   - Define custom shortcuts in the top-level `aliases:` section (e.g. mapping `input: "/home"` to `output: "/host home"`).
   - Use `{param}` for dynamic arguments (e.g. `/host {host} docker restart {service}`).
   - **Target Resolution**: Triggers containing `{host}` automatically target that host agent without needing a redundant `target: "{host}"`. Triggers without `{host}` execute locally.
   - **Help Discovery**: Categories are automatically inferred (`lan`, `docker`, `services`, `windows`, `system`). Provide `description` (short one-liner), `details` (operational notes), and `examples` (copy-pasteable examples) to populate the Slack `/help` and `/help <category>` interactive manual.
2. **OS-Agnostic `command` Mapping (`python | shell | ps1`):**
   - Each command object MUST contain a mandatory `command:` mapping:
     - `command.python`: Pure Python handler executed in the bot container or remote agent (e.g. `"commands.devices:list_devices"`). Receives `params`, `hub`, and `config`.
     - `command.shell`: Shell command string executed on Linux agents (or locally in the bot container).
     - `command.ps1`: PowerShell command string executed on Windows agents.
   - **Dual OS-Agnostic Execution**: If both `shell` and `ps1` are specified, Cmdiqo inspects the remote agent's reported OS platform (`hub.agents[host]["platform"]`) and dispatches the appropriate command dialect dynamically.
   - **Single OS Restriction**: If only `shell` is provided, attempting to execute on a Windows agent yields an unsupported OS error. If only `ps1` is provided, attempting to execute on a Linux agent yields an unsupported OS error.
3. **General `config` Object:**
   - Any command can define a `config:` dictionary containing arbitrary primitives, lists, or nested maps (e.g. `config.domains: [list]`).
   - Referenced in commands via `{config.<key>}` or passed directly to Python handlers.
4. **Execution Limits:**
   - Always define an explicit `timeout` in seconds (e.g., `timeout: 180`).
5. **Validation:**
   - Write a corresponding unit test in `tests/test_commands.py` verifying that the trigger regex matches valid inputs, validates the `command` mapping, and rejects malicious characters.

---

## 4. SonarQube Scanner Configuration

The repository uses `sonar-project.properties` for continuous inspection:
```properties
sonar.projectKey=Cmdiqo
sonar.projectName=Cmdiqo
sonar.sources=bot,agent
sonar.tests=tests
sonar.python.coverage.reportPaths=coverage.xml
sonar.python.version=3.12,3.13
```

---

## 5. Build Machinery, Dynamic Versioning & Signing Protocols

All build and maintenance scripts are organized across the repository:
- `build.ps1` & `build.sh`: Local native build scripts at the repository root.
- `development/update_version.py`: Resolves version and propagates to `version.txt`, `short_version.txt`, and bundled `_version.py` / `__version__.py` modules across `agent`, `bot`, and `slacksim`.
- `development/build_agent.py`: Unified standalone compiler (PyInstaller, UPX, Authenticode signer).

### Native Binary Release Pipeline
- `build.sh --clean --target installer` produces Linux x64 artifacts only: `cmdiqo-agent`, `cmdiqo-agent-linux-x64.zip`, and `cmdiqo-agent-setup`. Docker builds use Bullseye/glibc 2.31 when available.
- `build.ps1 -Clean -Platform win64 -Target Agent` produces the Windows PE agent, service ZIP, and Tkinter installer: `cmdiqo-agent.exe`, `cmdiqo-agent-windows-x64.zip`, and `cmdiqo-agent-setup.exe`.
- Do not attempt Linux-to-Windows PyInstaller cross-compilation. Use the native Windows matrix runner in `.github/workflows/build-agent.yml`.
- A manual **Build Agent Binaries (Windows & Linux x64)** workflow run retains both platform artifact sets for 90 days. `/release-agent` and `/release` publish the two ZIP service packages, two raw agent binaries, and two GUI installers to the GitHub Release.

### Versioning Rules:
- **CI Builds (GitHub Actions):** Derived dynamically as `<major>.<minor>.<GITHUB_RUN_NUMBER>` (e.g. `1.0.42`).
- **Local Builds:** Derived dynamically using the hex Linux timestamp with `.dev`: `<major>.<minor>.dev<HEX_TIMESTAMP>` (e.g. `1.0.dev6AB4BAC9`).
- **Windows PE Executable Metadata:** Populated via `FixedFileInfo` tuple `(major, minor, ts & 0xFFFF, 0)` and matching string tables (`FileVersion`, `ProductVersion`, `LegalCopyright`).

### Windows Authenticode Digital Signing:
- Binaries are digitally signed with SHA256 and RFC 3161 timestamps (`http://timestamp.digicert.com`).
- Public certificate is exported to `cmdiqo-agent.cer` and registered in `Cert:\CurrentUser\TrustedPublisher` for local trust.
