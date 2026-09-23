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
from dataclasses import dataclass
from typing import Any

import websockets

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("shango-agent")

IS_WINDOWS = platform.system().lower() == "windows"


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
        parser.add_argument("-u", "--hub-url", default=None, help="Hub WebSocket URL (e.g. ws://192.168.1.10:8765)")
        parser.add_argument("-t", "--token", default=None, help="Shared AGENT_TOKEN secret")
        parser.add_argument("-n", "--name", default=None, help="Agent hostname / target name")
        parser.add_argument("-c", "--config", default=None, help="Path to config JSON file")
        parser.add_argument(
            "-d",
            "--reconnect-delay",
            type=float,
            default=None,
            help="Initial reconnect delay in seconds",
        )
        parser.add_argument("--log-level", default=None, help="Logging level (DEBUG, INFO, WARNING, ERROR)")

        parsed, _ = parser.parse_known_args(args if args is not None else sys.argv[1:])

        if parsed.log_level:
            logging.getLogger().setLevel(parsed.log_level.upper())

        file_cfg: dict[str, Any] = {}
        config_path = parsed.config or os.environ.get("AGENT_CONFIG")
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)

        hub_url = parsed.hub_url or os.environ.get("HUB_URL") or file_cfg.get("hub_url")
        if not hub_url:
            raise ValueError("HUB_URL is required (via --hub-url, HUB_URL env var, or config file)")

        token = parsed.token or os.environ.get("AGENT_TOKEN") or file_cfg.get("token")
        if not token:
            raise ValueError("AGENT_TOKEN is required (via --token, AGENT_TOKEN env var, or config file)")

        name = parsed.name or os.environ.get("AGENT_NAME") or file_cfg.get("name") or socket.gethostname()

        delay_val = (
            parsed.reconnect_delay
            if parsed.reconnect_delay is not None
            else os.environ.get("RECONNECT_DELAY", file_cfg.get("reconnect_delay", 5.0))
        )
        reconnect_delay = float(delay_val)

        return cls(hub_url=hub_url, token=token, name=name, reconnect_delay=reconnect_delay)


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
        ok = (proc.returncode == 0)
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

    ok, code, output, error = await execute(msg)
    await ws.send(
        json.dumps({
            "id": msg.get("id"),
            "type": "result",
            "ok": ok,
            "code": code,
            "output": output,
            "error": error,
        })
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


async def run_agent(config: AgentConfig | None = None) -> None:
    """Run agent reconnect loop."""
    if config is None:
        config = AgentConfig.load()

    delay = config.reconnect_delay
    while True:
        try:
            async with connect(config.hub_url, config.token) as ws:
                await ws.send(
                    json.dumps({
                        "type": "hello",
                        "name": config.name,
                        "platform": f"{platform.system()}/{platform.release()}",
                    })
                )
                log.info("Connected to %s as '%s'", config.hub_url, config.name)
                delay = config.reconnect_delay  # reset delay on successful connection
                async for raw in ws:
                    await handle(ws, raw)
        except (websockets.ConnectionClosed, OSError, TimeoutError, asyncio.TimeoutError) as exc:
            log.warning("Connection lost: %s; retrying in %.1fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(60.0, delay * 1.5)  # exponential backoff with ceiling


if __name__ == "__main__":
    asyncio.run(run_agent())
