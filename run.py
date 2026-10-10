#!/usr/bin/env python3
"""run.py: Cmdiqo local application runner and process supervisor.

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
import traceback
from pathlib import Path
from typing import NoReturn

import click

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
{COLOR_CYAN}Cmdiqo Process Runner{COLOR_RESET}

Usage:
    python run.py [COMMAND] [OPTIONS...]

Commands:
    {COLOR_GREEN}(none){COLOR_RESET}         Start both Hub (bot) and Agent concurrently
    {COLOR_GREEN}hub{COLOR_RESET}            Start the Cmdiqo Bot & WebSocket Hub only
    {COLOR_GREEN}agent{COLOR_RESET}          Start the Cmdiqo Remote Agent only
    {COLOR_GREEN}sim, slacksim{COLOR_RESET}  Start the interactive Slack emulator (no Slack tokens needed)
    {COLOR_GREEN}diag{COLOR_RESET}           Inspect Python environment, permissions, and configuration
    {COLOR_GREEN}-v, --version{COLOR_RESET}  Show bundled application version and exit
    {COLOR_GREEN}-h, --help{COLOR_RESET}     Show this help message and exit

Examples:
    python run.py                  # Launch both services with graceful Ctrl+C shutdown
    python run.py hub              # Run bot only
    python run.py agent            # Run agent only
    python run.py diag             # Diagnostic inspection of Python, permissions & tokens
    python run.py sim              # Start interactive Slack console emulator
    python run.py sim -c "/help"   # Execute command non-interactively in simulator
    python run.py agent --help     # Pass options directly to agent

Termination:
    Press {COLOR_YELLOW}Ctrl+C{COLOR_RESET} at any time to safely shut down running services.
""")


def diagnose_environment(target_script: Path | None = None) -> None:
    """Print comprehensive diagnostic information about python binary, permissions, and configuration."""
    print()
    log_info("=================== Cmdiqo Diagnostics ===================")
    print(f"  Platform:            {platform.platform()} ({platform.machine()})")
    print(f"  Python Version:      {sys.version.splitlines()[0]}")
    print(f"  Current Working Dir: {Path.cwd()}")
    print(f"  Repository Root:     {ROOT}")
    print(f"  sys.executable:      {sys.executable}")

    # Inspect executable
    exe_p = Path(sys.executable)
    if exe_p.exists():
        try:
            stat_info = exe_p.stat()
            mode_oct = oct(stat_info.st_mode)
            is_exec = os.access(sys.executable, os.X_OK)
            is_read = os.access(sys.executable, os.R_OK)
            is_symlink = exe_p.is_symlink()
            print(f"  Executable Stat:     mode={mode_oct}, readable={is_read}, executable={is_exec}")
            if is_symlink:
                try:
                    resolved = exe_p.resolve()
                    res_exists = resolved.exists()
                    res_exec = os.access(resolved, os.X_OK) if res_exists else False
                    print(f"  Executable Symlink:  -> {resolved} (target_exists={res_exists}, target_exec={res_exec})")
                except Exception as exc:  # noqa: BLE001
                    print(f"  Executable Symlink Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Executable Stat Error: {exc}")
    else:
        print(f"  Executable Stat:     NOT FOUND at {sys.executable}")

    # Inspect target script
    if target_script:
        if target_script.exists():
            try:
                s_stat = target_script.stat()
                s_read = os.access(target_script, os.R_OK)
                print(f"  Target Script:       {target_script} (mode: {oct(s_stat.st_mode)}, readable: {s_read})")
            except Exception as exc:  # noqa: BLE001
                print(f"  Target Script Error: {exc}")
        else:
            print(f"  Target Script:       NOT FOUND ({target_script})")

    # Inspect virtual environment
    venv_dir = ROOT / ".venv"
    if venv_dir.is_dir():
        pyvenv_cfg = venv_dir / "pyvenv.cfg"
        cfg_info = "found" if pyvenv_cfg.is_file() else "missing"
        print(f"  .venv directory:     {venv_dir} (pyvenv.cfg: {cfg_info})")
        bin_dir = venv_dir / "bin"
        if bin_dir.is_dir():
            python_bin = bin_dir / "python"
            python3_bin = bin_dir / "python3"
            print(f"  .venv/bin/python:    exists={python_bin.exists()}, is_symlink={python_bin.is_symlink()}")
            print(f"  .venv/bin/python3:   exists={python3_bin.exists()}, is_symlink={python3_bin.is_symlink()}")

    # Inspect .env and tokens
    env_p = ROOT / ".env"
    print(f"  .env file:           {'Found at ' + str(env_p) if env_p.is_file() else 'NOT FOUND at ' + str(env_p)}")
    bot_tok = os.environ.get("SLACK_BOT_TOKEN", "")
    app_tok = os.environ.get("SLACK_APP_TOKEN", "")
    print(f"  SLACK_BOT_TOKEN:     {'Set (' + bot_tok[:6] + '...)' if bot_tok else 'NOT SET'}")
    print(f"  SLACK_APP_TOKEN:     {'Set (' + app_tok[:6] + '...)' if app_tok else 'NOT SET'}")
    web_val = os.environ.get("WEB_PORT") or os.environ.get("WEBGUI")
    print(f"  WEB_PORT:            {web_val if web_val else 'Not set'}")
    print(f"  HUB_PORT:            {os.environ.get('HUB_PORT', '8765 (default)')}")
    log_info("==========================================================")
    print()


