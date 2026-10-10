"""bot.webgui - Lightweight real-time WebGUI dashboard for Cmdiqo Hub.

Provides an integrated Flask single-page dashboard displaying connected agents
and a 50-entry live event log, styled using the Obsidian Cyberpunk aesthetic
from cmdiqo-web and updated in real-time via Server-Sent Events (SSE).
"""

from __future__ import annotations

import collections
import json
import logging
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, send_from_directory

log = logging.getLogger("cmdiqo-bot.webgui")

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def normalize_arch(arch_str: str, platform_str: str = "") -> str:
    """Normalize reported machine architecture to one of arm64, armv6, win64, linux."""
    a = (arch_str or "").strip().lower()
    p = (platform_str or "").strip().lower()

    if a in ("arm64", "aarch64", "armv8", "armv8l", "armv9"):
        return "arm64"
    if a in ("armv6", "armv6l", "armv7", "armv7l", "armhf"):
        return "armv6"
    if "win" in p or a in ("win64", "amd64", "x86_64") and "win" in p:
        return "win64"
    if a in ("x86_64", "amd64", "x64", "i386", "i686"):
        return "linux"
    if "linux" in p:
        return "linux"

    return "linux"


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
            boot_epoch = dt.replace(tzinfo=timezone.utc).timestamp()
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


class WebGuiState:
    """Thread-safe state manager for WebGUI connected agents and event logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, dict[str, Any]] = {}
        self._logs: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()

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
            self.state.add_log(
                level=record.levelname,
                source=record.name.replace("cmdiqo-bot.", "").replace("cmdiqo-bot", "hub"),
                message=msg,
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


def create_webgui_app(state: WebGuiState, bot_version: str = "1.0.0") -> Flask:
    """Create Flask application configured for the Cmdiqo Hub WebGUI."""
    app = Flask(
        "cmdiqo-webgui",
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
    )

    @app.after_request
    def set_security_headers(resp: Response) -> Response:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    @app.route("/favicon.ico")
    def favicon() -> Any:
        fav_path = STATIC_DIR / "img" / "cmdiqo-logo.webp"
        if fav_path.exists():
            return send_from_directory(str(STATIC_DIR / "img"), "cmdiqo-logo.webp", mimetype="image/webp")
        return ("", 204)

    @app.route("/")
    def index() -> str:
        agents = state.get_agents()
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
    state: WebGuiState, port: int, host: str = "0.0.0.0", bot_version: str = "1.0.0"
) -> threading.Thread:
    """Start the WebGUI Flask server in a dedicated background daemon thread."""
    from werkzeug.serving import make_server

    app = create_webgui_app(state, bot_version=bot_version)
    # Quiet werkzeug request logs to keep terminal logs clean
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    server = make_server(host, port, app, threaded=True)
    log.info("Starting WebGUI server on http://%s:%d", host, port)

    t = threading.Thread(target=server.serve_forever, daemon=True, name="WebGUI-Server")
    t.start()
    return t
