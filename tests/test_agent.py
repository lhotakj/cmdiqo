"""Unit and integration tests for Agent execution and protocol."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent.agent import (
    AgentConfig,
    ConfigurationError,
    build_argv,
    connect,
    execute,
    format_preflight_error,
    handle,
    main,
)


def test_agent_config_missing_env(monkeypatch):
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="HUB_URL"):
        AgentConfig.from_env()

    monkeypatch.setenv("HUB_URL", "ws://localhost:8765")
    with pytest.raises(ValueError, match="AGENT_TOKEN"):
        AgentConfig.from_env()


def test_agent_config_invalid_scheme(monkeypatch):
    monkeypatch.setenv("HUB_URL", "http://localhost:8765")
    monkeypatch.setenv("AGENT_TOKEN", "test-token")
    with pytest.raises(ConfigurationError, match="Invalid WebSocket scheme"):
        AgentConfig.from_env()


def test_agent_config_nonexistent_file(monkeypatch):
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="Configuration file not found"):
        AgentConfig.load(args=["--config", "nonexistent_file_path_123.json"])


def test_agent_config_malformed_json(tmp_path, monkeypatch):
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Failed to read/parse JSON"):
        AgentConfig.load(args=["--config", str(bad_cfg)])


def test_agent_config_invalid_delay(monkeypatch):
    monkeypatch.setenv("HUB_URL", "ws://localhost:8765")
    monkeypatch.setenv("AGENT_TOKEN", "test-token")
    with pytest.raises(ConfigurationError, match="RECONNECT_DELAY"):
        AgentConfig.load(args=["--reconnect-delay", "-5.0"])


def test_agent_config_success(monkeypatch):
    monkeypatch.setenv("HUB_URL", "ws://192.168.1.100:8765")
    monkeypatch.setenv("AGENT_TOKEN", "test-token-123")
    monkeypatch.setenv("AGENT_NAME", "my-test-host")

    config = AgentConfig.from_env()
    assert config.hub_url == "ws://192.168.1.100:8765"
    assert config.token == "test-token-123"
    assert config.name == "my-test-host"


def test_agent_config_cli_args(monkeypatch):
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    args = [
        "--hub-url",
        "ws://10.0.0.5:8765",
        "-t",
        "cli-token",
        "-n",
        "cli-node",
        "-d",
        "8.0",
        "--log-level",
        "DEBUG",
    ]
    config = AgentConfig.load(args=args)
    assert config.hub_url == "ws://10.0.0.5:8765"
    assert config.token == "cli-token"
    assert config.name == "cli-node"
    assert config.reconnect_delay == 8.0


def test_agent_config_json_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    cfg_file = tmp_path / "agent.json"
    cfg_file.write_text(
        json.dumps(
            {
                "hub_url": "ws://192.168.1.50:8765",
                "token": "file-token",
                "name": "file-node",
                "reconnect_delay": 3.0,
            }
        ),
        encoding="utf-8",
    )

    config = AgentConfig.load(args=["--config", str(cfg_file)])
    assert config.hub_url == "ws://192.168.1.50:8765"
    assert config.token == "file-token"
    assert config.name == "file-node"
    assert config.reconnect_delay == 3.0


def test_build_argv():
    ps_argv = build_argv("ps1", "Get-Process")
    assert any("powershell" in arg or "pwsh" in arg for arg in ps_argv)
    assert "-Command" in ps_argv
    assert "Get-Process" in ps_argv

    shell_argv = build_argv("shell", "echo test")
    assert any("cmd.exe" in arg or "bash" in arg for arg in shell_argv)


def test_connect():
    conn = connect("ws://localhost:8765", "token123")
    assert conn is not None


@pytest.mark.asyncio
async def test_agent_execute_success():
    # Execute a simple echo command across platforms
    msg = {
        "id": "req-1",
        "type": "exec",
        "kind": "shell",
        "cmd": "echo test_output_123",
        "timeout": 10,
    }
    ok, code, output, error = await execute(msg)
    assert ok is True
    assert code == 0
    assert "test_output_123" in output
    assert error is None


@pytest.mark.asyncio
async def test_agent_execute_not_found(monkeypatch):
    monkeypatch.setattr(
        "agent.agent.build_argv", lambda kind, cmd: ["nonexistent_binary_xyz_123"]
    )
    msg = {"id": "req-2", "type": "exec", "kind": "shell", "cmd": "foo"}
    ok, code, _output, error = await execute(msg)
    assert ok is False
    assert code == 127
    assert "executable not found" in error


@pytest.mark.asyncio
async def test_agent_execute_os_error(monkeypatch):
    async def mock_subp(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subp)
    msg = {"id": "req-3", "type": "exec", "kind": "shell", "cmd": "foo"}
    ok, code, _output, error = await execute(msg)
    assert ok is False
    assert code == 1
    assert "OS error" in error


@pytest.mark.asyncio
async def test_agent_execute_timeout(monkeypatch):
    class MockProcess:
        returncode = -1

        async def communicate(self):
            await asyncio.sleep(5)
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            return -1

    async def mock_subp(*args, **kwargs):
        return MockProcess()

    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_subp)
    msg = {
        "id": "req-4",
        "type": "exec",
        "kind": "shell",
        "cmd": "sleep",
        "timeout": 0.05,
    }
    ok, code, _output, error = await execute(msg)
    assert ok is False
    assert code == -1
    assert "timed out" in error


@pytest.mark.asyncio
async def test_agent_handle_message():
    mock_ws = AsyncMock()
    raw = json.dumps(
        {
            "id": "req-100",
            "type": "exec",
            "kind": "shell",
            "cmd": "echo ping",
            "timeout": 5,
        }
    )

    await handle(mock_ws, raw)
    mock_ws.send.assert_awaited_once()

    sent_data = json.loads(mock_ws.send.await_args[0][0])
    assert sent_data["id"] == "req-100"
    assert sent_data["type"] == "result"
    assert sent_data["ok"] is True
    assert sent_data["code"] == 0
    assert "ping" in sent_data["output"]


@pytest.mark.asyncio
async def test_agent_handle_invalid_message():
    mock_ws = AsyncMock()
    # Invalid JSON should not crash
    await handle(mock_ws, "not-json")
    await handle(mock_ws, json.dumps({"type": "not_exec"}))
    mock_ws.send.assert_not_awaited()


def test_format_preflight_error():
    issues = [
        ("TEST_VAR", "Test variable is required", "Provide via TEST_VAR"),
    ]
    formatted = format_preflight_error(issues)
    assert "[SHANGO-AGENT] PREFLIGHT CONFIGURATION CHECK FAILED" in formatted
    assert "* TEST_VAR:" in formatted
    assert "Problem:    Test variable is required" in formatted
    assert "Resolution: Provide via TEST_VAR" in formatted
    assert "Quick setup alternatives:" in formatted


def test_agent_main_configuration_error(capsys, monkeypatch):
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main(argv=[])
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "[SHANGO-AGENT] PREFLIGHT CONFIGURATION CHECK FAILED" in captured.err
    assert "HUB_URL" in captured.err
    assert "AGENT_TOKEN" in captured.err


def test_agent_main_keyboard_interrupt(monkeypatch):
    monkeypatch.setenv("HUB_URL", "ws://localhost:8765")
    monkeypatch.setenv("AGENT_TOKEN", "token")

    def mock_run(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(asyncio, "run", mock_run)

    with pytest.raises(SystemExit) as exc_info:
        main(argv=[])
    assert exc_info.value.code == 0


def test_agent_main_unhandled_exception(monkeypatch):
    monkeypatch.setenv("HUB_URL", "ws://localhost:8765")
    monkeypatch.setenv("AGENT_TOKEN", "token")

    def mock_run(coro):
        coro.close()
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(asyncio, "run", mock_run)

    with pytest.raises(SystemExit) as exc_info:
        main(argv=[])
    assert exc_info.value.code == 1


def test_agent_get_version():
    from agent.agent import get_version

    ver = get_version()
    assert isinstance(ver, str)
    assert len(ver) > 0


def test_agent_version_cli(capsys):
    with pytest.raises(SystemExit) as exc_info:
        AgentConfig.load(args=["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "shango-agent" in captured.out
