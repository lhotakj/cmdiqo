# ShangoHub Quality & Engineering Guide

> This document mirrors the Antigravity Skill located in [`.agents/skills/shangohub-quality/SKILL.md`](.agents/skills/shangohub-quality/SKILL.md).

## Quality Gates & SonarQube Compliance

- **Constant-Time Comparison:** Use `hmac.compare_digest` for secret/token verification.
- **Sanitized Execution:** Command parameter placeholders use strict regex validation `(?P<name>[a-zA-Z0-9_\-\.]+)` to prevent command injection.
- **Explicit Exceptions:** Avoid bare `except Exception:`. Log all caught unexpected exceptions.
- **No Import-Time Side Effects:** Use configuration dataclasses and factories (`create_bot_app()`, `run_agent()`).
- **Standardized Logging:** Use `logging.getLogger(...)`, never `print()`.
- **WebSocket Keepalives:** Server and client must configure `ping_interval=20` and `ping_timeout=20` for NAT stability.

## Running Tests

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run pytest with code coverage
pytest -v --cov=bot --cov=agent --cov-report=term-missing --cov-report=xml:coverage.xml
```

## Adding New Commands

Add definitions to `config/commands.yaml` following the pattern:
```yaml
- name: my-command
  trigger: "/home service restart {name}"
  description: "Restart a system service"
  kind: shell
  run: "systemctl restart {name}"
  timeout: 60
```
Always add corresponding unit tests in `tests/`.
