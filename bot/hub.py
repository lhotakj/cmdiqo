"""Hub server: accepts outbound WebSocket connections from LAN agents and
executes shell / PowerShell commands on them on behalf of Slack commands.

Agents connect OUTBOUND to this server, so agent hosts need no open ports,
no port forwarding, and work behind NAT the same way the Slack bot does.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any

import websockets

log = logging.getLogger("cmdiqo-bot.hub")


def _headers(ws: Any) -> Any:
    """Compatibility: websockets >= 14 exposes ws.request.headers, the legacy
    API exposed ws.request_headers."""
    req = getattr(ws, "request", None)
    if req is not None and getattr(req, "headers", None) is not None:
        return req.headers
    return getattr(ws, "request_headers", {})


def _validate_token(expected_token: str | None, auth_header: str | None) -> bool:
    """Validate bearer token using constant-time comparison to prevent timing attacks."""
    if not expected_token:
        return True
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    provided = auth_header[len("Bearer ") :].strip()
    return hmac.compare_digest(expected_token, provided)


DEFAULT_BIND_HOST = "0.0.0.0"  # nosec: B104


class Hub:
    """WebSocket Hub server that manages connections to distributed LAN agents."""

    def __init__(
        self, host: str = DEFAULT_BIND_HOST, port: int = 8765, token: str | None = None
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.agents: dict[
            str, dict[str, Any]
        ] = {}  # name -> {ws, platform, since, addr}
        self.pending: dict[str, asyncio.Future] = {}  # req_id -> Future
        self.pending_agents: dict[str, str] = {}  # req_id -> agent_name
        self._server: Any = None
        self._stop_event = asyncio.Event()
        self.on_agent_event: Any = None  # Optional async callback: fn(event_type: str, agent_name: str, hello: dict)

    async def run(self) -> None:
        """Run the hub WebSocket server."""
        async with websockets.serve(
            self._handle,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=20,
        ) as server:
            self._server = server
            log.info(
                "Hub listening on ws://%s:%d (agents connect outbound to this)",
                self.host,
                self.port,
            )
            await self._stop_event.wait()

    def stop(self) -> None:
        """Signal the hub to stop."""
        self._stop_event.set()
        if self._server is not None:
            self._server.close()

    def _cancel_pending_for_agent(self, agent_name: str) -> None:
        """Fail pending futures when their target agent disconnects."""
        to_cancel = [
            req_id for req_id, name in self.pending_agents.items() if name == agent_name
        ]
        for req_id in to_cancel:
            fut = self.pending.pop(req_id, None)
            self.pending_agents.pop(req_id, None)
            if fut is not None and not fut.done():
                fut.set_exception(
                    ConnectionResetError(
                        f"Agent '{agent_name}' disconnected before command completed."
                    )
                )

    # ------------------------------------------------------------------ conn
    async def _handle(self, ws: Any) -> None:
        addr = (
            f"{ws.remote_address[0]}:{ws.remote_address[1]}"
            if ws.remote_address
            else "?"
        )
        auth = _headers(ws).get("Authorization")
        if not _validate_token(self.token, auth):
            log.warning("Rejected connection with bad/missing token from %s", addr)
            await ws.close(code=4001, reason="bad token")
            return

        try:
            raw_hello = await asyncio.wait_for(ws.recv(), timeout=10)
            hello = json.loads(raw_hello)
        except (
            TimeoutError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
            websockets.ConnectionClosed,
        ) as exc:
            log.warning("Failed receiving hello from %s: %s", addr, exc)
            await ws.close(code=4002, reason="expected hello")
            return

        if (
            not isinstance(hello, dict)
            or hello.get("type") != "hello"
            or not hello.get("name")
        ):
            await ws.close(code=4002, reason="expected hello with name")
            return

        role = str(hello.get("role", "agent")).lower().strip()
        if role == "client":
            client_id = f"client-{uuid.uuid4().hex[:6]}"
            log.info("Client connected: %s from %s", client_id, addr)
            try:
                async for raw in ws:
                    await self._on_client_message(ws, raw)
            finally:
                log.info("Client disconnected: %s", client_id)
            return

        name = str(hello["name"]).strip()
        version = str(hello.get("version", "?"))
        self.agents[name] = {
            "ws": ws,
            "version": version,
            "platform": str(hello.get("platform", "?")),
            "since": time.strftime("%Y-%m-%d %H:%M:%S"),
            "addr": addr,
            "hello": hello,
        }
        log.info(
            "Agent connected: %s (v%s, %s) from %s",
            name,
            version,
            self.agents[name]["platform"],
            addr,
        )

        if self.on_agent_event is not None:
            try:
                res = self.on_agent_event("connected", name, hello)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:  # noqa: BLE001
                log.warning("Error in on_agent_event ('connected') for agent '%s': %s", name, exc)

        try:
            async for raw in ws:
                await self._on_message(raw)
        finally:
            if self.agents.get(name, {}).get("ws") is ws:
                del self.agents[name]
                self._cancel_pending_for_agent(name)
            log.info("Agent disconnected: %s", name)
            if self.on_agent_event is not None:
                try:
                    res = self.on_agent_event("disconnected", name, hello)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:  # noqa: BLE001
                    log.warning("Error in on_agent_event ('disconnected') for agent '%s': %s", name, exc)

    async def _on_client_message(self, ws: Any, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return

        req_id = msg.get("id")
        msg_type = msg.get("type")

        if msg_type == "get_agents":
            agents_summary = {
                n: {
                    "version": str(i.get("version", "?")),
                    "platform": str(i.get("platform", "?")),
                    "since": str(i.get("since", "")),
                    "addr": str(i.get("addr", "")),
                }
                for n, i in self.agents.items()
            }
            await ws.send(
                json.dumps(
                    {
                        "id": req_id,
                        "type": "agents_list",
                        "agents": agents_summary,
                        "table": self.agents_table(),
                    }
                )
            )
            return

        if msg_type == "exec":
            name = str(msg.get("name", ""))
            kind = str(msg.get("kind", "shell"))
            cmd = str(msg.get("cmd", ""))
            cwd = msg.get("cwd") or None
            timeout = int(msg.get("timeout") or 120)
            try:
                output = await self.exec(
                    name=name, kind=kind, cmd=cmd, cwd=cwd, timeout=timeout
                )
                await ws.send(
                    json.dumps(
                        {
                            "id": req_id,
                            "type": "exec_result",
                            "ok": True,
                            "output": output,
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                await ws.send(
                    json.dumps(
                        {
                            "id": req_id,
                            "type": "exec_result",
                            "ok": False,
                            "error": str(exc),
                        }
                    )
                )
            return

    async def _on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return

        if msg.get("type") == "result":
            req_id = msg.get("id")
            fut = self.pending.pop(req_id, None)
            agent_name = self.pending_agents.pop(req_id, None)
            if fut is not None and not fut.done():
                ok = bool(msg.get("ok"))
                code = msg.get("code")
                out_len = len(msg.get("output") or "")
                err = msg.get("error")
                log.info(
                    "[%s] Received result from agent '%s': ok=%s, code=%s, out_bytes=%d, error=%s",
                    str(req_id)[:8],
                    agent_name or "?",
                    ok,
                    code,
                    out_len,
                    err or "none",
                )
                fut.set_result(msg)

    # ------------------------------------------------------------------ exec
    async def exec(
        self,
        name: str,
        kind: str,
        cmd: str,
        cwd: str | None = None,
        timeout: int = 120,
    ) -> str:
        """Send an execution request to an agent and await the response."""
        agent = self.agents.get(name)
        if agent is None:
            connected = ", ".join(sorted(self.agents)) or "(none)"
            raise RuntimeError(
                f"agent '{name}' is not connected. Connected agents: {connected}"
            )

        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[req_id] = fut
        self.pending_agents[req_id] = name

        log.info(
            "[%s] Dispatching %s command to agent '%s': %s (timeout=%ds, cwd=%s)",
            req_id[:8],
            kind,
            name,
            cmd,
            timeout,
            cwd or "(default)",
        )

        try:
            await agent["ws"].send(
                json.dumps(
                    {
                        "id": req_id,
                        "type": "exec",
                        "kind": kind,
                        "cmd": cmd,
                        "cwd": cwd,
                        "timeout": timeout,
                    }
                )
            )
            msg = await asyncio.wait_for(fut, timeout=timeout + 30)
        finally:
            self.pending.pop(req_id, None)
            self.pending_agents.pop(req_id, None)

        output = (msg.get("output") or "").strip()
        code = msg.get("code")
        if not output:
            if code is not None:
                output = f"(finished with exit code {code})"
            elif msg.get("ok"):
                output = "(completed successfully with no output)"
            else:
                err = msg.get("error") or "command returned failure"
                output = f"(failed: {err})"
        return output

    def agents_table(self) -> str:
        """Format connected agents as a clean ASCII table."""
        if not self.agents:
            return "No agents connected."
        header = ("AGENT", "VERSION", "PLATFORM", "CONNECTED SINCE", "ADDRESS")
        rows = [
            (n, i.get("version", "?"), i["platform"], i["since"], i["addr"])
            for n, i in sorted(self.agents.items())
        ]
        data = [header] + rows
        widths = [max(len(r[i]) for r in data) for i in range(5)]
        return "\n".join(
            "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
            for row in data
        )


class RemoteHubClient:
    """Remote Hub client proxy connecting to an external Hub server over WebSocket.

    Provides the same interface as `Hub` (agents, agents_table, exec) so
    commands and command dispatch work transparently against a running Hub.
    """

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url
        self.token = token
        self.agents: dict[str, dict[str, Any]] = {}
        self._cached_table: str = "No agents connected."
        self._ws: Any = None
        self._pending: dict[str, asyncio.Future] = {}
        self._listener_task: asyncio.Task | None = None
        self._running = False

    async def start(self, timeout: float = 5.0) -> None:
        """Connect to the remote Hub WebSocket server and start background listener."""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            # websockets >= 14
            ws = await asyncio.wait_for(
                websockets.connect(
                    self.url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=timeout,
            )
        except TypeError:
            # websockets 13 compatibility
            ws = await asyncio.wait_for(
                websockets.connect(
                    self.url,
                    extra_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=timeout,
            )

        self._ws = ws
        self._running = True

        # Send client hello
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "role": "client",
                    "name": "slacksim",
                }
            )
        )

        self._listener_task = asyncio.create_task(self._listen_loop())
        # Refresh agents initially
        await self.refresh_agents(timeout=timeout)

    async def _listen_loop(self) -> None:
        """Listen for server responses and complete pending futures."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(msg, dict):
                    continue

                req_id = msg.get("id")
                if req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(
                        ConnectionResetError("Connection to Hub server lost.")
                    )
            self._pending.clear()

    async def refresh_agents(self, timeout: float = 5.0) -> dict[str, dict[str, Any]]:
        """Query Hub server for currently connected agents."""
        if not self._running or self._ws is None:
            return {}

        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(
                json.dumps(
                    {
                        "id": req_id,
                        "type": "get_agents",
                    }
                )
            )
            resp = await asyncio.wait_for(fut, timeout=timeout)
            self.agents = resp.get("agents", {})
            self._cached_table = resp.get("table", "No agents connected.")
            return self.agents
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed refreshing agents from Hub: %s", exc)
            return self.agents
        finally:
            self._pending.pop(req_id, None)

    def agents_table(self) -> str:
        """Return ASCII table of connected agents."""
        # Trigger an asynchronous refresh task if possible
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.refresh_agents())
        except RuntimeError:
            pass
        return self._cached_table

    async def exec(
        self,
        name: str,
        kind: str,
        cmd: str,
        cwd: str | None = None,
        timeout: int = 120,
    ) -> str:
        """Send an execution request through Hub to target agent."""
        if not self._running or self._ws is None:
            raise RuntimeError("Not connected to Hub server.")

        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(
                json.dumps(
                    {
                        "id": req_id,
                        "type": "exec",
                        "name": name,
                        "kind": kind,
                        "cmd": cmd,
                        "cwd": cwd,
                        "timeout": timeout,
                    }
                )
            )
            resp = await asyncio.wait_for(fut, timeout=timeout + 30)
        finally:
            self._pending.pop(req_id, None)

        if not resp.get("ok"):
            err = resp.get("error", "command execution failed")
            raise RuntimeError(err)
        return str(resp.get("output", ""))

    async def stop(self) -> None:
        """Disconnect and cleanup client proxy."""
        self._running = False
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.close()

