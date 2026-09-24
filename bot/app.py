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
DEFAULT_COMMANDS_CONFIG = (
    "/app/config/commands.yaml"
    if os.path.exists("/app/config/commands.yaml")
    else "config/commands.yaml"
)
DEFAULT_SHELL_TIMEOUT = 120
DEFAULT_HUB_PORT = 8765
DEFAULT_BIND_HOST = "0.0.0.0"  # nosec: B104


def get_version() -> str:
    """Return current bot version."""
    try:
        from ._version import __version__

        return __version__
    except ImportError:
        pass
    try:
        from bot._version import __version__

        return __version__
    except ImportError:
        pass
    return "1.0.0"


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
            issues.append(
                (
                    "SLACK_BOT_TOKEN",
                    "Slack Bot User OAuth Token is required",
                    "Set SLACK_BOT_TOKEN environment variable (starts with 'xoxb-')",
                )
            )
        if not self.slack_app_token:
            issues.append(
                (
                    "SLACK_APP_TOKEN",
                    "Slack App-Level Token is required for Socket Mode",
                    "Set SLACK_APP_TOKEN environment variable (starts with 'xapp-')",
                )
            )
        if self.hub_enabled and self.hub_port <= 0:
            issues.append(
                (
                    "HUB_PORT",
                    f"Invalid HUB_PORT '{self.hub_port}'",
                    "Must be a valid TCP port number (e.g. 8765)",
                )
            )
        if issues:
            raise BotConfigurationError(issues)

    @classmethod
    def from_env(cls) -> BotConfig:
        """Load configuration cleanly from environment variables without import-time side effects."""
        return cls(
            commands_config_path=os.getenv("COMMANDS_CONFIG", DEFAULT_COMMANDS_CONFIG),
            shell_timeout=int(os.getenv("SHELL_TIMEOUT", str(DEFAULT_SHELL_TIMEOUT))),
            allowed_users={
                x.strip()
                for x in os.getenv("ALLOWED_USERS", "").split(",")
                if x.strip()
            },
            allowed_channels={
                x.strip()
                for x in os.getenv("ALLOWED_CHANNELS", "").split(",")
                if x.strip()
            },
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
    """Convert a trigger template like '/host {host} docker restart {service}' to regex.

    Enforces safe identifier characters for placeholders to prevent command injection.
    """
    out = "^"
    for part in re.split(r"(\{\w+\})", trigger):
        m = re.fullmatch(r"\{(\w+)\}", part)
        if m:
            out += rf"(?P<{m.group(1)}>[a-zA-Z0-9_\-\.:]+)"
        else:
            out += re.escape(part)
    return re.compile(out + r"\s*$", re.IGNORECASE)


def validate_params(params: dict[str, str]) -> None:
    """Validate that parameters do not contain shell injection characters."""
    for key, val in params.items():
        if not SAFE_PARAM_RE.match(val):
            raise ValueError(
                f"Invalid parameter value for '{key}': '{val}' contains unsafe characters."
            )


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


def load_aliases(path: str) -> list[dict[str, Any]]:
    """Load command alias specifications from YAML configuration file."""
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    aliases: list[dict[str, Any]] = []
    for item in data.get("aliases", []):
        if isinstance(item, dict) and "input" in item and "output" in item:
            entry: dict[str, Any] = {
                "name": str(item.get("name", item["input"])),
                "input": str(item["input"]).strip(),
                "output": str(item["output"]).strip(),
            }
            if "description" in item:
                entry["description"] = str(item["description"])
            if "details" in item:
                entry["details"] = str(item["details"])
            if "examples" in item and isinstance(item["examples"], list):
                entry["examples"] = [str(x) for x in item["examples"]]
            aliases.append(entry)
    log.info("Loaded %d alias(es) from %s", len(aliases), path)
    return aliases


def apply_aliases(text: str, aliases: list[dict[str, str]]) -> str:
    """Translate command triggers according to configured aliases."""
    trimmed = text.strip()
    for alias in aliases:
        inp = alias["input"]
        out = alias["output"]
        if trimmed.lower() == inp.lower():
            return out
        if trimmed.lower().startswith(inp.lower() + " "):
            remainder = trimmed[len(inp) :].strip()
            return f"{out} {remainder}"
    return trimmed


# ---------------------------------------------------------------------------
# Execution backends
# ---------------------------------------------------------------------------


def _run_python(spec: dict[str, Any], params: dict[str, str]) -> str:
    module_name, func_name = spec["handler"].split(":", 1)
    module = importlib.import_module(f"bot.{module_name}")
    func = getattr(module, func_name)
    return str(func(params))


async def _run_python_handler(
    spec: dict[str, Any], params: dict[str, str], hub: Hub | None = None
) -> str:
    """Execute a Python handler function, passing hub if accepted and awaiting if coroutine."""
    module_name, func_name = spec["handler"].split(":", 1)
    module = importlib.import_module(f"bot.{module_name}")
    func = getattr(module, func_name)

    import inspect

    sig = inspect.signature(func)
    kwargs: dict[str, Any] = {}
    if "hub" in sig.parameters:
        kwargs["hub"] = hub

    if inspect.iscoroutinefunction(func):
        res = await func(params, **kwargs)
    else:
        res = await asyncio.to_thread(func, params, **kwargs)
    return str(res)


def format_params(template: str, params: dict[str, str]) -> str:
    """Format template string by substituting only known parameter keys ({name}),

    leaving PowerShell scriptblocks (${...}) and format specifiers ({0}, {1:yyyy...}) intact.
    """
    if not template or not params:
        return template or ""
    return re.sub(
        r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}",
        lambda m: params[m.group(1)] if m.group(1) in params else m.group(0),
        template,
    )


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


