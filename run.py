#!/usr/bin/env python3
"""run.py: Shango local application runner and process supervisor.

Usage:
    python run.py          # Start both Hub (bot) and Agent concurrently
    python run.py hub      # Start the Hub / Slack Bot only
    python run.py agent    # Start the LAN Remote Agent only
    python run.py --help   # Show usage and options

Features:
    - Pure Python process management (no Docker required).
    - Automatically loads .env configuration if present.
    - Gracefully intercepts Ctrl+C (SIGINT / SIGTERM) and terminates
      all child processes safely without leaving orphan tasks.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = platform.system().lower() == "windows"

# ANSI colors for supervisor messages
COLOR_GREEN = "\033[0;32m"
COLOR_YELLOW = "\033[0;33m"
COLOR_RED = "\033[0;31m"
COLOR_CYAN = "\033[0;36m"
COLOR_RESET = "\033[0m"


def log_info(msg: str) -> None:
    print(f"{COLOR_GREEN}[run:INFO]{COLOR_RESET} {msg}")


def log_warn(msg: str) -> None:
    print(f"{COLOR_YELLOW}[run:WARN]{COLOR_RESET} {msg}")


def log_err(msg: str) -> None:
    print(f"{COLOR_RED}[run:ERROR]{COLOR_RESET} {msg}")


def load_dotenv(env_file: Path) -> None:
    """Load environment variables from a .env file without overwriting existing vars."""
    if not env_file.is_file():
        return
    log_info(f"Loading environment from {env_file}")
    try:
        content = env_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as exc:  # noqa: BLE001
        log_warn(f"Failed to read {env_file}: {exc}")


def terminate_process_safely(
    proc: subprocess.Popen, name: str, timeout: float = 4.0
) -> None:
    """Safely terminate a subprocess and ensure its entire process tree is stopped."""
    if proc.poll() is not None:
        return

    log_info(f"Terminating {name} (PID {proc.pid})...")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
            log_info(f"{name} (PID {proc.pid}) stopped cleanly.")
            return
        except subprocess.TimeoutExpired:
            log_warn(
                f"{name} (PID {proc.pid}) did not exit within {timeout}s; forcing termination..."
            )
    except ProcessLookupError:
        return

    # Forcible kill fallback
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.kill()
        proc.wait(timeout=2.0)
        log_info(f"{name} (PID {proc.pid}) terminated.")
    except Exception as exc:  # noqa: BLE001
        log_err(f"Could not kill {name} (PID {proc.pid}): {exc}")


def print_usage() -> None:
    """Print command-line help."""
    print(f"""
{COLOR_CYAN}Shango Process Runner{COLOR_RESET}

Usage:
    python run.py [COMMAND] [OPTIONS...]

Commands:
    {COLOR_GREEN}(none){COLOR_RESET}     Start both Hub (bot) and Agent concurrently
    {COLOR_GREEN}hub{COLOR_RESET}        Start the Shango Bot & WebSocket Hub only
    {COLOR_GREEN}agent{COLOR_RESET}      Start the Shango Remote Agent only
    {COLOR_GREEN}-h, --help{COLOR_RESET} Show this help message and exit

Examples:
    python run.py                  # Launch both services with graceful Ctrl+C shutdown
    python run.py hub              # Run bot only
    python run.py agent            # Run agent only
    python run.py agent --help     # Pass options directly to agent

Termination:
    Press {COLOR_YELLOW}Ctrl+C{COLOR_RESET} at any time to safely shut down running services.
