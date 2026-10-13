"""bot.webgui - Lightweight real-time WebGUI dashboard for Cmdiqo Hub.

Provides an integrated Flask single-page dashboard displaying connected agents
and a 50-entry live event log, styled using the Obsidian Cyberpunk aesthetic
from cmdiqo-web and updated in real-time via Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import queue
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

log = logging.getLogger("cmdiqo-bot.webgui")

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def normalize_arch(arch_str: str, platform_str: str = "") -> str:
    """Normalize reported machine architecture to one of win64, linux64, arm64, armv6."""
    a = (arch_str or "").strip().lower()
    p = (platform_str or "").strip().lower()

    if a in ("arm64", "aarch64", "armv8", "armv8l", "armv9"):
        return "arm64"
    if a in ("armv6", "armv6l", "armv7", "armv7l", "armhf"):
        return "armv6"
    if a in ("win64", "amd64", "x86_64") and "win" in p or "win" in a or "win" in p:
        return "win64"
    if a in ("linux64", "linux", "x86_64", "amd64", "x64", "i386", "i686"):
        return "linux64"
    if "linux" in p:
        return "linux64"

    return "linux64"


def format_uptime_zero_padded(total_seconds: float) -> str:
    """Format total seconds into zero-padded '<days>d <hours>h <m>m <s>s'."""
    sec = max(0, int(total_seconds))
    days = sec // 86400
    hours = (sec % 86400) // 3600
    minutes = (sec % 3600) // 60
    seconds = sec % 60
    return f"{days:02d}d {hours:02d}h {minutes:02d}m {seconds:02d}s"


def parse_and_standardize_uptime_checked(
    raw_output: str, os_type: str = "linux"
) -> tuple[float, str, bool]:
    """Parse raw uptime output from Linux or Windows and return (boot_timestamp, standardized_uptime, is_matched).

    Returns:
        boot_timestamp: Unix epoch timestamp in seconds when the host booted.
        standardized_uptime: String formatted as '<days>d <hours>h <m>m <s>s' with zero pads.
        is_matched: True if a recognized uptime pattern was matched; False if fallback was used.
    """
    now = time.time()
    text = (raw_output or "").strip()

    # 1. Check for explicit duration format: "Uptime: 14d 3h 22m 15s" or "4d 2h 15m 9s"
    win_match = re.search(
        r"(?:Uptime:\s*)?(\d+)d\s+(\d+)h\s+(\d+)m\s+(\d+)s", text, re.IGNORECASE
    )
    if win_match:
        d = int(win_match.group(1))
        h = int(win_match.group(2))
        m = int(win_match.group(3))
        s = int(win_match.group(4))
        total_seconds = float(d * 86400 + h * 3600 + m * 60 + s)
        boot_epoch = now - total_seconds
        return boot_epoch, format_uptime_zero_padded(total_seconds), True

    # 2. Check for explicit boot timestamp (e.g. Booted: 2026-10-06 10:02:37)
    boot_match = re.search(
        r"Booted:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", text, re.IGNORECASE
    )
    if boot_match:
        boot_str = boot_match.group(1).replace("T", " ")
        try:
            dt = datetime.strptime(boot_str, "%Y-%m-%d %H:%M:%S")
            boot_epoch = dt.replace(tzinfo=UTC).timestamp()
            total_seconds = max(0.0, now - boot_epoch)
            return boot_epoch, format_uptime_zero_padded(total_seconds), True
        except ValueError:
            pass

    # 3. Check for Linux uptime output:
    # "14:23:01 up 14 days,  3:22,  2 users..."
    # "14:23:01 up 2 days, 14:05,  1 user..."
    # "14:23:01 up  1:30,  1 user..."
    # "14:23:01 up 45 min,  1 user..."
    # "14:23:01 up 35 secs,  1 user..."
    up_idx = text.find("up ")
    if up_idx != -1:
        up_part = text[up_idx + 3 :]
        user_m = re.search(r",\s*\d+\s*user", up_part)
        if user_m:
            up_substr = up_part[: user_m.start()].strip()
        else:
            up_substr = up_part.split("\n")[0].strip()

        days = 0
        hours = 0
        minutes = 0
        seconds = 0

        # Match days if present
        day_m = re.search(r"(\d+)\s+day", up_substr, re.IGNORECASE)
        if day_m:
            days = int(day_m.group(1))
            up_substr = re.sub(r"(\d+)\s+days?,\s*", "", up_substr, flags=re.IGNORECASE).strip()

        # Check for HH:MM
        hm_m = re.search(r"(\d+):(\d+)", up_substr)
        if hm_m:
            hours = int(hm_m.group(1))
            minutes = int(hm_m.group(2))
        else:
            # Check for "XX min"
            min_m = re.search(r"(\d+)\s+min", up_substr, re.IGNORECASE)
            if min_m:
                minutes = int(min_m.group(1))
            # Check for "XX sec"
            sec_m = re.search(r"(\d+)\s+sec", up_substr, re.IGNORECASE)
            if sec_m:
                seconds = int(sec_m.group(1))

        total_seconds = float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
        boot_epoch = now - total_seconds
        return boot_epoch, format_uptime_zero_padded(total_seconds), True

    # 4. Fallback: single number (seconds)
    num_m = re.match(r"^(\d+(?:\.\d+)?)$", text)
    if num_m:
        total_seconds = float(num_m.group(1))
        boot_epoch = now - total_seconds
        return boot_epoch, format_uptime_zero_padded(total_seconds), True

    # Default fallback
    return now, "00d 00h 00m 00s", False


def parse_and_standardize_uptime(
    raw_output: str, os_type: str = "linux"
) -> tuple[float, str]:
    """Parse raw uptime output from Linux or Windows and return (boot_timestamp, standardized_uptime)."""
    boot_epoch, formatted, _ = parse_and_standardize_uptime_checked(raw_output, os_type)
    return boot_epoch, formatted


def is_command_output_failure(output: str) -> bool:
    """Check whether a command execution response indicates a failure or error."""
    if not output or not isinstance(output, str):
        return False
    stripped = output.strip()
    # Explicit emoji/prefix failure indicators
    if stripped.startswith((":x:", "❌", ":no_entry_sign:", "🚫", ":stopwatch:", "Error:", "ERROR:")):
        return True
    # Warning failure indicators
    if stripped.startswith((":warning:", "⚠️")):
        lower = stripped.lower()
        if any(term in lower for term in ("failed", "error", "not connected", "unknown", "invalid", "cannot")):
            return True
    return bool(stripped.lower().startswith("error:"))


class WebGuiState:
    """Thread-safe state manager for WebGUI connected agents and event logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, dict[str, Any]] = {}
        self._logs: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.hub: Any = None
        self.commands: list[dict[str, Any]] = []
        self.aliases: list[dict[str, Any]] = []
        self.default_timeout: int = 120
        self.custom_executor: Any = None

    def get_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._agents.values())

    def get_logs(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._logs)

    def register_agent(
        self,
        name: str,
        hello: dict[str, Any],
        addr: str = "",
        boot_time: float | None = None,
        uptime_str: str = "00d 00h 00m 00s",
    ) -> None:
        platform_str = str(hello.get("platform", "Unknown"))
        arch_raw = str(hello.get("arch", ""))
        normalized_arch = normalize_arch(arch_raw, platform_str)
        now_epoch = time.time()

        if boot_time is None:
            boot_time = now_epoch

        agent_entry = {
            "name": name,
            "version": str(hello.get("version", "?")),
            "platform": platform_str,
            "arch": normalized_arch,
            "arch_icon": f"/static/img/arch/{normalized_arch}.webp",
            "boot_time": boot_time,
            "uptime_str": uptime_str,
            "connected_since": time.strftime("%Y-%m-%d %H:%M:%S"),
            "addr": addr,
        }

        with self._lock:
            self._agents[name] = agent_entry
            agents_snapshot = list(self._agents.values())

        self.broadcast("agent_update", {"agents": agents_snapshot})

    def update_agent_uptime(
        self, name: str, boot_time: float, uptime_str: str
    ) -> None:
        with self._lock:
            if name in self._agents:
                self._agents[name]["boot_time"] = boot_time
                self._agents[name]["uptime_str"] = uptime_str
                agents_snapshot = list(self._agents.values())
            else:
                return

        self.broadcast("agent_update", {"agents": agents_snapshot})

    def unregister_agent(self, name: str) -> None:
        with self._lock:
            if name in self._agents:
                del self._agents[name]
                agents_snapshot = list(self._agents.values())
            else:
                return

        self.broadcast("agent_update", {"agents": agents_snapshot})

    def add_log(self, level: str, source: str, message: str) -> None:
        entry = {
            "id": f"log-{time.time_ns()}",
            "ts": time.strftime("%H:%M:%S"),
            "level": level.upper(),
            "source": source,
            "message": message,
        }
        with self._lock:
            self._logs.append(entry)

        self.broadcast("log_entry", {"log": entry})

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        payload = {"type": event_type, "data": data, "timestamp": time.time()}
        with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    # Drop stale events for slow clients
                    pass

    def execute_command_sync(
        self, command_text: str, timeout: int | None = None
    ) -> dict[str, Any]:
        """Execute a bot command synchronously from the WebGUI HTTP worker thread."""
        cleaned = command_text.strip()
        if not cleaned:
            return {
                "success": False,
                "error": "Empty command",
                "output": "Error: Empty command",
                "command": "",
            }

        if self.custom_executor is not None:
            return self.custom_executor(cleaned)

        if not cleaned.startswith("/"):
            cleaned = f"/{cleaned}"

        eff_timeout = timeout or self.default_timeout

        if self.loop is None or not self.loop.is_running():
            return {
                "success": False,
                "error": "Hub event loop is not active",
                "output": "Error: Hub event loop is not active or agent is disconnected",
                "command": cleaned,
            }

        async def _exec() -> dict[str, Any]:
            from bot.app import (
                apply_aliases,
                execute_command,
                format_params,
                get_version,
            )

            txt = cleaned
            if self.aliases:
                txt = apply_aliases(txt, self.aliases)

            # Built-in version check
            if txt.lower() in ("/lan version", "/host version"):
                return {
                    "success": True,
                    "output": f"cmdiqo-bot v{get_version()}",
                    "command": txt,
                }

            # Built-in agents check
            if self.hub is not None and txt.lower() in (
                "/lan agents list",
                "/lan agents",
                "/host agents list",
                "/host agents",
            ):
                agents_tbl = (
                    self.hub.agents_table()
                    if hasattr(self.hub, "agents_table")
                    else "No agents connected"
                )
                return {
                    "success": True,
                    "output": agents_tbl,
                    "command": txt,
                }

            target_name = ""
            for c in self.commands:
                m = c["regex"].match(txt)
                if m:
                    spec = c["spec"]
                    params = m.groupdict()
                    target_name = (
                        params.get("host")
                        or (format_params(spec["target"], params) if spec.get("target") else "")
                    )
                    try:
                        output = await execute_command(
                            spec,
                            params,
                            hub=self.hub,
                            default_timeout=eff_timeout,
                        )
                        if spec.get("name") == "host-uptime" or str(spec.get("trigger", "")).lower().endswith(" uptime"):
                            try:
                                boot_epoch, formatted, ok = parse_and_standardize_uptime_checked(output)
                                if ok and target_name:
                                    self.update_agent_uptime(target_name, boot_epoch, formatted)
                            except Exception as exc:  # noqa: BLE001
                                log.debug("Failed updating webgui agent uptime: %s", exc)

                        is_failed = is_command_output_failure(output)
                        if is_failed:
                            self.add_log("ERROR", "webgui", f"Command failed: {txt}")
                            return {
                                "success": False,
                                "output": output,
                                "command": txt,
                                "target": target_name,
                            }

                        self.add_log("INFO", "webgui", f"Executed: {txt}")
                        return {
                            "success": True,
                            "output": output,
                            "command": txt,
                            "target": target_name,
                        }
                    except Exception as exc:
                        log.exception("Command execution failed for %s", spec.get("name"))
                        self.add_log("ERROR", "webgui", f"Command failed: {txt} ({exc})")
                        return {
                            "success": False,
                            "output": f"Error: {exc}",
                            "command": txt,
                            "target": target_name,
                        }

            self.add_log("ERROR", "webgui", f"Unknown command: {txt}")
            return {
                "success": False,
                "output": f"Unknown or unrecognized command: {txt}",
                "command": txt,
            }

        fut = asyncio.run_coroutine_threadsafe(_exec(), self.loop)
        try:
            return fut.result(timeout=eff_timeout + 5)
        except TimeoutError:
            fut.cancel()
            self.add_log("ERROR", "webgui", f"Command timed out: {cleaned}")
            return {
                "success": False,
                "error": f"Command timed out after {eff_timeout} seconds",
                "output": f"Error: Command timed out after {eff_timeout} seconds",
                "command": cleaned,
            }
        except Exception as exc:  # noqa: BLE001
            self.add_log("ERROR", "webgui", f"Command error: {cleaned} ({exc})")
            return {
                "success": False,
                "error": str(exc),
                "output": f"Error: {exc}",
                "command": cleaned,
            }


