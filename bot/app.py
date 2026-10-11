"""cmdiqo-bot - manage your local network from Slack, even behind NAT.

Uses Slack Socket Mode (outbound WebSocket only) so no ports need to be exposed
to the internet. Also embeds a Hub server: LAN agents (Windows/Linux) connect
OUTBOUND to the hub so you can run commands on any host from Slack.

Commands are defined in a YAML file under `command:` mappings:
  - python  - a Python function inside the container or on a remote agent
  - shell   - a shell command on Linux agents (or local container)
  - ps1     - a PowerShell command on Windows agents
Commands with both `shell` and `ps1` run cross-platform based on agent OS.
"""

from __future__ import annotations

import asyncio
import importlib
import json
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
log = logging.getLogger("cmdiqo-bot")

MAX_BLOCK = 3500  # Slack message limit is ~4000 chars
SAFE_PARAM_RE = re.compile(r"^[a-zA-Z0-9_\-\.:]+$")
SAFE_SCHEDULE_RE = re.compile(
    r"^(?:all|(?:(?:(?:[A-Za-z]{1,2}(?:-[A-Za-z]{1,2})?|\*),\d{1,2}(?::\d{2})?(?:am|pm)?-\d{1,2}(?::\d{2})?(?:am|pm)?)(?:;\s*)?)+)$",
    re.IGNORECASE,
)
SAFE_MESSAGE_RE = re.compile(r"^[\w\s\.\,\!\?\-\:\;\(\)\'\"\/@#%+=]+$")
DEFAULT_COMMANDS_CONFIG = (
    "/config/commands.yaml"
    if os.path.exists("/config/commands.yaml")
    else (
        "/app/config/commands.yaml"
        if os.path.exists("/app/config/commands.yaml")
        else "config/commands.yaml"
    )
)
DEFAULT_SHELL_TIMEOUT = 120
DEFAULT_HUB_PORT = 8765
DEFAULT_BIND_HOST = "0.0.0.0"  # nosec: B104


def get_version() -> str:
    """Return current bot version from version.txt (single source of truth) or bundled _version.py."""
    # 1. Prefer version.txt if present (repository root or container paths)
    try:
        from pathlib import Path

        for candidate in (
            Path(__file__).resolve().parent.parent / "version.txt",  # repo root
            Path(__file__).resolve().parent / "version.txt",         # package directory
            Path("version.txt"),                                    # current working dir
            Path("/config/version.txt"),                            # container mounted config
            Path("/app/version.txt"),                               # container app dir
        ):
            if candidate.is_file():
                v = candidate.read_text(encoding="utf-8").strip()
                if v:
                    return v
    except OSError as exc:
        log.debug("Could not read version.txt candidate: %s", exc)

    # 2. Fall back to bundled module if running without version.txt (e.g. standalone binary)
    try:
        from bot._version import __version__

        return str(__version__)
    except ImportError:
        pass
    try:
        from ._version import __version__

        return str(__version__)
    except (ImportError, ValueError):
        pass
    try:
        import _version

        if hasattr(_version, "__version__"):
            return str(_version.__version__)
    except ImportError:
        pass
    return "1.0.0"


