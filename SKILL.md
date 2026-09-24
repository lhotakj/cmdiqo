# Shango Quality & Engineering Guide

> This document mirrors the Antigravity Skill located in [`.agents/skills/shango-quality/SKILL.md`](.agents/skills/shango-quality/SKILL.md).

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

## Build Machinery & Versioning Protocols

All build and maintenance scripts are centralized in `development/`:
- `development/update_version.py`: Resolves version and propagates to `version.txt`, `short_version.txt`, `agent/_version.py`, `bot/_version.py`, and `agent/version_info.txt`.
- `development/build_agent.py`: Unified standalone compiler (PyInstaller, UPX, Authenticode signer).
- `development/build.ps1` & `development/build.sh`: Local build scripts. Root `build.ps1` and `build.sh` forward to them.

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
    category: system
    trigger: "/host {host} uptime"
    description: "Show system uptime"
    details: "Runs uptime on Linux or queries Win32_OperatingSystem on Windows."
    examples:
      - "/host home uptime"
      - "/host AMD uptime"
    kind: shell
    target: "{host}"
    run: "uptime"
    windows_kind: ps1
    windows_run: "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; $u = (Get-Date) - $b; 'Uptime: {0}d {1}h {2}m {3}s (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f [int]$u.TotalDays, $u.Hours, $u.Minutes, $u.Seconds, $b"

  - name: host-service-restart
    category: services
    trigger: "/host {host} service restart {service}"
    description: "Restart a system service on host"
    details: "Issues systemctl restart on Linux or Restart-Service on Windows."
    examples:
      - "/host lab service restart nginx"
      - "/host AMD service restart Spooler"
    kind: shell
    target: "{host}"
    run: "systemctl restart {service}"
    windows_kind: ps1
    windows_run: "Restart-Service -Name '{service}' -Force; Get-Service -Name '{service}' | Select-Object Name, DisplayName, Status"
    timeout: 60
```
> **Help Discovery**: The `category`, `description`, `details`, and `examples` fields dynamically feed into the Slack `/help` and `/help <category>` interactive manual.

Always add corresponding unit tests in `tests/`.

