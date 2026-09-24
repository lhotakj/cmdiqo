"""Unit and integration tests for Hub server and WebSocket protocol."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from bot.hub import Hub, _headers, _validate_token


def test_headers_extraction():
    class MockReq:
        def __init__(self):
            self.headers = {"Authorization": "Bearer 123"}

    class MockWs:
        def __init__(self):
            self.request = MockReq()

    assert _headers(MockWs()) == {"Authorization": "Bearer 123"}

    class LegacyWs:
        def __init__(self):
            self.request_headers = {"Authorization": "Bearer legacy"}

    assert _headers(LegacyWs()) == {"Authorization": "Bearer legacy"}


def test_validate_token():
    # No token configured on hub
    assert _validate_token(None, None) is True
    assert _validate_token(None, "Bearer secret") is True

    # Token configured
    expected = "super-secret-token"
    assert _validate_token(expected, None) is False
    assert _validate_token(expected, "Basic abc") is False
    assert _validate_token(expected, "Bearer wrong-token") is False
    assert _validate_token(expected, f"Bearer {expected}") is True


def test_agents_table_empty():
    hub = Hub()
    assert hub.agents_table() == "No agents connected."


def test_agents_table_populated():
    hub = Hub()
    hub.agents["host1"] = {
        "platform": "Linux/6.1",
        "since": "2026-09-23 12:00:00",
        "addr": "192.168.1.10:54321",
    }
    table = hub.agents_table()
    assert "AGENT" in table
    assert "host1" in table
    assert "Linux/6.1" in table


@pytest.mark.asyncio
async def test_hub_stop():
    hub = Hub()
    assert not hub._stop_event.is_set()
    hub.stop()
    assert hub._stop_event.is_set()


@pytest.mark.asyncio
async def test_hub_on_message_invalid():
    hub = Hub()
    # Should not raise on invalid data
    await hub._on_message("not valid json")
    await hub._on_message(json.dumps(["not", "a", "dict"]))
    await hub._on_message(json.dumps({"type": "other"}))


@pytest.mark.asyncio
async def test_hub_handle_bad_token():
    hub = Hub(token="secret-token")
    mock_ws = AsyncMock()
    mock_ws.remote_address = ("192.168.1.5", 12345)
    mock_ws.request.headers = {"Authorization": "Bearer wrong"}

    await hub._handle(mock_ws)
    mock_ws.close.assert_awaited_once_with(code=4001, reason="bad token")


@pytest.mark.asyncio
async def test_hub_handle_bad_hello():
    hub = Hub(token=None)
    mock_ws = AsyncMock()
    mock_ws.remote_address = ("192.168.1.5", 12345)
    mock_ws.request.headers = {}
    mock_ws.recv.return_value = json.dumps({"type": "wrong_type"})

    await hub._handle(mock_ws)
    mock_ws.close.assert_awaited_once_with(code=4002, reason="expected hello with name")


@pytest.mark.asyncio
async def test_hub_exec_not_connected():
    hub = Hub()
    with pytest.raises(RuntimeError, match="agent 'unknown-pc' is not connected"):
        await hub.exec("unknown-pc", "shell", "whoami")


@pytest.mark.asyncio
async def test_hub_exec_successful():
    hub = Hub()
    mock_ws = AsyncMock()
    hub.agents["win-node"] = {
        "ws": mock_ws,
        "platform": "Windows/11",
        "since": "2026-09-23 12:00:00",
        "addr": "192.168.1.15:5555",
    }

    async def simulate_agent_response():
        await asyncio.sleep(0.01)
        req_id = next(iter(hub.pending.keys()))
        result_msg = json.dumps(
            {
                "id": req_id,
                "type": "result",
                "ok": True,
                "code": 0,
                "output": "Windows Hotfix installed",
            }
        )
        await hub._on_message(result_msg)

    asyncio.create_task(simulate_agent_response())
    output = await hub.exec("win-node", "ps1", "Get-HotFix", timeout=5)
    assert output == "Windows Hotfix installed"
    mock_ws.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_hub_exec_empty_output_fallback():
    hub = Hub()
    mock_ws = AsyncMock()
    hub.agents["node"] = {
        "ws": mock_ws,
        "platform": "Linux",
        "since": "2026-09-23 12:00:00",
        "addr": "192.168.1.15:5555",
    }

    async def simulate_exit_code_response():
        await asyncio.sleep(0.01)
        req_id = next(iter(hub.pending.keys()))
        await hub._on_message(
            json.dumps(
                {
                    "id": req_id,
                    "type": "result",
                    "ok": True,
                    "code": 0,
                    "output": "",
                }
            )
        )

    asyncio.create_task(simulate_exit_code_response())
    output = await hub.exec("node", "shell", "true", timeout=5)
    assert "(finished with exit code 0)" in output


@pytest.mark.asyncio
async def test_hub_agent_disconnect_fails_pending_exec():
    hub = Hub()
    mock_ws = AsyncMock()
    hub.agents["node1"] = {
        "ws": mock_ws,
        "platform": "Linux",
        "since": "2026-09-23 12:00:00",
        "addr": "192.168.1.25:5555",
    }

    async def disconnect_agent():
        await asyncio.sleep(0.01)
        hub._cancel_pending_for_agent("node1")

    asyncio.create_task(disconnect_agent())

    with pytest.raises(
        ConnectionResetError, match="disconnected before command completed"
    ):
        await hub.exec("node1", "shell", "sleep 10", timeout=5)


@pytest.mark.asyncio
async def test_hub_exec_fallback_ok_no_output():
    hub = Hub()
    mock_ws = AsyncMock()
    hub.agents["node"] = {
        "ws": mock_ws,
        "platform": "Linux",
        "since": "2026-09-23 12:00:00",
        "addr": "192.168.1.15:5555",
    }

    async def simulate_ok():
        await asyncio.sleep(0.01)
        req_id = next(iter(hub.pending.keys()))
        await hub._on_message(
            json.dumps(
                {
                    "id": req_id,
                    "type": "result",
                    "ok": True,
                    "output": "",
                }
            )
        )

    asyncio.create_task(simulate_ok())
    output = await hub.exec("node", "shell", "noop", timeout=5)
    assert "(completed successfully with no output)" in output


@pytest.mark.asyncio
async def test_hub_exec_fallback_failure():
    hub = Hub()
    mock_ws = AsyncMock()
    hub.agents["node"] = {
        "ws": mock_ws,
        "platform": "Linux",
        "since": "2026-09-23 12:00:00",
        "addr": "192.168.1.15:5555",
    }

    async def simulate_fail():
        await asyncio.sleep(0.01)
        req_id = next(iter(hub.pending.keys()))
        await hub._on_message(
            json.dumps(
                {
                    "id": req_id,
                    "type": "result",
                    "ok": False,
                    "error": "permission denied",
                    "output": "",
                }
            )
        )

    asyncio.create_task(simulate_fail())
    output = await hub.exec("node", "shell", "restricted", timeout=5)
    assert "(failed: permission denied)" in output


@pytest.mark.asyncio
async def test_hub_handle_hello_timeout():
    hub = Hub(token=None)
    mock_ws = AsyncMock()
    mock_ws.remote_address = ("192.168.1.5", 12345)
    mock_ws.request.headers = {}
    mock_ws.recv.side_effect = TimeoutError()

    await hub._handle(mock_ws)
    mock_ws.close.assert_awaited_once_with(code=4002, reason="expected hello")


@pytest.mark.asyncio
async def test_hub_handle_client_hello_and_messages():
    hub = Hub(token=None)
    mock_ws = AsyncMock()
    mock_ws.remote_address = ("127.0.0.1", 54321)
    mock_ws.request.headers = {}
    mock_ws.recv.return_value = json.dumps({"type": "hello", "role": "client", "name": "slacksim"})

    # Setup an agent so get_agents returns data
    agent_ws = AsyncMock()
    hub.agents["node1"] = {
        "ws": agent_ws,
        "version": "1.0.0",
        "platform": "Linux/6.1",
        "since": "2026-09-24 10:00:00",
        "addr": "192.168.1.50:5555",
    }

    # Simulate client messages iterator
    async def mock_iter():
        yield json.dumps({"id": "req-1", "type": "get_agents"})
        yield json.dumps({"id": "req-2", "type": "exec", "name": "node1", "kind": "shell", "cmd": "hostname"})

    mock_ws.__aiter__.side_effect = mock_iter

    # Simulate agent result response for the exec request
    async def simulate_agent():
        await asyncio.sleep(0.01)
        req_id = next(iter(hub.pending.keys()))
        await hub._on_message(
            json.dumps({"id": req_id, "type": "result", "ok": True, "code": 0, "output": "nas-server"})
        )

    asyncio.create_task(simulate_agent())
    await hub._handle(mock_ws)

    # Verify mock_ws sent agents_list and exec_result
    assert mock_ws.send.await_count >= 2
    sent_msgs = [json.loads(call.args[0]) for call in mock_ws.send.await_args_list]
    types = [m.get("type") for m in sent_msgs]
    assert "agents_list" in types
    assert "exec_result" in types


@pytest.mark.asyncio
async def test_remote_hub_client_start_refresh_and_exec():
    from bot.hub import RemoteHubClient

    client = RemoteHubClient(url="ws://127.0.0.1:8765", token="secret")
    mock_ws = AsyncMock()

    msg_queue = asyncio.Queue()

    async def mock_iter():
        while True:
            item = await msg_queue.get()
            if item is None:
                break
            yield item

    mock_ws.__aiter__.side_effect = mock_iter

    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws

        # Start client in background task
        start_task = asyncio.create_task(client.start(timeout=2.0))
        await asyncio.sleep(0.01)

        # Feed message for get_agents via queue
        if client._pending:
            pending_id = next(iter(client._pending.keys()))
            await msg_queue.put(
                json.dumps(
                    {
                        "id": pending_id,
                        "type": "agents_list",
                        "agents": {"node1": {"platform": "Linux", "version": "1.0.0"}},
                        "table": "AGENT node1",
                    }
                )
            )

        await start_task
        assert "node1" in client.agents
        assert client.agents_table() == "AGENT node1"

        # Test exec
        async def respond_exec():
            await asyncio.sleep(0.01)
            if client._pending:
                req_id = next(iter(client._pending.keys()))
                await msg_queue.put(
                    json.dumps(
                        {
                            "id": req_id,
                            "type": "exec_result",
                            "ok": True,
                            "output": "sample output",
                        }
                    )
                )

        asyncio.create_task(respond_exec())
        res = await client.exec("node1", "shell", "echo test", timeout=2)
        assert res == "sample output"

        await msg_queue.put(None)
        await client.stop()


@pytest.mark.asyncio
async def test_hub_on_agent_event_callbacks():
    events = []

    async def mock_event_cb(evt_type, agent_name, hello):
        events.append((evt_type, agent_name, hello.get("agent_connection_notify")))

    hub = Hub(token=None)
    hub.on_agent_event = mock_event_cb

    mock_ws = AsyncMock()
    mock_ws.remote_address = ("192.168.1.100", 54321)
    mock_ws.request.headers = {}
    mock_ws.recv.return_value = json.dumps({
        "type": "hello",
        "name": "test-agent",
        "version": "1.0.0",
        "agent_connection_notify": True,
    })

    async def mock_iter():
        await asyncio.sleep(0.01)
        return
        yield ""

    mock_ws.__aiter__.side_effect = mock_iter

    await hub._handle(mock_ws)

    assert len(events) == 2
    assert events[0] == ("connected", "test-agent", True)
    assert events[1] == ("disconnected", "test-agent", True)