""")


def run_single(script_path: Path, name: str, extra_args: list[str]) -> int:
    """Run a single service and supervise until exit or interrupt."""
    cmd = [sys.executable, str(script_path)] + extra_args
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)

    log_info(f"Starting {name} ({' '.join(cmd)})...")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)

    try:
        return proc.wait()
    except KeyboardInterrupt:
        print()
        log_warn(f"Interrupt received. Shutting down {name}...")
        terminate_process_safely(proc, name)
        return 0


def run_both(extra_args: list[str]) -> int:
    """Run both Hub and Agent concurrently with unified Ctrl+C supervision."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)

    hub_script = ROOT / "bot" / "app.py"
    agent_script = ROOT / "agent" / "agent.py"

    # Default HUB_URL for local agent if not explicitly defined
    hub_port = env.get("HUB_PORT", "8765")
    if "HUB_URL" not in env:
        env["HUB_URL"] = f"ws://127.0.0.1:{hub_port}"
        log_info(f"Setting default HUB_URL={env['HUB_URL']} for local agent")

    # Ensure local commands.yaml path is set
    local_cmds = ROOT / "config" / "commands.yaml"
    if "COMMANDS_CONFIG" not in env and local_cmds.is_file():
        env["COMMANDS_CONFIG"] = str(local_cmds)

    processes: dict[str, subprocess.Popen] = {}

    def shutdown_all(*_args) -> NoReturn:
        print()
        log_warn(
            "Interrupt received (Ctrl+C). Shutting down all Shango services safely..."
        )
        for name, proc in list(processes.items()):
            terminate_process_safely(proc, name)
        log_info("All services stopped.")
        sys.exit(0)

    # Register OS signals
    signal.signal(signal.SIGINT, shutdown_all)
    signal.signal(signal.SIGTERM, shutdown_all)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, shutdown_all)

    # 1. Start Hub / Bot
    log_info("Starting Shango Bot & Hub server...")
    hub_cmd = [sys.executable, str(hub_script)]
    hub_proc = subprocess.Popen(hub_cmd, cwd=str(ROOT), env=env)
    processes["Hub"] = hub_proc

    # Wait briefly for Hub to initialize
    time.sleep(1.5)
    if hub_proc.poll() is not None:
        log_err(f"Hub server exited prematurely with code {hub_proc.returncode}.")
        return hub_proc.returncode

    # 2. Start Agent
    log_info("Starting Shango Remote Agent...")
    agent_cmd = [sys.executable, str(agent_script)] + extra_args
    agent_proc = subprocess.Popen(agent_cmd, cwd=str(ROOT), env=env)
    processes["Agent"] = agent_proc

    log_info(
        f"{COLOR_GREEN}Both services are running.{COLOR_RESET} Press {COLOR_YELLOW}Ctrl+C{COLOR_RESET} to stop."
    )

    # 3. Supervise processes
    try:
        while True:
            time.sleep(0.5)
            # Check if any process died
            for name, proc in list(processes.items()):
                code = proc.poll()
                if code is not None:
                    log_warn(f"{name} exited with code {code}.")
                    # Terminate remaining processes
                    for other_name, other_proc in list(processes.items()):
                        if other_proc != proc:
                            terminate_process_safely(other_proc, other_name)
                    return code
    except KeyboardInterrupt:
        shutdown_all()

    return 0


def main() -> None:
    args = sys.argv[1:]

    # Load project .env if present
    load_dotenv(ROOT / ".env")

    if not args:
        # No args: run both Hub and Agent
        sys.exit(run_both([]))

    cmd = args[0].lower()
    remaining = args[1:]

    if cmd in ("-h", "--help", "help"):
        print_usage()
        sys.exit(0)
    elif cmd == "hub":
        sys.exit(run_single(ROOT / "bot" / "app.py", "Hub", remaining))
    elif cmd == "agent":
        sys.exit(run_single(ROOT / "agent" / "agent.py", "Agent", remaining))
    else:
        # Unknown command or flags passed directly to run_both
        if cmd.startswith("-"):
            sys.exit(run_both(args))
        else:
            log_err(f"Unknown command '{cmd}'. Available commands: 'hub', 'agent'")
            print_usage()
            sys.exit(1)


if __name__ == "__main__":
    main()