class HubWebLogHandler(logging.Handler):
    """Logging handler that routes bot log records into the WebGUI 50-entry ring buffer."""

    def __init__(self, state: WebGuiState) -> None:
        super().__init__()
        self.state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Filter internal noisy werkzeug / access logs from cluttering the bot log view
            if "werkzeug" in record.name.lower():
                return
            msg = record.getMessage()
            lvl = record.levelname
            if record.name.startswith("websockets") and "connection open" in msg.lower():
                lvl = "DEBUG"
            self.state.add_log(
                level=lvl,
                source=record.name.replace("cmdiqo-bot.", "").replace("cmdiqo-bot", "hub"),
                message=msg,
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


def create_webgui_app(state: WebGuiState, bot_version: str | None = None) -> Flask:
    """Create Flask application configured for the Cmdiqo Hub WebGUI."""
    if not bot_version:
        from bot.app import get_version

        bot_version = get_version()
    app = Flask(
        "cmdiqo-webgui",
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.after_request
    def set_security_headers(resp: Response) -> Response:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
        return resp

    @app.route("/favicon.ico")
    def favicon() -> Any:
        fav_path = STATIC_DIR / "img" / "cmdiqo-logo.webp"
        if fav_path.exists():
            return send_from_directory(str(STATIC_DIR / "img"), "cmdiqo-logo.webp", mimetype="image/webp")
        return ("", 204)

    @app.route("/")
    def index() -> str:
        agents = sorted(
            state.get_agents(),
            key=lambda a: a.get("name", "").lower(),
        )
        logs = state.get_logs()
        return render_template(
            "index.html",
            agents=agents,
            logs=logs,
            bot_version=bot_version,
            agent_count=len(agents),
        )

    @app.route("/api/agents")
    def api_agents() -> Any:
        return jsonify({"agents": state.get_agents()})

    @app.route("/api/logs")
    def api_logs() -> Any:
        return jsonify({"logs": state.get_logs()})

    @app.route("/api/exec", methods=["POST"])
    def api_exec() -> Any:
        data = request.get_json(silent=True) or {}
        cmd = str(data.get("command", "")).strip()
        host = str(data.get("host", "")).strip()
        subcmd = str(data.get("cmd", "")).strip()

        if not cmd and host and subcmd:
            cmd = f"/host {host} {subcmd}"

        if not cmd:
            return jsonify({
                "success": False,
                "error": "Missing 'command' parameter",
                "output": "Error: Missing command parameter",
            }), 400

        result = state.execute_command_sync(cmd)
        return jsonify(result), 200

    @app.route("/api/events")
    def api_events() -> Response:
        q = state.subscribe()

        def stream() -> Any:
            try:
                # Initial snapshot event
                initial_data = {
                    "agents": state.get_agents(),
                    "logs": state.get_logs(),
                }
                yield f"event: init\ndata: {json.dumps(initial_data)}\n\n"

                while True:
                    try:
                        event = q.get(timeout=20.0)
                        yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                    except queue.Empty:
                        # Keep-alive heartbeat ping to prevent proxy connection drops
                        yield ": keepalive\n\n"
            finally:
                state.unsubscribe(q)

        response = Response(stream(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Connection"] = "keep-alive"
        return response

    return app


def start_webgui_thread(
    state: WebGuiState, port: int, host: str = "0.0.0.0", bot_version: str | None = None  # nosec: B104
) -> threading.Thread:
    """Start the WebGUI Flask server in a dedicated background daemon thread."""
    from werkzeug.serving import make_server

    if not bot_version:
        from bot.app import get_version

        bot_version = get_version()

    app = create_webgui_app(state, bot_version=bot_version)
    # Quiet werkzeug request logs to keep terminal logs clean
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    server = make_server(host, port, app, threaded=True)
    log.info("Starting WebGUI server v%s on http://%s:%d", bot_version, host, port)

    if not state.get_logs():
        state.add_log("INFO", "hub", f"Cmdiqo Hub v{bot_version} started")

    t = threading.Thread(target=server.serve_forever, daemon=True, name="WebGUI-Server")
    t.start()
    return t