def _run_ps1_local(cmdline: str, cwd: str | None, timeout: int) -> str:
    exe = "powershell.exe" if os.name == "nt" else "pwsh"
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", cmdline],
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


WINDOWS_COMMAND_FALLBACKS: dict[str, tuple[str, str]] = {
    "uptime": (
        "ps1",
        (
            "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; $u = (Get-Date) - $b; "
            "'Uptime: {0}d {1}h {2}m {3}s (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f [int]$u.TotalDays, $u.Hours, $u.Minutes, $u.Seconds, $b"
        ),
    ),
    "free -h": (
        "ps1",
        (
            "Get-CimInstance Win32_OperatingSystem | Select-Object "
            "@{Name='Total_GB';Expression={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, "
            "@{Name='Free_GB';Expression={[math]::Round($_.FreePhysicalMemory/1MB,2)}}, "
            "@{Name='Used_GB';Expression={[math]::Round(($_.TotalVisibleMemorySize - $_.FreePhysicalMemory)/1MB,2)}}, "
            "@{Name='UsedPercent';Expression={[math]::Round((($_.TotalVisibleMemorySize - $_.FreePhysicalMemory)/$_.TotalVisibleMemorySize)*100,1)}} "
            "| Format-Table -AutoSize"
        ),
    ),
    "df -h": (
        "ps1",
        (
            'Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, VolumeName, '
            "@{Name='Total_GB';Expression={[math]::Round($_.Size/1GB,2)}}, "
            "@{Name='Free_GB';Expression={[math]::Round($_.FreeSpace/1GB,2)}}, "
            "@{Name='Used_GB';Expression={[math]::Round(($_.Size - $_.FreeSpace)/1GB,2)}}, "
            "@{Name='PercentFree';Expression={[math]::Round(($_.FreeSpace/$_.Size)*100,1)}} "
            "| Format-Table -AutoSize"
        ),
    ),
    "ps aux --sort=-%cpu | head -n 11": (
        "ps1",
        (
            "Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Id, ProcessName, "
            "@{Name='CPU_s';Expression={[math]::Round($_.CPU,2)}}, "
            "@{Name='RAM_MB';Expression={[math]::Round($_.WorkingSet/1MB,2)}} "
            "| Format-Table -AutoSize"
        ),
    ),
}


