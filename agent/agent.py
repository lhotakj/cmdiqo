"""shango-agent: tiny agent that connects OUTBOUND to the shango-bot hub
and executes commands on this host on request.

Cross-platform: Windows (PowerShell via kind 'ps1', cmd/bash via 'shell') and
Linux/macOS (bash via 'shell').

Configuration priority:
    1. CLI flags (--hub-url, --token, --name)
    2. Environment variables (HUB_URL, AGENT_TOKEN, AGENT_NAME)
    3. JSON config file (--config or AGENT_CONFIG)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import socket
import sys
import threading
from dataclasses import dataclass
from typing import Any

import websockets

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("shango-agent")

IS_WINDOWS = platform.system().lower() == "windows"


def get_version() -> str:
    """Return current agent version."""
    try:
        import _version  # Sibling import in PyInstaller bundle or when cwd/sys.path is agent/

        return getattr(_version, "__version__", "1.0.0")
    except ImportError:
        pass
    try:
        from . import _version  # Relative import when used as agent.agent package

        return getattr(_version, "__version__", "1.0.0")
    except (ImportError, ValueError):
        pass
    try:
        from agent import _version  # Absolute import when repo root is in sys.path

        return getattr(_version, "__version__", "1.0.0")
    except ImportError:
        pass
    return "1.0.0"


class ConfigurationError(ValueError):
    """Raised when one or more required configuration settings are missing or invalid."""

    def __init__(self, issues: list[tuple[str, str, str]]) -> None:
        message = "; ".join(f"{name}: {desc}" for name, desc, _ in issues)
        super().__init__(message)
        self.issues = issues


@dataclass
class AgentConfig:
    hub_url: str
    token: str
    name: str
    reconnect_delay: float = 5.0

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load configuration from environment variables."""
        return cls.load(args=[])

    @classmethod
    def load(cls, args: list[str] | None = None) -> AgentConfig:
        """Load configuration with precedence: CLI flags > Env vars > Config file."""
        parser = argparse.ArgumentParser(description="shango-agent: LAN command agent")
        parser.add_argument(
            "-v", "--version", action="version", version=f"shango-agent {get_version()}"
        )
        parser.add_argument(
            "-u",
            "--hub-url",
            default=None,
            help="Hub WebSocket URL (e.g. ws://192.168.1.10:8765)",
        )
        parser.add_argument(
            "-t", "--token", default=None, help="Shared AGENT_TOKEN secret"
        )
        parser.add_argument(
            "-n", "--name", default=None, help="Agent hostname / target name"
        )
        parser.add_argument(
            "-c", "--config", default=None, help="Path to config JSON file"
        )
        parser.add_argument(
            "-d",
            "--reconnect-delay",
            type=float,
            default=None,
            help="Initial reconnect delay in seconds",
        )
        parser.add_argument(
            "--log-level",
            default=None,
            help="Logging level (DEBUG, INFO, WARNING, ERROR)",
        )
        parser.add_argument(
            "--service",
            action="store_true",
            help="Run as native Windows Service under SCM",
        )

        parsed, _ = parser.parse_known_args(args if args is not None else sys.argv[1:])

        if parsed.log_level:
            logging.getLogger().setLevel(parsed.log_level.upper())

        issues: list[tuple[str, str, str]] = []
        file_cfg: dict[str, Any] = {}
        config_path = parsed.config or os.environ.get("AGENT_CONFIG")

        # 1. Preflight check: Config file
        if config_path:
            if not os.path.exists(config_path):
                issues.append(
                    (
                        "CONFIG_FILE",
                        f"Configuration file not found at '{config_path}'",
                        "Ensure file exists or check path passed via --config / AGENT_CONFIG",
                    )
                )
            else:
                try:
                    with open(config_path, "r", encoding="utf-8-sig") as f:
                        file_cfg = json.load(f)
                except (json.JSONDecodeError, OSError) as exc:
                    issues.append(
                        (
                            "CONFIG_FILE",
                            f"Failed to read/parse JSON in '{config_path}': {exc}",
                            "Check file permissions and ensure the file is valid JSON",
                        )
                    )

        # 2. Preflight check: Hub URL
        hub_url = parsed.hub_url or os.environ.get("HUB_URL") or file_cfg.get("hub_url")
        if not hub_url:
            issues.append(
                (
                    "HUB_URL",
                    "Hub WebSocket URL is required",
                    "Provide via --hub-url, HUB_URL environment variable, or in config file",
                )
            )
        elif not hub_url.startswith(("ws://", "wss://")):
            issues.append(
                (
                    "HUB_URL",
                    f"Invalid WebSocket scheme for '{hub_url}'",
                    "Must begin with ws:// (unencrypted) or wss:// (encrypted), e.g. ws://192.168.1.10:8765",
                )
            )

        # 3. Preflight check: Shared secret token
        token = parsed.token or os.environ.get("AGENT_TOKEN") or file_cfg.get("token")
        if not token:
            issues.append(
                (
                    "AGENT_TOKEN",
                    "Shared secret token is required",
                    "Provide via --token, AGENT_TOKEN environment variable, or in config file",
                )
            )

        # 4. Preflight check: Reconnect delay
        delay_val = (
            parsed.reconnect_delay
            if parsed.reconnect_delay is not None
            else os.environ.get("RECONNECT_DELAY", file_cfg.get("reconnect_delay", 5.0))
        )
        try:
            reconnect_delay = float(delay_val)
            if reconnect_delay <= 0:
                raise ValueError("delay must be positive")
        except (ValueError, TypeError):
            issues.append(
                (
                    "RECONNECT_DELAY",
                    f"Invalid reconnect delay value '{delay_val}'",
                    "Must be a positive number of seconds (e.g. 5.0)",
                )
            )
            reconnect_delay = 5.0

        if issues:
            raise ConfigurationError(issues)

        name = (
            parsed.name
            or os.environ.get("AGENT_NAME")
            or file_cfg.get("name")
            or socket.gethostname()
        )
        return cls(
            hub_url=hub_url, token=token, name=name, reconnect_delay=reconnect_delay
        )


