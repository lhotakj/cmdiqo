"""shango-bot - manage your local network from Slack, even behind NAT.

Uses Slack Socket Mode (outbound WebSocket only) so no ports need to be exposed
to the internet. Also embeds a Hub server: LAN agents (Windows/Linux) connect
OUTBOUND to the hub so you can run commands on any host from Slack.

Commands are defined in a YAML file and can run:
  - python  - a Python function inside the container
  - shell   - a command locally in the container, or on a Linux agent (target:)
  - ps1     - a PowerShell command on a Windows agent (requires target:)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import yaml
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from bot.hub import Hub

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("shango-bot")

MAX_BLOCK = 3500  # Slack message limit is ~4000 chars
SAFE_PARAM_RE = re.compile(r"^[a-zA-Z0-9_\-\.:]+$")
DEFAULT_COMMANDS_CONFIG = "/app/config/commands.yaml"
DEFAULT_SHELL_TIMEOUT = 120
DEFAULT_HUB_PORT = 8765
DEFAULT_BIND_HOST = "0.0.0.0"  # nosec: B104


class BotConfigurationError(ValueError):
    """Raised when one or more required bot configuration settings are missing or invalid."""

    def __init__(self, issues: list[tuple[str, str, str]]) -> None:
        message = "; ".join(f"{name}: {desc}" for name, desc, _ in issues)
        super().__init__(message)
        self.issues = issues


@dataclass
class BotConfig:
    commands_config_path: str = DEFAULT_COMMANDS_CONFIG
    shell_timeout: int = DEFAULT_SHELL_TIMEOUT
    allowed_users: set[str] = field(default_factory=set)
    allowed_channels: set[str] = field(default_factory=set)
    hub_enabled: bool = True
    hub_host: str = DEFAULT_BIND_HOST
    hub_port: int = DEFAULT_HUB_PORT
    agent_token: str = ""
    slack_bot_token: str = ""
    slack_app_token: str = ""

    def validate(self) -> None:
        """Perform preflight checks on critical configuration."""
        issues: list[tuple[str, str, str]] = []
        if not self.slack_bot_token:
            issues.append((
                "SLACK_BOT_TOKEN",
                "Slack Bot User OAuth Token is required",
                "Set SLACK_BOT_TOKEN environment variable (starts with 'xoxb-')",
            ))
        if not self.slack_app_token:
            issues.append((
                "SLACK_APP_TOKEN",
                "Slack App-Level Token is required for Socket Mode",
                "Set SLACK_APP_TOKEN environment variable (starts with 'xapp-')",
            ))
        if self.hub_enabled and self.hub_port <= 0:
            issues.append((
                "HUB_PORT",
                f"Invalid HUB_PORT '{self.hub_port}'",
                "Must be a valid TCP port number (e.g. 8765)",
            ))
        if issues:
            raise BotConfigurationError(issues)

    @classmethod
    def from_env(cls) -> BotConfig:
        """Load configuration cleanly from environment variables without import-time side effects."""
        return cls(
            commands_config_path=os.getenv("COMMANDS_CONFIG", DEFAULT_COMMANDS_CONFIG),
            shell_timeout=int(os.getenv("SHELL_TIMEOUT", str(DEFAULT_SHELL_TIMEOUT))),
            allowed_users={x.strip() for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()},
            allowed_channels={x.strip() for x in os.getenv("ALLOWED_CHANNELS", "").split(",") if x.strip()},
            hub_enabled=os.getenv("HUB_ENABLED", "true").lower() == "true",
            hub_host=os.getenv("HUB_HOST", DEFAULT_BIND_HOST),
            hub_port=int(os.getenv("HUB_PORT", str(DEFAULT_HUB_PORT))),
            agent_token=os.getenv("AGENT_TOKEN", ""),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            slack_app_token=os.getenv("SLACK_APP_TOKEN", ""),
        )


# ---------------------------------------------------------------------------
# Command loading & validation
# ---------------------------------------------------------------------------

def trigger_to_regex(trigger: str) -> re.Pattern[str]:
    """Convert a trigger template like '/home docker restart {service}' to regex.

    Enforces safe identifier characters for placeholders to prevent command injection.
    """
    out = "^"
    for part in re.split(r"(\{\w+\})", trigger):
        m = re.fullmatch(r"\{(\w+)\}", part)
        if m:
            out += rf"(?P<{m.group(1)}>[a-zA-Z0-9_\-\.]+)"
        else:
            out += re.escape(part)
    return re.compile(out + r"\s*$", re.IGNORECASE)


def validate_params(params: dict[str, str]) -> None:
    """Validate that parameters do not contain shell injection characters."""
    for key, val in params.items():
        if not SAFE_PARAM_RE.match(val):
            raise ValueError(f"Invalid parameter value for '{key}': '{val}' contains unsafe characters.")


def load_commands(path: str) -> list[dict[str, Any]]:
    """Load and compile commands from YAML specification file."""
    if not os.path.exists(path):
        log.warning("Command configuration file not found at %s", path)
        return []

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    commands: list[dict[str, Any]] = []
    for spec in data.get("commands", []):
        spec.setdefault("name", spec["trigger"])
        spec.setdefault("description", "")
        commands.append({"spec": spec, "regex": trigger_to_regex(spec["trigger"])})
    log.info("Loaded %d command(s) from %s", len(commands), path)
    return commands


# ---------------------------------------------------------------------------
# Execution backends
# ---------------------------------------------------------------------------

def _run_python(spec: dict[str, Any], params: dict[str, str]) -> str:
    module_name, func_name = spec["handler"].split(":", 1)
    module = importlib.import_module(f"bot.{module_name}")
    func = getattr(module, func_name)
    return str(func(params))


def split_cmdline(cmdline: str) -> list[str]:
    """Split command-line string into arguments, supporting cross-platform path normalization."""
    if os.name == "nt":
        cmdline = cmdline.replace("\\", "/")
    return shlex.split(cmdline, posix=True)


def _run_shell_local(cmdline: str, cwd: str | None, timeout: int) -> str:
    try:
        argv = split_cmdline(cmdline)
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )  # nosec: B603
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return output or f"(finished with exit code {proc.returncode})"
    except FileNotFoundError as exc:
        return f"Error: executable not found: {exc}"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except OSError as exc:
        return f"Error executing command: {exc}"


async def execute_command(
    spec: dict[str, Any],
    params: dict[str, str],
    hub: Hub | None = None,
    default_timeout: int = 120,
) -> str:
    """Route a command to the container or to an agent when `target` is set."""
    validate_params(params)
    kind = spec["kind"]
    cmdline = spec["run"].format(**params)
    cwd = spec.get("cwd")
    if cwd:
        cwd = cwd.format(**params)
    timeout = int(spec.get("timeout", default_timeout))

    target = spec.get("target")
    if target:
        if hub is None:
            raise RuntimeError("hub is disabled (HUB_ENABLED=false)")
        agent_name = target.format(**params)
        return await hub.exec(agent_name, kind, cmdline, cwd=cwd, timeout=timeout)

    if kind == "ps1":
        return "kind 'ps1' requires a 'target' pointing at a Windows agent"
    if kind == "shell":
        return await asyncio.to_thread(_run_shell_local, cmdline, cwd, timeout)
    return await asyncio.to_thread(_run_python, spec, params)


# ---------------------------------------------------------------------------
# Dispatch & Formatting
# ---------------------------------------------------------------------------

def help_text(commands: list[dict[str, Any]], prefix: str | None = None) -> str:
    lines = ["*Available commands:*"]
    for c in commands:
        spec = c["spec"]
        if prefix and not spec["trigger"].lower().startswith(prefix.lower() + " "):
            continue
        tgt = f" [on `{spec['target']}`]" if spec.get("target") else ""
        lines.append(f"\u2022 `{spec['trigger']}`{tgt} \u2014 {spec['description']}")
    if len(lines) == 1:
        lines.append("_(none)_")
    return "\n".join(lines)


async def reply_output(
    post: Callable[[str], Coroutine[Any, Any, None]],
    header: str,
    output: str,
) -> None:
    """Post structured output back to Slack in chunks within the message size limit."""
    output = (output or "(no output)").replace("```", "\u200b```")
    chunks = [output[i : i + MAX_BLOCK] for i in range(0, len(output), MAX_BLOCK)] or ["(no output)"]
    if len(chunks) == 1:
        await post(f"{header}\n```{chunks[0]}```")
    else:
        for i, chunk in enumerate(chunks, 1):
            await post(f"{header} (part {i}/{len(chunks)})\n```{chunk}```")


async def handle_command(
    text: str,
    user_id: str,
    channel_id: str,
    post: Callable[[str], Coroutine[Any, Any, None]],
    commands: list[dict[str, Any]],
    hub: Hub | None = None,
    allowed_users: set[str] | None = None,
    allowed_channels: set[str] | None = None,
    default_timeout: int = 120,
) -> None:
    """Authorize and route incoming Slack messages."""
    if allowed_users and user_id not in allowed_users:
        await post(":no_entry_sign: You are not authorized to use this bot.")
        return
    if allowed_channels and channel_id and channel_id not in allowed_channels:
        await post(":no_entry_sign: This channel is not allowed to use this bot.")
        return

    text = " ".join(text.strip().split())
    if not text:
        await post(help_text(commands))
        return

    # built-in: list connected agents
    if hub is not None and text.lower() in ("/lan agents list", "/lan agents"):
        await reply_output(post, ":satellite_antenna: *Connected agents*", hub.agents_table())
        return

    for c in commands:
        m = c["regex"].match(text)
        if m:
            spec = c["spec"]
            tgt = f" on `{spec['target'].format(**m.groupdict())}`" if spec.get("target") else ""
            await post(f":hourglass_flowing_sand: Running `{spec['trigger']}`{tgt} ...")
            try:
                output = await execute_command(
                    spec,
                    m.groupdict(),
                    hub=hub,
                    default_timeout=default_timeout,
                )
            except Exception as exc:
                log.exception("Command %s failed", spec["name"])
                output = f"Error: {exc}"
            await reply_output(post, f":white_check_mark: `{spec['name']}`", output)
            return

    sub = help_text(commands, text)
    if sub.count("\n") > 1:
        await post(sub)
    else:
        await post(f":question: Unknown command `{text}`\n{help_text(commands)}")


# ---------------------------------------------------------------------------
# Slack Application Factory
# ---------------------------------------------------------------------------

def _make_post_respond(respond: Any) -> Callable[[str], Coroutine[Any, Any, None]]:
    async def post(text: str) -> None:
        await respond(text=text)

    return post


def create_bot_app(config: BotConfig | None = None) -> tuple[AsyncApp, Hub | None, list[dict[str, Any]]]:
    """Factory creating and wiring the Slack AsyncApp and optional Hub."""
    if config is None:
        config = BotConfig.from_env()

    hub: Hub | None = None
    if config.hub_enabled:
        hub = Hub(host=config.hub_host, port=config.hub_port, token=config.agent_token or None)
        if not config.agent_token:
            log.warning("AGENT_TOKEN is not set - any host on your LAN can register an agent!")

    commands = load_commands(config.commands_config_path)

    # Allow instantiation without token for testing
    token = config.slack_bot_token or "mock-test-bot-token"
    app = AsyncApp(token=token)

    @app.command("/lan")
    async def slash_lan(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await handle_command(
            f"/lan {command.get('text', '')}",
            command.get("user_id", ""),
            command.get("channel_id", ""),
            _make_post_respond(respond),
            commands=commands,
            hub=hub,
            allowed_users=config.allowed_users,
            allowed_channels=config.allowed_channels,
            default_timeout=config.shell_timeout,
        )

    @app.command("/home")
    async def slash_home(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await handle_command(
            f"/home {command.get('text', '')}",
            command.get("user_id", ""),
            command.get("channel_id", ""),
            _make_post_respond(respond),
            commands=commands,
            hub=hub,
            allowed_users=config.allowed_users,
            allowed_channels=config.allowed_channels,
            default_timeout=config.shell_timeout,
        )

    @app.event("message")
    async def on_message(event: dict[str, Any], say: Any) -> None:
        if event.get("subtype") or event.get("bot_id"):
            return
        text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
        if not text:
            return
        if not text.startswith("/"):
            text = "/" + text
        ts = event.get("ts")

        async def post(t: str) -> None:
            await say(text=t, thread_ts=ts)

        await handle_command(
            text,
            event.get("user", ""),
            event.get("channel", ""),
            post,
            commands=commands,
            hub=hub,
            allowed_users=config.allowed_users,
            allowed_channels=config.allowed_channels,
            default_timeout=config.shell_timeout,
        )

    return app, hub, commands


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def format_bot_preflight_error(issues: list[tuple[str, str, str]]) -> str:
    """Format bot configuration issues into a clean, professional diagnostic report."""
    width = 76
    sep = "=" * width
    lines = [
        sep,
        "[SHANGO-BOT] PREFLIGHT CONFIGURATION CHECK FAILED".center(width),
        sep,
        "The bot cannot start because required configuration parameters are missing",
        "or invalid. Please review the following issues:",
        "",
    ]
    for name, desc, fix in issues:
        lines.append(f"  * {name}:")
        lines.append(f"      Problem:    {desc}")
        lines.append(f"      Resolution: {fix}")
        lines.append("")

    lines.extend([
        "Slack App Setup Checklist:",
        "  1. Go to https://api.slack.com/apps and select your app.",
        "  2. Install app to workspace to obtain SLACK_BOT_TOKEN (xoxb-...).",
        "  3. Enable Socket Mode and generate SLACK_APP_TOKEN (xapp-...) with 'connections:write'.",
        "  4. Set environment variables or supply them via .env / docker-compose.yml.",
        sep,
    ])
    return "\n".join(lines)


async def main() -> None:
    config = BotConfig.from_env()
    config.validate()

    app, hub, _ = create_bot_app(config)
    tasks: list[asyncio.Task] = []
    if hub is not None:
        tasks.append(asyncio.create_task(hub.run()))

    handler = AsyncSocketModeHandler(app, config.slack_app_token)
    tasks.append(asyncio.create_task(handler.start_async()))
    log.info("Starting Socket Mode handler (outbound WebSocket only - NAT friendly)")
    await asyncio.gather(*tasks)


def run_main() -> None:
    """Entry point for shango-bot with preflight error handling and clean exit."""
    try:
        asyncio.run(main())
    except BotConfigurationError as exc:
        print(format_bot_preflight_error(exc.issues), file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        log.info("Bot stopped by user (Ctrl+C).")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        log.critical("Fatal bot runtime error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    run_main()