async def execute_command(
    spec: dict[str, Any],
    params: dict[str, str],
    hub: Hub | None = None,
    default_timeout: int = 120,
) -> str:
    """Route a command to the container or to an agent when `target` is set."""
    validate_params(params)
    kind = spec["kind"]
    raw_cmdline = spec.get("run")
    cwd = spec.get("cwd")
    if cwd:
        cwd = format_params(cwd, params)
    timeout = int(spec.get("timeout", default_timeout))

    target = spec.get("target")
    if target:
        agent_name = format_params(target, params)
        is_local_target = agent_name.lower() in (
            "local",
            "localhost",
            "container",
            "self",
        )
        is_home_unregistered = agent_name.lower() == "home" and (
            hub is None
            or not isinstance(getattr(hub, "agents", None), dict)
            or "home" not in hub.agents
        )
        if is_local_target or is_home_unregistered:
            if os.name == "nt":
                if spec.get("windows_run"):
                    raw_cmdline = spec["windows_run"]
                    kind = spec.get("windows_kind", "ps1")
                elif raw_cmdline in WINDOWS_COMMAND_FALLBACKS:
                    kind, raw_cmdline = WINDOWS_COMMAND_FALLBACKS[raw_cmdline]
            cmdline = format_params(raw_cmdline, params) if raw_cmdline else ""
            if kind == "ps1":
                return await asyncio.to_thread(_run_ps1_local, cmdline, cwd, timeout)
            if kind == "shell":
                return await asyncio.to_thread(_run_shell_local, cmdline, cwd, timeout)
            if kind == "python":
                return await _run_python_handler(spec, params, hub)

        if kind == "python":
            return await _run_python_handler(spec, params, hub)

        if hub is None:
            raise RuntimeError("hub is disabled (HUB_ENABLED=false)")

        # Target is a remote agent connected to the hub
        agents_registry = getattr(hub, "agents", None)
        if isinstance(agents_registry, dict) and agent_name in agents_registry:
            agent_info = agents_registry[agent_name]
            platform_str = str(agent_info.get("platform", "")).lower()
            if "win" in platform_str:
                if spec.get("windows_run"):
                    raw_cmdline = spec["windows_run"]
                    kind = spec.get("windows_kind", "ps1")
                elif raw_cmdline in WINDOWS_COMMAND_FALLBACKS:
                    kind, raw_cmdline = WINDOWS_COMMAND_FALLBACKS[raw_cmdline]

        cmdline = format_params(raw_cmdline, params) if raw_cmdline else ""
        return await hub.exec(agent_name, kind, cmdline, cwd=cwd, timeout=timeout)

    # Local command without target
    cmdline = format_params(raw_cmdline, params) if raw_cmdline else ""
    if kind == "ps1":
        return "kind 'ps1' requires a 'target' pointing at a Windows agent"
    if kind == "shell":
        return await asyncio.to_thread(_run_shell_local, cmdline, cwd, timeout)
    return await _run_python_handler(spec, params, hub)


# ---------------------------------------------------------------------------
# Dispatch & Formatting
# ---------------------------------------------------------------------------


CATEGORY_LABELS: dict[str, str] = {
    "lan": ":satellite_antenna: *LAN Operations & Wake-on-LAN*",
    "host": ":computer: *Host-Agnostic Remote Operations*",
    "system": ":bar_chart: *System Health & Resource Monitoring*",
    "docker": ":whale: *Docker Containers & Compose*",
    "services": ":gear: *Systemd Service Management*",
    "windows": ":desktop_computer: *Windows Agent & PowerShell*",
}

