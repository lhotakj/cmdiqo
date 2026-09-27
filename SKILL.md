# Cmdiqo Quality & Engineering Guide

> This document mirrors the Antigravity Skill located in [`.agents/skills/cmdiqo-quality/SKILL.md`](.agents/skills/cmdiqo-quality/SKILL.md).

## Quality Gates & SonarQube Compliance

- **Constant-Time Comparison:** Use `hmac.compare_digest` for secret/token verification.
- **Sanitized Execution:** Command parameter placeholders use strict regex validation `(?P<name>[a-zA-Z0-9_\-\.]+)` to prevent command injection.
- **Safe Parameter Substitution:** Use `format_params(template, params)` to substitute validated parameter keys (`{host}`, `{service}`) without breaking PowerShell format braces (`{0}`, `{1}`) or script blocks (`{$_.Status ...}`).
- **Cross-Platform Routing:** The bot automatically detects the target agent's OS (`Windows` vs `Linux`), switching from `run` / `shell` to `windows_run` / `ps1` on Windows agents.
- **Explicit Exceptions:** Avoid bare `except Exception:`. Log all caught unexpected exceptions.
- **No Import-Time Side Effects:** Use configuration dataclasses and factories (`create_bot_app()`, `run_agent()`).
- **Standardized Logging:** Use `logging.getLogger(...)`, never `print()`.
- **WebSocket Keepalives:** Server and client must configure `ping_interval=20` and `ping_timeout=20` for NAT stability.
- **Clean Disconnect Handling:** Pending futures are immediately cancelled and failed on agent disconnect.

## Running Linters & Tests

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run ruff linter
ruff check bot agent development tests

# Run pytest with code coverage (target: >=80%)
pytest -v --cov=bot --cov=agent --cov-report=term-missing --cov-report=xml:coverage.xml
```

## Testing with the Slack Emulator (`slacksim`)

Test slash commands, parameter validation, and remote agent execution locally without Slack credentials:
```bash
# Interactive Slack console emulator:
python run.py sim
# or directly:
python -m slacksim
# or:
python slacksim/slacksim.py

# Non-interactive single-command verification:
python run.py sim -c "/host AMD uptime"
```
The interactive simulator supports full line editing: **Up/Down arrows** for history traversal, and **Esc** to instantly clear/delete the current message draft.

## Build Machinery & Versioning Protocols

All build and maintenance scripts are organized across the repository:
- `build.ps1` & `build.sh`: Local native build scripts at the repository root.
- `development/update_version.py`: Resolves version from `version.txt` and propagates to bundled `_version.py` / `__version__.py` modules across `agent`, `bot`, and `slacksim`.
- `development/build_agent.py`: Unified standalone compiler (PyInstaller, UPX, Authenticode signer).

### Native Binary Release Pipeline
- `build.sh --clean --target installer` produces Linux x64 artifacts only: `cmdiqo-agent`, `cmdiqo-agent-linux-x64.zip`, and `cmdiqo-agent-linux-x64-setup`. Docker builds use Bullseye/glibc 2.31 when available.
- `build.ps1 -Clean -Platform win64 -Target Agent` produces the Windows PE agent, service ZIP, and Tkinter installer: `cmdiqo-agent.exe`, `cmdiqo-agent-windows-x64.zip`, and `cmdiqo-agent-windows-x64-setup.exe`.
- Never attempt to produce a Windows PE file with Linux PyInstaller. The `.github/workflows/build-agent.yml` Windows matrix runner is the supported automated Windows build path.
- A manual **Build Agent Binaries (Windows & Linux x64)** workflow run uploads both platform artifact sets for 90 days. `/release-agent` and `/release` also publish the two ZIP service packages, two raw agent binaries, and two GUI installers to the GitHub Release.

### Dynamic Versioning Rules:
- **CI Builds (GitHub Actions):** Derived dynamically as `<major>.<minor>.<GITHUB_RUN_NUMBER>` (e.g. `1.0.42`).
- **Local Builds:** Derived dynamically using the hex Linux timestamp with `.dev`: `<major>.<minor>.dev<HEX_TIMESTAMP>` (e.g. `1.0.dev6AB4BAC9`).
- **Windows PE Executable Metadata:** Populated via `FixedFileInfo` tuple `(major, minor, ts & 0xFFFF, 0)` and matching string tables (`FileVersion`, `ProductVersion`, `LegalCopyright`).
- See **[BUILD.md](BUILD.md)** for full build system details and Windows Authenticode signing instructions.

## Adding New Commands

Add definitions to `config/commands.yaml` following the pattern:
```yaml
aliases:
  - name: home
    input: "/home"
    output: "/host home"
    description: "Execute commands on home host"
    details: "Translates `/home <cmd>` into `/host home <cmd>` automatically."
    examples:
      - "/home docker ps"
      - "/home uptime"

commands:
  # Cross-platform command (Linux & Windows)
  - name: host-uptime
    trigger: "/host {host} uptime"
    description: "Show system uptime"
    details: "Runs uptime on Linux or queries Win32_OperatingSystem on Windows."
    examples:
      - "/host home uptime"
      - "/host AMD uptime"
    command:
      shell: "uptime"
      ps1: "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; $u = (Get-Date) - $b; 'Uptime: {0}d {1}h {2}m {3}s (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f [int]$u.TotalDays, $u.Hours, $u.Minutes, $u.Seconds, $b"

  - name: host-service-restart
    trigger: "/host {host} service restart {service}"
    description: "Restart a system service on host"
    details: "Issues systemctl restart on Linux or Restart-Service on Windows."
    examples:
      - "/host lab service restart nginx"
      - "/host AMD service restart Spooler"
    command:
      shell: "systemctl restart {service}"
      ps1: "Restart-Service -Name '{service}' -Force; Get-Service -Name '{service}' | Select-Object Name, DisplayName, Status"
    timeout: 60
```
> **Command Resolution & Help Discovery**: Triggers containing `{host}` automatically target that host agent (no redundant `target: "{host}"`). The `command` mapping dynamically selects `shell` on Linux agents and `ps1` on Windows agents. Category is automatically inferred (or can be explicitly set), feeding into Slack `/help` and `/help <category>`. Arbitrary command configuration is supported under `config:`.

Always add corresponding unit tests in `tests/`.