def run_single(script_path: Path, name: str, extra_args: list[str]) -> int:
    """Run a single service and supervise until exit or interrupt."""
    cmd = [sys.executable, str(script_path)] + extra_args
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)

    log_info(f"Starting {name} ({' '.join(cmd)})...")
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    except PermissionError as exc:
        log_err(f"Permission denied executing '{cmd[0]}': {exc}")
        print(traceback.format_exc())
        diagnose_environment(script_path)
        if not IS_WINDOWS:
            log_warn(f"Remediation: chmod +x {cmd[0]}")
            log_warn("Or recreate the Linux virtual environment: rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")
            log_warn("Or launch directly via python3: python3 -m bot.app")
        return 126
    except OSError as exc:
        log_err(f"OS error executing '{cmd[0]}': {exc}")
        print(traceback.format_exc())
        diagnose_environment(script_path)
        return 1

    try:
        rc = proc.wait()
        if rc != 0:
            log_err(f"{name} exited with status code {rc}.")
            if rc in (126, 127):
                log_warn(f"Exit code {rc} indicates an execution or permission error.")
                diagnose_environment(script_path)
                if not IS_WINDOWS:
                    log_warn(f"Inspect binary: ls -la {cmd[0]}")
                    log_warn(f"Ensure executable bit: chmod +x {cmd[0]}")
                    log_warn("Or launch directly via python3: python3 -m bot.app")
        else:
            log_info(f"{name} process finished cleanly.")
        return rc
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
            "Interrupt received (Ctrl+C). Shutting down all Cmdiqo services safely..."
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
    log_info("Starting Cmdiqo Bot & Hub server...")
    hub_cmd = [sys.executable, str(hub_script)]
    try:
        hub_proc = subprocess.Popen(hub_cmd, cwd=str(ROOT), env=env)
        processes["Hub"] = hub_proc
    except PermissionError as exc:
        log_err(f"Permission denied executing '{hub_cmd[0]}': {exc}")
        if not IS_WINDOWS:
            log_warn(f"Try running: chmod +x {hub_cmd[0]}")
            log_warn("Or recreate the virtual environment on Linux: rm -rf .venv && python3 -m venv .venv")
        return 126

    # Wait briefly for Hub to initialize
    time.sleep(1.5)
    if hub_proc.poll() is not None:
        log_err(f"Hub server exited prematurely with code {hub_proc.returncode}.")
        return hub_proc.returncode

    # 2. Start Agent
    log_info("Starting Cmdiqo Remote Agent...")
    agent_cmd = [sys.executable, str(agent_script)] + extra_args
    try:
        agent_proc = subprocess.Popen(agent_cmd, cwd=str(ROOT), env=env)
        processes["Agent"] = agent_proc
    except PermissionError as exc:
        log_err(f"Permission denied executing '{agent_cmd[0]}': {exc}")
        if not IS_WINDOWS:
            log_warn(f"Try running: chmod +x {agent_cmd[0]}")
        shutdown_all()
        return 126

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


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"], max_content_width=100)


def _resolve_cli_version() -> str:
    try:
        from bot.app import get_version

        return get_version()
    except Exception:  # noqa: BLE001
        return "1.0.0"


@click.group(
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
)
@click.version_option(
    version=_resolve_cli_version(),
    prog_name="Cmdiqo",
    message="%(prog)s v%(version)s",
)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Cmdiqo Process Runner & Service Supervisor.

    Manage Hub, Agent, and interactive Slack simulation with unified process lifecycle.
    Running without arguments starts both Hub and Agent concurrently.
    """
    load_dotenv(ROOT / ".env")
    if ctx.invoked_subcommand is None:
        sys.exit(run_both([]))


@cli.command("hub", context_settings=dict(ignore_unknown_options=True, help_option_names=["-h", "--help"]))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def cmd_hub(extra_args: tuple[str, ...]) -> None:
    """Start the Cmdiqo Bot & WebSocket Hub service only."""
    sys.exit(run_single(ROOT / "bot" / "app.py", "Hub", list(extra_args)))


@cli.command("agent", context_settings=dict(ignore_unknown_options=True, help_option_names=["-h", "--help"]))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def cmd_agent(extra_args: tuple[str, ...]) -> None:
    """Start the Cmdiqo Remote Agent on this host only."""
    sys.exit(run_single(ROOT / "agent" / "agent.py", "Agent", list(extra_args)))


@cli.command("sim", context_settings=dict(ignore_unknown_options=True, help_option_names=["-h", "--help"]))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def cmd_sim(extra_args: tuple[str, ...]) -> None:
    """Start the interactive Slack emulator (no Slack tokens needed)."""
    sys.exit(run_single(ROOT / "slacksim" / "slacksim.py", "SlackSim", list(extra_args)))


@cli.command("slacksim", hidden=True, context_settings=dict(ignore_unknown_options=True, help_option_names=["-h", "--help"]))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def cmd_slacksim(extra_args: tuple[str, ...]) -> None:
    """Hidden alias for sim command."""
    sys.exit(run_single(ROOT / "slacksim" / "slacksim.py", "SlackSim", list(extra_args)))


@cli.command("both", context_settings=dict(ignore_unknown_options=True, help_option_names=["-h", "--help"]))
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def cmd_both(extra_args: tuple[str, ...]) -> None:
    """Start both Hub and Agent concurrently with unified supervision."""
    sys.exit(run_both(list(extra_args)))


@cli.command("diag", context_settings=dict(help_option_names=["-h", "--help"]))
def cmd_diag() -> None:
    """Inspect Python runtime, filesystem permissions, and configuration."""
    diagnose_environment()
    sys.exit(0)


@cli.command("version", context_settings=dict(help_option_names=["-h", "--help"]))
def cmd_version() -> None:
    """Show bundled application version and exit."""
    from bot.app import get_version

    click.echo(f"Cmdiqo v{get_version()}")
    sys.exit(0)


def main() -> None:
    """Entry point for run.py CLI."""
    cli()


if __name__ == "__main__":
    main()