BUILTIN_COMMANDS: list[dict[str, Any]] = [
    {
        "trigger": "/lan agents list",
        "category": "lan",
        "description": "List all connected remote agents, OS, uptime, and LAN IPs",
        "details": "Queries the active Hub WebSocket session registry to display connected agent names, platforms, connection timestamps, and remote addresses.",
        "examples": ["/lan agents list"],
    },
    {
        "trigger": "/lan version",
        "category": "lan",
        "description": "Display running shango-bot version",
        "details": "Returns the installed semantic version string of the Shango bot container.",
        "examples": ["/lan version"],
    },
    {
        "trigger": "/help [category_or_command]",
        "category": "system",
        "description": "Show interactive help manual and dynamic recap of commands",
        "details": "Displays structured guidance, command syntax, parameters, and examples. Supports nested filtering by category (e.g. /help lan, /help docker) or keyword (e.g. /help wake).",
        "examples": ["/help", "/help lan", "/help host", "/help docker", "/help home"],
    },
]


def help_text(
    commands: list[dict[str, Any]],
    prefix: str | None = None,
    aliases: list[dict[str, Any]] | None = None,
    topic: str | None = None,
) -> str:
    """Generate dynamic, structured help recap for Slack with nested topic filtering."""
    req_topic = (topic or prefix or "").strip().lower()
    if req_topic.startswith("/"):
        req_topic = req_topic[1:].strip()

    all_specs: list[dict[str, Any]] = [c["spec"] for c in commands] + BUILTIN_COMMANDS

    # 1. General Overview (no topic requested)
    if not req_topic:
        lines = [
            ":book: *Shango Command Manual*",
            "Manage your LAN, Docker services, and remote Windows/Linux machines directly from Slack.\n",
            "*Available Categories:*",
            "• *`lan`* — Network scanning, pinging, and Wake-on-LAN (`/help lan`)",
            "• *`host`* — All host-agnostic remote operations (`/help host`)",
            "• *`system`* — CPU, RAM, disk, and uptime monitoring (`/help system`)",
            "• *`docker`* — Container inspection, restart, logs, and stats (`/help docker`)",
            "• *`services`* — Systemd service management (`/help services`)",
            "• *`windows`* — Windows updates, services, memory, and reboot (`/help windows`)",
        ]

        if aliases:
            lines.append("\n*Configured Shortcuts & Aliases:*")
            for a in aliases:
                desc = f" — {a['description']}" if a.get("description") else ""
                lines.append(f"• `{a['input']}` \u2192 `{a['output']}`{desc}")

        lines.append("\n*Quick Command Reference:*")
        categories: dict[str, list[dict[str, Any]]] = {}
        for s in all_specs:
            cat = s.get("category", "other").lower()
            categories.setdefault(cat, []).append(s)

        for cat, specs in categories.items():
            cat_header = CATEGORY_LABELS.get(cat, f"*{cat.upper()}*")
            lines.append(f"\n{cat_header}")
            for s in specs:
                tgt = f" [on `{s['target']}`]" if s.get("target") else ""
                lines.append(f"• `{s['trigger']}`{tgt} — {s['description']}")

        lines.append(
            "\n:information_source: *Tip:* Type `/help <category>` (e.g. `/help lan`, `/help host`, `/help docker`) "
            "for detailed parameters, usage instructions, and copy-pasteable examples."
        )
        return "\n".join(lines)

    # 2. Filtered Topic / Category
    matching_specs: list[dict[str, Any]] = []
    for s in all_specs:
        cat = s.get("category", "").lower()
        trig = s["trigger"].lower()
        name = s.get("name", "").lower()
        desc = s.get("description", "").lower()

        # Match category, command prefix (e.g. /lan, /host), or keyword
        if (
            req_topic == cat
            or (req_topic == "host" and s.get("target"))
            or trig.startswith(f"/{req_topic} ")
            or trig == f"/{req_topic}"
            or req_topic in trig
            or req_topic in name
            or req_topic in desc
        ):
            matching_specs.append(s)

    matching_aliases: list[dict[str, Any]] = []
    if aliases:
        for a in aliases:
            inp = a["input"].lower().lstrip("/")
            out = a["output"].lower()
            name = a.get("name", "").lower()
            if (
                req_topic in (inp, name)
                or req_topic in out
                or req_topic in a.get("description", "").lower()
            ):
                matching_aliases.append(a)

    if req_topic == "home":
        for s in all_specs:
            if s.get("target") and s not in matching_specs:
                matching_specs.append(s)

    if not matching_specs and not matching_aliases:
        return (
            f":question: No help found for topic `{req_topic}`.\n"
            f"Type `/help` to see all available categories and commands."
        )

    lines = [f":book: *Shango Help: `{req_topic}`*"]

    if matching_aliases:
        lines.append("\n*Matching Aliases:*")
        for a in matching_aliases:
            lines.append(f"• `{a['input']}` \u2192 `{a['output']}`")
            if a.get("details"):
                lines.append(f"  _{a['details']}_")
            if a.get("examples"):
                lines.append(
                    "  _Examples:_ " + ", ".join(f"`{ex}`" for ex in a["examples"])
                )

    if matching_specs:
        lines.append("\n*Commands:*")
        for s in matching_specs:
            tgt = f" [on `{s['target']}`]" if s.get("target") else ""
            lines.append(f"• `{s['trigger']}`{tgt} — *{s['description']}*")
            if s.get("details"):
                lines.append(f"  _{s['details']}_")
            if s.get("examples"):
                ex_str = "\n    ".join(f"• `{ex}`" for ex in s["examples"])
                lines.append(f"  _Examples:_\n    {ex_str}")

    return "\n".join(lines)