__version__ = get_version()


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
    agent_connection_notify: bool = True
    web_port: int | None = None
    webgui_port: int | None = None

    def __post_init__(self) -> None:
        if self.web_port is None and self.webgui_port is not None:
            self.web_port = self.webgui_port
        elif self.webgui_port is None and self.web_port is not None:
            self.webgui_port = self.web_port

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
        if self.web_port is not None and self.web_port <= 0:
            issues.append(
                (
                    "WEB_PORT",
                    f"Invalid WEB_PORT '{self.web_port}'",
                    "Must be a valid TCP port number (e.g. 8080)",
                )
            )
        if issues:
            raise BotConfigurationError(issues)

    @classmethod
    def from_env(cls) -> BotConfig:
        """Load configuration cleanly from environment variables and config files without import-time side effects."""
        config_notify = False
        config_notify_found = False
        for cfg_file in ("/config/config.json", "config.json", "config/config.json", os.getenv("CONFIG_FILE", "")):
            if cfg_file and os.path.exists(cfg_file):
                try:
                    with open(cfg_file, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        if isinstance(data, dict) and "agent_connection_notify" in data:
                            val = data["agent_connection_notify"]
                            config_notify = str(val).lower() in ("true", "1", "yes", "on")
                            config_notify_found = True
                except (OSError, json.JSONDecodeError):
                    pass

        env_notify = os.getenv("AGENT_NOTIFICATION_NOTIFY") or os.getenv("AGENT_CONNECTION_NOTIFY")
        if env_notify is not None:
            agent_notify = env_notify.strip().lower() not in ("false", "0", "no", "off")
        else:
            agent_notify = config_notify if config_notify_found else True

        web_val = (os.getenv("WEB_PORT") or os.getenv("WEBGUI") or "").strip()
        web_port = int(web_val) if web_val.isdigit() and int(web_val) > 0 else None

        hub_port_str = os.getenv("HUB_PORT", str(DEFAULT_HUB_PORT))
        try:
            hub_port = int(hub_port_str)
        except ValueError:
            hub_port = -1

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
            hub_port=hub_port,
            agent_token=os.getenv("AGENT_TOKEN", ""),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            slack_app_token=os.getenv("SLACK_APP_TOKEN", ""),
            agent_connection_notify=agent_notify,
            web_port=web_port,
            webgui_port=web_port,
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
            name = m.group(1)
            if name in ("schedule", "time", "times"):
                out += rf"(?P<{name}>[a-zA-Z0-9_\-\.:,;]+)"
            elif name in ("message", "msg", "text"):
                out += rf"(?P<{name}>.+)"
            else:
                out += rf"(?P<{name}>[a-zA-Z0-9_\-\.:]+)"
        else:
            out += re.escape(part)
    return re.compile(out + r"\s*$", re.IGNORECASE)


def validate_params(params: dict[str, str]) -> None:
    """Validate that parameters do not contain shell injection characters."""
    for key, val in params.items():
        if key in ("schedule", "time", "times"):
            if not SAFE_SCHEDULE_RE.match(val):
                raise ValueError(
                    f"Invalid parameter value for '{key}': '{val}' contains unsafe schedule characters."
                )
        elif key in ("message", "msg", "text"):
            if not SAFE_MESSAGE_RE.match(val):
                raise ValueError(
                    f"Invalid parameter value for '{key}': contains unsafe message characters."
                )
        elif not SAFE_PARAM_RE.match(val):
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

    global_config = data.get("config")
    commands: list[dict[str, Any]] = []
    for spec in data.get("commands", []):
        spec.setdefault("name", spec["trigger"])
        spec.setdefault("description", "")
        if "config" not in spec and global_config is not None:
            spec["config"] = global_config

        # Normalize and validate 'command' mapping
        if "command" not in spec:
            cmd_map: dict[str, str] = {}
            kind = spec.get("kind")
            if kind == "python" and spec.get("handler"):
                cmd_map["python"] = spec["handler"]
            elif kind == "shell" and spec.get("run"):
                cmd_map["shell"] = spec["run"]
            elif kind == "ps1" and spec.get("run"):
                cmd_map["ps1"] = spec["run"]
            if spec.get("windows_run"):
                cmd_map["ps1"] = spec["windows_run"]
            spec["command"] = cmd_map
        elif isinstance(spec["command"], str):
            spec["command"] = {"shell": spec["command"]}
        elif not isinstance(spec["command"], dict):
            raise ValueError(
                f"Command '{spec.get('name')}' has invalid 'command' field; must be mapping of python | shell | ps1."
            )

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


def _run_python(spec: dict[str, Any], params: dict[str, Any]) -> str:
    handler = spec.get("handler")
    if not handler and isinstance(spec.get("command"), dict):
        handler = spec["command"].get("python")
    if not handler:
        raise ValueError(f"No python handler defined for command '{spec.get('name')}'")
    module_name, func_name = handler.split(":", 1)
    module = importlib.import_module(f"bot.{module_name}")
    func = getattr(module, func_name)
    return str(func(params))


async def _run_python_handler(
    spec: dict[str, Any], params: dict[str, Any], hub: Hub | None = None
) -> str:
    """Execute a Python handler function, passing hub and config if accepted and awaiting if coroutine."""
    handler = spec.get("handler")
    if not handler and isinstance(spec.get("command"), dict):
        handler = spec["command"].get("python")
    if not handler:
        raise ValueError(f"No python handler defined for command '{spec.get('name')}'")
    module_name, func_name = handler.split(":", 1)
    module = importlib.import_module(f"bot.{module_name}")
    func = getattr(module, func_name)

    import inspect

    merged_params = dict(params) if isinstance(params, dict) else {}
    if spec.get("name"):
        merged_params["_spec_name"] = spec["name"]
    if spec.get("trigger"):
        merged_params["_spec_trigger"] = spec["trigger"]
    if "config" in spec:
        merged_params["_config"] = spec["config"]
        merged_params["_spec_config"] = spec["config"]

    sig = inspect.signature(func)
    kwargs: dict[str, Any] = {}
    if "hub" in sig.parameters:
        kwargs["hub"] = hub
    if "config" in sig.parameters:
        kwargs["config"] = spec.get("config")

    if inspect.iscoroutinefunction(func):
        res = await func(merged_params, **kwargs)
    else:
        res = await asyncio.to_thread(func, merged_params, **kwargs)
    return str(res)


def format_params(
    template: str, params: dict[str, Any] | None = None, config: Any = None
) -> str:
    """Format template string by substituting parameter keys ({name}) and {config.<key>},

    leaving PowerShell scriptblocks (${...}) and format specifiers ({0}, {1:yyyy...}) intact.
    """
    if not template:
        return ""
    if not params and not config:
        return template

    p_dict = params or {}

    def _replace(m: re.Match[str]) -> str:
        k = m.group(1)
        if k in p_dict:
            return str(p_dict[k])
        if k.startswith("config.") and isinstance(config, dict):
            sub_k = k[7:]
            if sub_k in config:
                return str(config[sub_k])
        if k == "config" and config is not None and not isinstance(config, (dict, list)):
            return str(config)
        return m.group(0)

    return re.sub(
        r"\{([a-zA-Z_][a-zA-Z0-9_\.]*)\}",
        _replace,
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
            "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, VolumeName, "
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


def _standardize_uptime_if_applicable(spec: dict[str, Any], output: str) -> str:
    """Format uptime command output to standardized '<days>d <hours>h <m>m <s>s' with zero pads."""
    if (
        spec.get("name") == "host-uptime"
        or str(spec.get("trigger", "")).lower().endswith(" uptime")
    ) and (
        isinstance(output, str)
        and not output.startswith(("Error", "ℹ️", ":warning:", "kind 'ps1'"))
    ):
        try:
            from bot.webgui import parse_and_standardize_uptime_checked

            _, formatted, ok = parse_and_standardize_uptime_checked(output)
            if ok:
                return f"Uptime: {formatted}"
        except Exception as exc:  # noqa: BLE001
            log.debug("Failed parsing uptime: %s", exc)
    return output


async def _execute_command_raw(
    spec: dict[str, Any],
    params: dict[str, str],
    hub: Hub | None = None,
    default_timeout: int = 120,
) -> str:
    """Route a command to the container or to an agent, supporting OS-agnostic command definitions."""
    validate_params(params)
    config = spec.get("config")
    cwd = spec.get("cwd")
    if cwd:
        cwd = format_params(cwd, params, config=config)
    timeout = int(spec.get("timeout", default_timeout))

    # Extract command definitions
    cmd_def = spec.get("command")
    if isinstance(cmd_def, dict):
        py_cmd = cmd_def.get("python")
        sh_cmd = cmd_def.get("shell")
        ps1_cmd = cmd_def.get("ps1")
    elif isinstance(cmd_def, str):
        py_cmd, sh_cmd, ps1_cmd = None, cmd_def, None
    else:
        # Legacy fallback
        kind = spec.get("kind")
        py_cmd = spec.get("handler") if kind == "python" else None
        sh_cmd = spec.get("run") if kind == "shell" else None
        ps1_cmd = spec.get("run") if kind == "ps1" else None
        if spec.get("windows_run"):
            ps1_cmd = spec["windows_run"]

    # Target host determination:
    # If spec has explicit target, format it; otherwise use params["host"].
    agent_name = ""
    if spec.get("target"):
        agent_name = format_params(str(spec["target"]), params, config=config).strip()
    elif "host" in params:
        agent_name = str(params["host"]).strip()

    is_local_target = agent_name.lower() in ("local", "localhost", "container", "self")
    is_home_unregistered = agent_name.lower() == "home" and (
        hub is None
        or not isinstance(getattr(hub, "agents", None), dict)
        or "home" not in hub.agents
    )
    is_local = (not agent_name) or is_local_target or is_home_unregistered

    if is_local:
        if py_cmd:
            spec_copy = dict(spec)
            spec_copy["handler"] = py_cmd
            return await _run_python_handler(spec_copy, params, hub)

        if not agent_name and ps1_cmd:
            return "kind 'ps1' requires a 'target' pointing at a Windows agent"

        if ps1_cmd and os.name == "nt":
            cmdline = format_params(ps1_cmd, params, config=config)
            return await asyncio.to_thread(_run_ps1_local, cmdline, cwd, timeout)

        if sh_cmd:
            if os.name == "nt" and sh_cmd in WINDOWS_COMMAND_FALLBACKS:
                _, fallback_run = WINDOWS_COMMAND_FALLBACKS[sh_cmd]
                cmdline = format_params(fallback_run, params, config=config)
                return await asyncio.to_thread(_run_ps1_local, cmdline, cwd, timeout)
            cmdline = format_params(sh_cmd, params, config=config)
            return await asyncio.to_thread(_run_shell_local, cmdline, cwd, timeout)

        if ps1_cmd:
            return "kind 'ps1' requires a 'target' pointing at a Windows agent"

        return f":warning: Command '{spec.get('name')}' has no executable command."

    # Remote agent execution
    if py_cmd:
        spec_copy = dict(spec)
        spec_copy["handler"] = py_cmd
        return await _run_python_handler(spec_copy, params, hub)

    if hub is None:
        raise RuntimeError("hub is disabled (HUB_ENABLED=false)")

    agents_registry = getattr(hub, "agents", None)
    agent_info = agents_registry.get(agent_name) if isinstance(agents_registry, dict) else None
    platform_str = str(agent_info.get("platform", "")).lower() if agent_info else ""
    is_windows_agent = "win" in platform_str

    if is_windows_agent:
        if ps1_cmd:
            cmdline = format_params(ps1_cmd, params, config=config)
            return await hub.exec(agent_name, "ps1", cmdline, cwd=cwd, timeout=timeout)
        if sh_cmd and sh_cmd in WINDOWS_COMMAND_FALLBACKS:
            _, fallback_run = WINDOWS_COMMAND_FALLBACKS[sh_cmd]
            cmdline = format_params(fallback_run, params, config=config)
            return await hub.exec(agent_name, "ps1", cmdline, cwd=cwd, timeout=timeout)
        return "ℹ️ Sorry, this command is not available for the target OS"
    else:
        if sh_cmd:
            cmdline = format_params(sh_cmd, params, config=config)
            return await hub.exec(agent_name, "shell", cmdline, cwd=cwd, timeout=timeout)
        return "ℹ️ Sorry, this command is not available for the target OS"


async def execute_command(
    spec: dict[str, Any],
    params: dict[str, str],
    hub: Hub | None = None,
    default_timeout: int = 120,
) -> str:
    """Route a command to the container or to an agent, standardizing uptime if applicable."""
    res = await _execute_command_raw(spec, params, hub=hub, default_timeout=default_timeout)
    return _standardize_uptime_if_applicable(spec, res)


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
        "description": "Display running cmdiqo-bot version",
        "details": "Returns the installed semantic version string of the Cmdiqo bot container.",
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


def get_spec_category(spec: dict[str, Any]) -> str:
    """Infer category for command specification when category field is absent."""
    if spec.get("category"):
        return str(spec["category"]).lower()
    trig = spec.get("trigger", "").lower()
    if trig.startswith("/lan"):
        return "lan"
    if "docker" in trig:
        return "docker"
    if "service" in trig:
        return "services"
    cmd = spec.get("command") or {}
    if isinstance(cmd, dict) and "ps1" in cmd and "shell" not in cmd and "python" not in cmd:
        return "windows"
    if "win " in trig:
        return "windows"
    return "system"


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
    if req_topic.endswith(" help"):
        req_topic = req_topic[:-5].strip()
    elif req_topic.endswith(" ?"):
        req_topic = req_topic[:-2].strip()

    all_specs: list[dict[str, Any]] = [c["spec"] for c in commands] + BUILTIN_COMMANDS

    # 1. General Overview (no topic requested)
    if not req_topic:
        lines = [
            ":book: *Cmdiqo Command Manual*",
            "Manage your LAN, Docker services, and remote Windows/Linux machines directly from Slack.\n",
            "*Available Categories:*",
            "• *`lan`* — Network scanning, pinging, and Wake-on-LAN (`/help lan`)",
            "• *`host`* — All host-agnostic remote operations (`/help host`)",
            "• *`system`* — CPU, RAM, disk, and uptime monitoring (`/help system`)",
            "• *`docker`* — Container inspection, restart, logs, and stats (`/help docker`)",
            "• *`services`* — Systemd service management (`/help services`)",
            "• *`windows`* — Windows updates, services, memory, reboot, and shutdown (`/help windows`)",
        ]

        if aliases:
            lines.append("\n*Configured Shortcuts & Aliases:*")
            for a in aliases:
                desc = f" — {a['description']}" if a.get("description") else ""
                lines.append(f"• `{a['input']}` \u2192 `{a['output']}`{desc}")

        lines.append("\n*Quick Command Reference:*")
        categories: dict[str, list[dict[str, Any]]] = {}
        for s in all_specs:
            cat = get_spec_category(s)
            categories.setdefault(cat, []).append(s)

        for cat, specs in categories.items():
            cat_header = CATEGORY_LABELS.get(cat, f"*{cat.upper()}*")
            lines.append(f"\n{cat_header}")
            for s in specs:
                tgt = (
                    f" [on `{s['target']}`]"
                    if s.get("target")
                    else (" [on `{host}`]" if "{host}" in s.get("trigger", "") else "")
                )
                lines.append(f"• `{s['trigger']}`{tgt} — {s['description']}")

        lines.append(
            "\n:information_source: *Tip:* Type `/help <category>` (e.g. `/help lan`, `/help host`, `/help docker`) "
            "for detailed parameters, usage instructions, and copy-pasteable examples."
        )
        return "\n".join(lines)

    # 2. Filtered Topic / Category
    matching_specs: list[dict[str, Any]] = []
    for s in all_specs:
        cat = get_spec_category(s)
        trig = s["trigger"].lower()
        name = s.get("name", "").lower()
        desc = s.get("description", "").lower()

        # Match category, command prefix (e.g. /lan, /host), or keyword
        if (
            req_topic == cat
            or (req_topic == "host" and (s.get("target") or "{host}" in trig))
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
            if (s.get("target") or "{host}" in s.get("trigger", "")) and s not in matching_specs:
                matching_specs.append(s)

    if not matching_specs and not matching_aliases:
        return (
            f":question: No help found for topic `{req_topic}`.\n"
            f"Type `/help` to see all available categories and commands."
        )

    lines = [f":book: *Cmdiqo Help: `{req_topic}`*"]

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
            tgt = (
                f" [on `{s['target']}`]"
                if s.get("target")
                else (" [on `{host}`]" if "{host}" in s.get("trigger", "") else "")
            )
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
    webgui_state: Any = None,
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

    # Direct commands or category queries with trailing "help" or "?"
    # (e.g. "/lan help", "lan help", "/host help", "host help", "/home help", "/wol help", "/docker help")
    words = text.split()
    if len(words) >= 2 and words[-1].lower() in ("help", "?"):
        sub_topic = " ".join(words[:-1]).lstrip("/").strip()
        if sub_topic:
            await reply_help(post, help_text(commands, topic=sub_topic, aliases=aliases))
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
    if text.lower() in (
        "/docker", "docker",
        "/services", "services",
        "/windows", "windows",
        "/system", "system",
        "/parental", "parental",
        "/blockapp", "blockapp",
        "/appblock", "appblock",
    ):
        await reply_help(post, help_text(commands, topic=text.lstrip("/").lower(), aliases=aliases))
        return

    # Translate triggers matching configured aliases (e.g. /home -> /host home)
    if aliases:
        text = apply_aliases(text, aliases)

    # built-in: version check
    if text.lower() in ("/lan version", "/host version"):
        await reply_output(
            post,
            ":information_source: *Cmdiqo Version*",
            f"cmdiqo-bot v{get_version()}",
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
            target_name = (
                m.groupdict().get("host")
                or (format_params(spec["target"], m.groupdict()) if spec.get("target") else "")
            )
            tgt = f" on `{target_name}`" if target_name else ""
            await post(f":hourglass_flowing_sand: Running `{text}`{tgt} ...")
            try:
                output = await execute_command(
                    spec,
                    m.groupdict(),
                    hub=hub,
                    default_timeout=default_timeout,
                )
                if webgui_state is not None and (spec.get("name") == "host-uptime" or str(spec.get("trigger", "")).lower().endswith(" uptime")):
                    try:
                        from bot.webgui import parse_and_standardize_uptime_checked

                        boot_epoch, formatted, ok = parse_and_standardize_uptime_checked(output)
                        if ok and target_name:
                            webgui_state.update_agent_uptime(target_name, boot_epoch, formatted)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Failed updating webgui agent uptime: %s", exc)
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
    webgui_state: Any = None,
) -> tuple[AsyncApp, Hub | None, list[dict[str, Any]]]:
    """Factory creating and wiring the Slack AsyncApp and optional Hub."""
    if config is None:
        config = BotConfig.from_env()

    hub: Hub | None = None
    if config.hub_enabled:
        hub = Hub(
            host=config.hub_host,
            port=config.hub_port,
            token=config.agent_token or None,
            version=__version__,
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

    active_channels: set[str] = set()

    if hub is not None:
        async def _probe_agent_uptime(
            agent_name: str, hello_msg: dict[str, Any]
        ) -> None:
            if webgui_state is None:
                return
            platform_str = str(hello_msg.get("platform", "")).lower()
            is_windows = "win" in platform_str
            try:
                if is_windows:
                    cmd = (
                        "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; $u = (Get-Date) - $b; "
                        "'Uptime: {0}d {1}h {2}m {3}s (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f [int]$u.TotalDays, $u.Hours, $u.Minutes, $u.Seconds, $b"
                    )
                    raw = await hub.exec(agent_name, "ps1", cmd, timeout=10)
                else:
                    raw = await hub.exec(agent_name, "shell", "uptime", timeout=10)
                from bot.webgui import parse_and_standardize_uptime_checked

                boot_epoch, formatted, ok = parse_and_standardize_uptime_checked(
                    raw, "win" if is_windows else "linux"
                )
                if ok:
                    webgui_state.update_agent_uptime(agent_name, boot_epoch, formatted)
            except Exception as exc:  # noqa: BLE001
                log.debug("Initial uptime probe for '%s' failed: %s", agent_name, exc)

        async def _on_hub_agent_event(event_type: str, name: str, hello: dict[str, Any]) -> None:
            if webgui_state is not None:
                if event_type == "connected":
                    addr = ""
                    if isinstance(getattr(hub, "agents", None), dict) and name in hub.agents:
                        addr = str(hub.agents[name].get("addr", ""))
                    webgui_state.register_agent(name, hello, addr=addr)
                    asyncio.create_task(_probe_agent_uptime(name, hello))
                elif event_type == "disconnected":
                    webgui_state.unregister_agent(name)

            agent_notify = hello.get("agent_connection_notify") if isinstance(hello, dict) else None
            if agent_notify is False or (agent_notify is None and not config.agent_connection_notify):
                return

            status_text = f"🟢 Agent {name} connected" if event_type == "connected" else f"🔴 Agent {name} just disconnected"
            msg = f"Agent notification: {status_text}"
            log.info("%s", msg)

            target_channels = set(config.allowed_channels) if config.allowed_channels else set(active_channels)

            if not target_channels:
                try:
                    resp = await app.client.users_conversations(types="public_channel,private_channel", limit=50)
                    ch_list = []
                    if hasattr(resp, "data") and isinstance(resp.data, dict):
                        ch_list = resp.data.get("channels", [])
                    elif hasattr(resp, "get") or isinstance(resp, dict):
                        ch_list = resp.get("channels", [])
                    for ch_info in ch_list:
                        if isinstance(ch_info, dict) and "id" in ch_info:
                            target_channels.add(ch_info["id"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("Could not auto-discover Slack channels via users_conversations: %s", exc)

            if not target_channels:
                try:
                    resp = await app.client.conversations_list(types="public_channel", limit=20)
                    ch_list = []
                    if hasattr(resp, "data") and isinstance(resp.data, dict):
                        ch_list = resp.data.get("channels", [])
                    elif hasattr(resp, "get") or isinstance(resp, dict):
                        ch_list = resp.get("channels", [])
                    for ch_info in ch_list:
                        if isinstance(ch_info, dict) and "id" in ch_info:
                            target_channels.add(ch_info["id"])
                            break
                except Exception as exc:  # noqa: BLE001
                    log.warning("Could not auto-discover public channels via conversations_list: %s", exc)

            if target_channels:
                for ch in target_channels:
                    try:
                        await app.client.chat_postMessage(channel=ch, text=msg)
                        log.debug("Successfully posted agent event notification to channel %s: %s", ch, msg)
                    except Exception as exc:  # noqa: BLE001
                        if "not_in_channel" in str(exc):
                            try:
                                await app.client.conversations_join(channel=ch)
                                await app.client.chat_postMessage(channel=ch, text=msg)
                                log.debug("Auto-joined channel %s and posted agent notification: %s", ch, msg)
                                continue
                            except Exception as join_exc:  # noqa: BLE001
                                log.warning("Could not auto-join channel %s: %s", ch, join_exc)
                        log.warning("Could not post agent event notification to %s: %s", ch, exc)
            else:
                log.warning("Could not auto-discover any Slack channel for agent notification. Invite the bot to a channel or specify ALLOWED_CHANNELS.")

        hub.on_agent_event = _on_hub_agent_event

    def _record_channel(command: dict[str, Any]) -> None:
        ch_id = command.get("channel_id", "")
        if ch_id:
            active_channels.add(ch_id)

    @app.command("/host")
    async def slash_host(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        _record_channel(command)
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
            webgui_state=webgui_state,
        )

    @app.command("/lan")
    async def slash_lan(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        _record_channel(command)
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
            webgui_state=webgui_state,
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
            webgui_state=webgui_state,
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
            webgui_state=webgui_state,
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
            webgui_state=webgui_state,
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
            webgui_state=webgui_state,
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
        "[CMDIQO-BOT] PREFLIGHT CONFIGURATION CHECK FAILED".center(width),
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

    log.info("Starting Cmdiqo Bot / Hub v%s", __version__)

    webgui_state = None
    if config.web_port is not None:
        from bot.webgui import HubWebLogHandler, WebGuiState, start_webgui_thread

        webgui_state = WebGuiState()
        hub_log_handler = HubWebLogHandler(webgui_state)
        hub_log_handler.setLevel(logging.DEBUG)
        configured_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        for h in logging.getLogger().handlers:
            if h is not hub_log_handler and h.level == logging.NOTSET:
                h.setLevel(configured_log_level)
        logging.getLogger("cmdiqo-bot").setLevel(logging.DEBUG)
        logging.getLogger().addHandler(hub_log_handler)
        start_webgui_thread(
            webgui_state, port=config.web_port, bot_version=__version__
        )
        log.info("Cmdiqo Hub v%s WebGUI active on http://0.0.0.0:%d", __version__, config.web_port)

    app, hub, _ = create_bot_app(config, webgui_state=webgui_state)
    tasks: list[asyncio.Task] = []
    if hub is not None:
        tasks.append(asyncio.create_task(hub.run()))

    handler = AsyncSocketModeHandler(app, config.slack_app_token)
    tasks.append(asyncio.create_task(handler.start_async()))
    log.info("Starting Socket Mode handler v%s (outbound WebSocket only - NAT friendly)", __version__)
    await asyncio.gather(*tasks)


def print_bot_help() -> None:
    """Print command-line help for cmdiqo-bot."""
    print("""Usage: cmdiqo-bot [OPTIONS]

  Cmdiqo Slack Bot & WebSocket Hub.

Options:
  -v, --version  Show the version and exit.
  -h, --help     Show this message and exit.

Environment Variables:
  SLACK_BOT_TOKEN   Slack Bot User OAuth Token (xoxb-...) [Required]
  SLACK_APP_TOKEN   Slack App-Level Token for Socket Mode (xapp-...) [Required]
  ALLOWED_USERS     Comma-separated authorized Slack member IDs (e.g. U0123ABCDEF)
  AGENT_TOKEN       Shared authentication token for remote agents
  WEB_PORT          Port for real-time WebGUI dashboard (e.g. 8080)
  HUB_PORT          WebSocket Hub listen port (default: 8765)
  HUB_ENABLED       Enable Hub server (default: true)
  COMMANDS_CONFIG   Path to commands.yaml (default: config/commands.yaml)
  LOG_LEVEL         Log verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO)
""")


def run_main() -> None:
    """Entry point for cmdiqo-bot with preflight error handling and clean exit."""
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("-v", "--version", "version"):
        print(f"cmdiqo-bot {get_version()}")
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("-h", "--help", "help"):
        print_bot_help()
        sys.exit(0)
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