def build_argv(kind: str, cmd: str) -> list[str]:
    """Map a command kind to [executable, ...args, command-line]."""
    if kind == "ps1":
        exe = "powershell.exe" if IS_WINDOWS else "pwsh"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", cmd]
    if IS_WINDOWS:
        return ["cmd.exe", "/c", cmd]
    return ["bash", "-c", cmd]


async def execute(msg: dict[str, Any]) -> tuple[bool, int | None, str, str | None]:
    """Execute a command received from the hub.

    Returns:
        (ok, returncode, output, error)
    """
    kind = str(msg.get("kind", "shell"))
    cmd = str(msg.get("cmd", ""))
    cwd = msg.get("cwd") or None
    timeout = int(msg.get("timeout") or 120)

    try:
        proc = await asyncio.create_subprocess_exec(
            *build_argv(kind, cmd),
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
            await proc.wait()
            return False, -1, "", f"timed out after {timeout}s"

        output_str = out.decode("utf-8", errors="replace")
        ok = proc.returncode == 0
        return ok, proc.returncode, output_str, None
    except FileNotFoundError as exc:
        return False, 127, "", f"executable not found: {exc}"
    except OSError as exc:
        return False, 1, "", f"OS error: {exc}"


async def handle(ws: Any, raw: str) -> None:
    """Handle a raw message received from the hub."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(msg, dict) or msg.get("type") != "exec":
        return

    req_id = str(msg.get("id", "?"))
    kind = str(msg.get("kind", "shell"))
    cmd = str(msg.get("cmd", ""))
    timeout = int(msg.get("timeout") or 120)
    cwd = msg.get("cwd") or "(default)"

    log.info(
        "[%s] Received %s execution request: %s (timeout=%ds, cwd=%s)",
        req_id[:8],
        kind,
        cmd,
        timeout,
        cwd,
    )

    ok, code, output, error = await execute(msg)

    log.info(
        "[%s] Execution finished: ok=%s, code=%s, out_bytes=%d, error=%s",
        req_id[:8],
        ok,
        code,
        len(output),
        error or "none",
    )

    await ws.send(
        json.dumps(
            {
                "id": msg.get("id"),
                "type": "result",
                "ok": ok,
                "code": code,
                "output": output,
                "error": error,
            }
        )
    )


def connect(url: str, token: str) -> Any:
    """Establish a WebSocket connection with authorization header."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # websockets >= 14
        return websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        )
    except TypeError:
        # websockets 13 compatibility
        return websockets.connect(
            url,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        )