async def reply_help(
    post: Callable[[str], Coroutine[Any, Any, None]],
    text: str,
) -> None:
    """Send formatted help text to Slack, splitting across blocks if needed."""
    if len(text) <= MAX_BLOCK:
        await post(text)
        return

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for section in text.split("\n\n"):
        if current_len + len(section) + 2 > MAX_BLOCK and current:
            chunks.append("\n\n".join(current))
            current = [section]
            current_len = len(section)
        else:
            current.append(section)
            current_len += len(section) + 2

    if current:
        chunks.append("\n\n".join(current))

    for chunk in chunks:
        await post(chunk)


async def reply_output(
    post: Callable[[str], Coroutine[Any, Any, None]],
    header: str,
    output: str,
) -> None:
    """Post structured output back to Slack in chunks within the message size limit."""
    output = (output or "(no output)").replace("```", "\u200b```")
    chunks = [output[i : i + MAX_BLOCK] for i in range(0, len(output), MAX_BLOCK)] or [
        "(no output)"
    ]
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
    aliases: list[dict[str, Any]] | None = None,
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
        await reply_help(post, help_text(commands, aliases=aliases))
        return

    # built-in /help command with nested topic filtering
    if text.lower() == "/help" or text.lower().startswith("/help "):
        topic = text[5:].strip()
        await reply_help(
            post, help_text(commands, topic=topic if topic else None, aliases=aliases)
        )
        return

    # Direct category / root inquiries with no arguments
    if text.lower() in ("/lan", "lan"):
        await reply_help(post, help_text(commands, topic="lan", aliases=aliases))
        return
    if text.lower() in ("/host", "host"):
        await reply_help(post, help_text(commands, topic="host", aliases=aliases))
        return
    if text.lower() in ("/home", "home"):
        await reply_help(post, help_text(commands, topic="home", aliases=aliases))
        return
    if text.lower() in ("/wol", "wol"):
        await reply_help(post, help_text(commands, topic="wol", aliases=aliases))
        return

    # Translate triggers matching configured aliases (e.g. /home -> /host home)
    if aliases:
        text = apply_aliases(text, aliases)

    # built-in: version check
    if text.lower() in ("/lan version", "/host version"):
        await reply_output(
            post,
            ":information_source: *Shango Version*",
            f"shango-bot v{get_version()}",
        )
        return

    # built-in: list connected agents
    if hub is not None and text.lower() in (
        "/lan agents list",
        "/lan agents",
        "/host agents list",
        "/host agents",
    ):
        await reply_output(
            post, ":satellite_antenna: *Connected agents*", hub.agents_table()
        )
        return

    for c in commands:
        m = c["regex"].match(text)
        if m:
            spec = c["spec"]
            tgt = (
                f" on `{format_params(spec['target'], m.groupdict())}`"
                if spec.get("target")
                else ""
            )
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

    sub = help_text(commands, topic=text, aliases=aliases)
    if not sub.startswith(":question:"):
        await reply_help(post, sub)
    else:
        await reply_help(
            post,
            f":question: Unknown command `{text}`\n\n{help_text(commands, aliases=aliases)}",
        )


