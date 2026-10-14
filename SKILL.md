# Cmdiqo Quality & Engineering Guide

> This document mirrors the Antigravity Skill located in [`.agents/skills/cmdiqo-quality/SKILL.md`](.agents/skills/cmdiqo-quality/SKILL.md).

## Quality Gates & SonarQube Compliance

- **Constant-Time Comparison:** Use `hmac.compare_digest` for secret/token verification.
- **Sanitized Execution:** Command parameter placeholders use strict regex validation `(?P<name>[a-zA-Z0-9_\-\.]+)` to prevent command injection.
- **Safe Parameter Substitution:** Use `format_params(template, params)` to substitute validated parameter keys (`{host}`, `{service}`) without breaking PowerShell format braces (`{0}`, `{1}`) or script blocks (`{$_.Status ...}`).
- **PowerShell Quote Safety Constraint:** Windows PowerShell scripts executed via `ps1` or generated dynamically on the agent must use strictly single-quoted strings (`'`). Never include unescaped double quotes (`"`) inside generated scripts to prevent token stripping or escaping breakage across nested process calls (`powershell.exe -Command ...`). Use `[char]34` or single-quoted string concatenation when double quote characters are needed.
- **Cross-Platform Routing:** The bot automatically detects the target agent's OS (`Windows` vs `Linux`), switching from `run` / `shell` to `windows_run` / `ps1` on Windows agents.
- **Logon Hours Enforcement & Task Scheduler Principal:**
  - Tasks displaying interactive WinRT toast notifications or Windows Forms alerts must register under the target user's interactive session principal (`New-ScheduledTaskPrincipal -UserId $targetUser -LogonType Interactive`), while forced logoff commands register under `SYSTEM` (`-LogonType ServiceAccount -RunLevel Highest`).
  - All scheduled enforcement tasks must specify `-StartWhenAvailable` (via `New-ScheduledTaskSettingsSet`) to guarantee execution after waking from sleep or hibernation.
  - Reset operations (`off`) must cleanly unregister all scheduled tasks (`Cmdiqo-Restrict-<user>-*`) and purge associated helper scripts from `C:\ProgramData\Cmdiqo\Agent\scripts\`.
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
- `build.sh --clean --target installer` defaults to Linux x64 on Unix and Windows x64 on Git Bash. Docker installer builds use `cmdiqo-installer-builder:all` for native GTK3/Linux and cross-compiled wxMSW/Windows setups; Linux agent builds use Debian trixie.
- `build.ps1 -Clean -Platform win64 -Target Installer` produces the Windows PE agent, service ZIP, and native self-contained wxWidgets installer: `cmdiqo-agent.exe`, `cmdiqo-agent-windows-x64.zip`, and `cmdiqo-agent-windows-x64-setup.exe`. `-Platform all` builds both platforms using native Windows Python and Docker.
- From Unix, `build.sh --installer --platform win64 --skip-agent-build` cross-compiles the Windows C++ setup using an existing Windows-built agent binary. `--platform all --skip-agent-build` rebuilds both setups from their existing binaries.
- Both Windows and Linux native C++ agents and setup executables are built inside Docker containers via MinGW cross-compilation on Linux (Ubuntu 24.04). No Windows runner is required.
- The unified workflow `.github/workflows/release.yml` utilizes `build.sh` on Linux with all options to build and publish both platform packages, native installers, Docker multi-arch images, and GitHub Releases when commit messages contain `/release`.

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
> **Command Resolution & Help Discovery**: Triggers containing `{host}` automatically target that host agent (no redundant `target: "{host}"`). The `command` mapping dynamically selects `shell` on Linux agents and `ps1` on Windows agents. Category is automatically inferred (or can be explicitly set), feeding into Slack `/help` and `/help <category>`. Arbitrary command configuration is supported under `config:`. Second-level multi-word and lemma/synonym help queries (e.g. `/help host restriction`, `/help host restrict`, `/help restrict`) are dynamically matched against spec triggers, descriptions, and details.

Always add corresponding unit tests in `tests/`.
