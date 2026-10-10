"""Slack Simulator (slacksim): Terminal-based emulator for cmdiqo-bot.

Simulates the Slack chat environment in a console REPL with full
support for Slack slash commands, aliases, permissions, and ANSI-rendered
mrkdwn output (bold, italics, code blocks in light gray, emojis).

Features:
    - Reuses the exact same command dispatch engine and YAML configuration.
    - Runs the embedded WebSocket Hub so real LAN agents (Windows & Linux)
      can connect and execute commands without needing Slack credentials.
    - Real-time display of in-progress notifications and execution results.
    - Switch simulated users and channels on the fly to test security policies.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path when invoked directly as a script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.app import (
    DEFAULT_BIND_HOST,
    DEFAULT_COMMANDS_CONFIG,
    DEFAULT_HUB_PORT,
    DEFAULT_SHELL_TIMEOUT,
    get_version,
    handle_command,
    load_aliases,
    load_commands,
)
from bot.hub import Hub, RemoteHubClient

try:
    from slacksim._version import __version__
except ImportError:
    try:
        from _version import __version__
    except ImportError:
        __version__ = "1.0.0"

try:
    from slacksim.line_reader import ConsoleLineReader, restore_terminal
    from slacksim.slack_formatter import (
        ANSI_BOLD,
        ANSI_CYAN,
        ANSI_DARK_GRAY,
        ANSI_GREEN,
        ANSI_MAGENTA,
        ANSI_RESET,
        ANSI_YELLOW,
        format_slack_to_ansi,
    )
except (ImportError, ValueError, AttributeError):
    from line_reader import ConsoleLineReader, restore_terminal
    from slack_formatter import (
        ANSI_BOLD,
        ANSI_CYAN,
        ANSI_DARK_GRAY,
        ANSI_GREEN,
        ANSI_MAGENTA,
        ANSI_RESET,
        ANSI_YELLOW,
        format_slack_to_ansi,
    )

log = logging.getLogger("cmdiqo-slacksim")


@dataclass
class SimConfig:
    commands_config_path: str = DEFAULT_COMMANDS_CONFIG
    shell_timeout: int = DEFAULT_SHELL_TIMEOUT
    allowed_users: set[str] = field(default_factory=set)
    allowed_channels: set[str] = field(default_factory=set)
    hub_enabled: bool = True
    hub_host: str = DEFAULT_BIND_HOST
    hub_port: int = DEFAULT_HUB_PORT
    hub_url: str = ""
    agent_token: str = ""
    sim_user: str = "U_ADMIN"
    sim_channel: str = "C_GENERAL"
    use_color: bool = True

    @classmethod
    def from_env_and_args(cls, args: argparse.Namespace | None = None) -> SimConfig:
        """Build SimConfig merging environment variables and CLI arguments."""
        # 1. Start with env vars
        config_path = os.getenv("COMMANDS_CONFIG", DEFAULT_COMMANDS_CONFIG)
        shell_timeout = int(os.getenv("SHELL_TIMEOUT", str(DEFAULT_SHELL_TIMEOUT)))
        allowed_users = {
            x.strip()
            for x in os.getenv("ALLOWED_USERS", "").split(",")
            if x.strip()
        }
        allowed_channels = {
            x.strip()
            for x in os.getenv("ALLOWED_CHANNELS", "").split(",")
            if x.strip()
        }
        hub_enabled = os.getenv("HUB_ENABLED", "true").lower() == "true"
        hub_host = os.getenv("HUB_HOST", DEFAULT_BIND_HOST)
        hub_port = int(os.getenv("HUB_PORT", str(DEFAULT_HUB_PORT)))
        hub_url = os.getenv("HUB_URL", "")
        agent_token = os.getenv("AGENT_TOKEN", "")

        # Default simulated user to the first allowed user if configured
        sim_user = next(iter(allowed_users)) if allowed_users else "U_ADMIN"
        sim_channel = next(iter(allowed_channels)) if allowed_channels else "C_GENERAL"

        # 2. Apply CLI overrides if provided
        if args is not None:
            if getattr(args, "config", None):
                config_path = args.config
            if getattr(args, "port", None) is not None:
                hub_port = args.port
            if getattr(args, "hub_url", None):
                hub_url = args.hub_url
            if getattr(args, "token", None) is not None:
                agent_token = args.token
            if getattr(args, "no_hub", False):
                hub_enabled = False
            if getattr(args, "user", None):
                sim_user = args.user
            if getattr(args, "channel", None):
                sim_channel = args.channel
            use_color = not getattr(args, "no_color", False)
        else:
            use_color = True

        return cls(
            commands_config_path=config_path,
            shell_timeout=shell_timeout,
            allowed_users=allowed_users,
            allowed_channels=allowed_channels,
            hub_enabled=hub_enabled,
            hub_host=hub_host,
            hub_port=hub_port,
            hub_url=hub_url,
            agent_token=agent_token,
            sim_user=sim_user,
            sim_channel=sim_channel,
            use_color=use_color,
        )


class SlackSimulator:
    """Console-based interactive Slack simulator."""

    def __init__(self, config: SimConfig) -> None:
        self.config = config
        self.current_user = config.sim_user
        self.current_channel = config.sim_channel
        self.commands: list[dict[str, Any]] = []
        self.aliases: list[dict[str, Any]] = []
        self.hub: Hub | RemoteHubClient | None = None
        self._hub_task: asyncio.Task | None = None
        self._running = False
        self.line_reader = ConsoleLineReader()

    def load_specifications(self) -> None:
        """Load command and alias configurations from YAML."""
        cfg_path = self.config.commands_config_path
        if not os.path.exists(cfg_path):
            alt_path = Path(__file__).resolve().parent.parent / "config" / "commands.yaml"
            if alt_path.exists():
                cfg_path = str(alt_path)

        self.commands = load_commands(cfg_path)
        self.aliases = load_aliases(cfg_path)

    async def start(self) -> None:
        """Initialize Hub connection (client mode if running, or embedded server)."""
        self.load_specifications()
        self._running = True

        async def _on_agent_event(event_type: str, name: str, hello: dict[str, Any]) -> None:
            agent_notify = hello.get("agent_connection_notify") if isinstance(hello, dict) else None
            notify_enabled = agent_notify if agent_notify is not None else True
            if not notify_enabled:
                return

            icon = "🟢" if event_type == "connected" else "🔴"
            action_word = "connected" if event_type == "connected" else "just disconnected"
            msg = f"Agent notification: {icon} Agent {name} {action_word}"
            post_fn = self._post_callback()
            await post_fn(msg)

        if self.config.hub_enabled:
            target_url = self.config.hub_url or f"ws://127.0.0.1:{self.config.hub_port}"
            token = self.config.agent_token or None

            # 1. Try connecting as a client to an already running Hub server
            try:
                client = RemoteHubClient(url=target_url, token=token)
                await client.start(timeout=0.6)
                self.hub = client
                log.info("Connected directly to running Hub at %s", target_url)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("No existing Hub reachable at %s (%s); fallback to embedded Hub", target_url, exc)

            # 2. Fallback to starting local embedded Hub server
            try:
                hub = Hub(
                    host=self.config.hub_host,
                    port=self.config.hub_port,
                    token=token,
                )
                hub.on_agent_event = _on_agent_event
                self.hub = hub
                self._hub_task = asyncio.create_task(hub.run())
                log.info("Simulator embedded Hub started on ws://%s:%d", self.config.hub_host, self.config.hub_port)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not start embedded Hub: %s", exc)

    async def stop(self) -> None:
        """Stop simulator and cancel hub tasks or close client."""
        self._running = False
        if self.hub is not None:
            try:
                stop_fn = getattr(self.hub, "stop", None)
                if stop_fn is not None:
                    res = stop_fn()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as exc:  # noqa: BLE001
                log.debug("Hub shutdown exception: %s", exc)

        if self._hub_task is not None:
            self._hub_task.cancel()
            try:
                await self._hub_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("Hub task shutdown exception: %s", exc)
        restore_terminal()

    def _post_callback(self) -> Any:
        """Create a post callback that formats and prints bot messages to stdout."""
        use_color = self.config.use_color

        async def post(text: str) -> None:
            ts = time.strftime("%H:%M:%S")
            formatted = format_slack_to_ansi(text, use_color=use_color)
            if use_color:
                header = f"  {ANSI_DARK_GRAY}[{ts}]{ANSI_RESET} {ANSI_BOLD}{ANSI_MAGENTA}🤖 cmdiqo-bot:{ANSI_RESET}"
            else:
                header = f"  [{ts}] cmdiqo-bot:"
            print(f"{header} {formatted}")

        return post

    async def dispatch(self, text: str) -> bool:
        """Dispatch a single user input string. Returns False if simulator should exit."""
        cleaned = text.strip()
        if not cleaned:
            return True

        # Simulator meta-commands
        lower = cleaned.lower()
        if lower in ("exit", "quit", "/exit", "/quit"):
            print("\nExiting slacksim. Goodbye!")
            return False

        if lower == "/clear":
            # ANSI escape sequence to clear terminal and reset cursor position
            print("\033[2J\033[H", end="", flush=True)
            return True

        if lower.startswith(("/as ", "/user ")):
            parts = cleaned.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                self.current_user = parts[1].strip()
                status = "ALLOWED" if (not self.config.allowed_users or self.current_user in self.config.allowed_users) else "BLOCKED"
                print(f"  {ANSI_GREEN}✔{ANSI_RESET} Simulated user switched to {ANSI_BOLD}{self.current_user}{ANSI_RESET} (Access: {status})")
            return True

        if lower.startswith(("/in ", "/channel ")):
            parts = cleaned.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                self.current_channel = parts[1].strip()
                print(f"  {ANSI_GREEN}✔{ANSI_RESET} Simulated channel switched to {ANSI_BOLD}{self.current_channel}{ANSI_RESET}")
            return True

        if lower in ("/whoami", "/sim status"):
            print(f"\n  {ANSI_CYAN}Simulator Context:{ANSI_RESET}")
            print(f"    User:     {ANSI_BOLD}{self.current_user}{ANSI_RESET}")
            print(f"    Channel:  {ANSI_BOLD}{self.current_channel}{ANSI_RESET}")
            if self.hub:
                print(f"    Hub:      ws://{self.config.hub_host}:{self.config.hub_port}")
                print(f"    Agents:   {len(self.hub.agents)} connected ({', '.join(sorted(self.hub.agents)) or 'none'})")
            print()
            return True

        # Forward command through production handle_command
        # If input doesn't start with '/', prepend it like Slack slash commands
        cmd_text = cleaned if cleaned.startswith("/") else f"/{cleaned}"

        await handle_command(
            text=cmd_text,
            user_id=self.current_user,
            channel_id=self.current_channel,
            post=self._post_callback(),
            commands=self.commands,
            hub=self.hub,
            allowed_users=self.config.allowed_users or None,
            allowed_channels=self.config.allowed_channels or None,
            default_timeout=self.config.shell_timeout,
            aliases=self.aliases,
        )
        return True

    def print_banner(self) -> None:
        """Display an attractive terminal banner on startup."""
        ver = __version__ or get_version()
        color = self.config.use_color
        bold = ANSI_BOLD if color else ""
        cyan = ANSI_CYAN if color else ""
        green = ANSI_GREEN if color else ""
        yellow = ANSI_YELLOW if color else ""
        gray = ANSI_DARK_GRAY if color else ""
        reset = ANSI_RESET if color else ""

        print(f"""
{cyan}======================================================================{reset}
  {bold}⚡ Cmdiqo Slack Simulator (slacksim) v{ver}{reset}
  {gray}Interactive local console emulator for Slack commands & remote agents{reset}
{cyan}======================================================================{reset}
  {green}Active User:{reset}    {bold}{self.current_user}{reset} (type {yellow}/as <id>{reset} to change)
  {green}Active Channel:{reset} {bold}{self.current_channel}{reset} (type {yellow}/in <channel>{reset} to change)
  {green}WebSocket Hub:{reset}  {"ws://" + self.config.hub_host + ":" + str(self.config.hub_port) if self.config.hub_enabled else "disabled"}
  {green}Loaded Specs:{reset}   {len(self.commands)} commands, {len(self.aliases)} aliases

  {gray}Type any Slack command (e.g. /help, /lan devices list, /host AMD uptime){reset}
  {gray}Type /whoami for context, /clear to clear screen, exit to quit.{reset}
  {gray}Keys: Up/Down for command history, Esc to clear current message.{reset}
{cyan}----------------------------------------------------------------------{reset}
""")

    async def run_interactive(self) -> None:
        """Run the interactive console prompt loop."""
        self.print_banner()

        while self._running:
            prompt = f"{ANSI_CYAN}slack{ANSI_RESET} [{ANSI_GREEN}#{self.current_channel}{ANSI_RESET}] {ANSI_BOLD}@{self.current_user}{ANSI_RESET} > "
            try:
                # Use asyncio.to_thread with self.line_reader.readline so the Hub WebSocket server stays active and responsive
                user_input = await asyncio.to_thread(self.line_reader.readline, prompt)
            except (EOFError, KeyboardInterrupt):
                print("\nExiting slacksim. Goodbye!")
                break

            should_continue = await self.dispatch(user_input)
            if not should_continue:
                break
            print()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build command-line parser for slacksim."""
    parser = argparse.ArgumentParser(
        description="slacksim: Interactive Slack command console emulator for Cmdiqo."
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"cmdiqo-slacksim {__version__}",
    )
    parser.add_argument(
        "-c", "--command",
        help="Run a single Slack command non-interactively and exit (e.g. --command '/host AMD uptime').",
    )
    parser.add_argument(
        "-u", "--user",
        help="Simulate a specific Slack user ID (e.g. --user U0123ABCDEF).",
    )
    parser.add_argument(
        "--channel",
        help="Simulate a specific Slack channel ID (e.g. --channel C0123CHANNEL).",
    )
    parser.add_argument(
        "--config",
        help="Path to commands.yaml configuration file.",
    )
    parser.add_argument(
        "-H", "--hub-url",
        help="Target Hub WebSocket URL (e.g. ws://127.0.0.1:8765). Connects directly to running Hub.",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        help="Hub WebSocket port (default: 8765, env: HUB_PORT).",
    )
    parser.add_argument(
        "-t", "--token",
        help="Shared AGENT_TOKEN secret for agents.",
    )
    parser.add_argument(
        "--no-hub",
        action="store_true",
        help="Disable embedded WebSocket Hub server (local container commands only).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors and formatting.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Default: WARNING for clean REPL.",
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    """Async main routine."""
    logging.getLogger().setLevel(args.log_level.upper())
    config = SimConfig.from_env_and_args(args)
    sim = SlackSimulator(config)

    try:
        await sim.start()
        if args.command:
            # One-shot command execution mode
            await sim.dispatch(args.command)
            return 0
        await sim.run_interactive()
        return 0
    finally:
        await sim.stop()


def run_main() -> None:
    """Entry point for slacksim with preflight error handling."""
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(async_main(args)))
    except KeyboardInterrupt:
        print("\nSimulator stopped by user.")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"\nFatal simulator error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        restore_terminal()


if __name__ == "__main__":
    run_main()