# ---------------------------------------------------------------------------
# Slack Application Factory
# ---------------------------------------------------------------------------


def _make_post_respond(respond: Any) -> Callable[[str], Coroutine[Any, Any, None]]:
    async def post(text: str) -> None:
        await respond(text=text)

    return post


def create_bot_app(
    config: BotConfig | None = None,
) -> tuple[AsyncApp, Hub | None, list[dict[str, Any]]]:
    """Factory creating and wiring the Slack AsyncApp and optional Hub."""
    if config is None:
        config = BotConfig.from_env()

    hub: Hub | None = None
    if config.hub_enabled:
        hub = Hub(
            host=config.hub_host, port=config.hub_port, token=config.agent_token or None
        )
        if not config.agent_token:
            log.warning(
                "AGENT_TOKEN is not set - any host on your LAN can register an agent!"
            )

    commands = load_commands(config.commands_config_path)
    aliases = load_aliases(config.commands_config_path)

    # Allow instantiation without token for testing
    token = config.slack_bot_token or "mock-test-bot-token"
    app = AsyncApp(token=token)

    @app.command("/host")
    async def slash_host(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await handle_command(
            f"/host {command.get('text', '')}",
            command.get("user_id", ""),
            command.get("channel_id", ""),
            _make_post_respond(respond),
            commands=commands,
            hub=hub,
            allowed_users=config.allowed_users,
            allowed_channels=config.allowed_channels,
            default_timeout=config.shell_timeout,
            aliases=aliases,
        )

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
            aliases=aliases,
        )

    @app.command("/help")
    async def slash_help(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await handle_command(
            f"/help {command.get('text', '')}",
            command.get("user_id", ""),
            command.get("channel_id", ""),
            _make_post_respond(respond),
            commands=commands,
            hub=hub,
            allowed_users=config.allowed_users,
            allowed_channels=config.allowed_channels,
            default_timeout=config.shell_timeout,
            aliases=aliases,
        )

    @app.command("/wol")
    async def slash_wol(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await handle_command(
            f"/wol {command.get('text', '')}",
            command.get("user_id", ""),
            command.get("channel_id", ""),
            _make_post_respond(respond),
            commands=commands,
            hub=hub,
            allowed_users=config.allowed_users,
            allowed_channels=config.allowed_channels,
            default_timeout=config.shell_timeout,
            aliases=aliases,
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
            aliases=aliases,
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
            aliases=aliases,
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

    lines.extend(
        [
            "Slack App Setup Checklist:",
            "  1. Go to https://api.slack.com/apps and select your app.",
            "  2. Install app to workspace to obtain SLACK_BOT_TOKEN (xoxb-...).",
            "  3. Enable Socket Mode and generate SLACK_APP_TOKEN (xapp-...) with 'connections:write'.",
            "  4. Set environment variables or supply them via .env / docker-compose.yml.",
            sep,
        ]
    )
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