async def run_agent(
    config: AgentConfig | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Run agent reconnect loop."""
    if config is None:
        config = AgentConfig.load()

    delay = config.reconnect_delay
    while not (stop_event and stop_event.is_set()):
        try:
            async with connect(config.hub_url, config.token) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "name": config.name,
                            "version": get_version(),
                            "platform": f"{platform.system()}/{platform.release()}",
                        }
                    )
                )
                log.info(
                    "Connected to %s as '%s' (v%s)",
                    config.hub_url,
                    config.name,
                    get_version(),
                )
                delay = config.reconnect_delay  # reset delay on successful connection
                while not (stop_event and stop_event.is_set()):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        await handle(ws, raw)
                    except asyncio.TimeoutError:
                        continue
        except (
            websockets.ConnectionClosed,
            OSError,
            TimeoutError,
            asyncio.TimeoutError,
        ) as exc:
            if stop_event and stop_event.is_set():
                break
            log.warning("Connection lost: %s; retrying in %.1fs", exc, delay)
            slept = 0.0
            while slept < delay and not (stop_event and stop_event.is_set()):
                await asyncio.sleep(min(0.5, delay - slept))
                slept += 0.5
            delay = min(60.0, delay * 1.5)  # exponential backoff with ceiling


def format_preflight_error(issues: list[tuple[str, str, str]]) -> str:
    """Format configuration issues into a clean, professional diagnostic report."""
    width = 76
    sep = "=" * width
    lines = [
        sep,
        "[SHANGO-AGENT] PREFLIGHT CONFIGURATION CHECK FAILED".center(width),
        sep,
        "The agent cannot start because required configuration parameters are missing",
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
            "Quick setup alternatives:",
            "  - CLI:  shango-agent --hub-url ws://<HUB_IP>:8765 --token <AGENT_TOKEN>",
            "  - Env:  set HUB_URL=ws://<HUB_IP>:8765 && set AGENT_TOKEN=<AGENT_TOKEN>",
            "  - Help: shango-agent --help",
            sep,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """Main entry point for shango-agent with robust error handling and preflight checks."""
    effective_args = argv if argv is not None else sys.argv[1:]
    if "--service" in effective_args:
        if not IS_WINDOWS:
            print("[ERROR] The --service option is only supported on Windows.", file=sys.stderr)
            sys.exit(1)
        try:
            from agent.service_windows import run_as_windows_service
        except ImportError:
            from service_windows import run_as_windows_service

        async def _service_worker(stop_evt: threading.Event) -> None:
            cfg = AgentConfig.load(args=argv)
            await run_agent(cfg, stop_event=stop_evt)

        run_as_windows_service("ShangoAgent", _service_worker)
        return

    try:
        config = AgentConfig.load(args=argv)
        asyncio.run(run_agent(config))
    except ConfigurationError as exc:
        print(format_preflight_error(exc.issues), file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        log.info("Agent stopped by user (Ctrl+C).")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        log.critical("Fatal agent runtime error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
