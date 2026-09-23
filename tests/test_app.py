"""Unit and integration tests for Slack bot application and command dispatching."""

import re
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.app import (
    BotConfig,
    BotConfigurationError,
    _run_python,
    _run_shell_local,
    create_bot_app,
    execute_command,
    format_bot_preflight_error,
    handle_command,
    help_text,
    reply_output,
    run_main,
)


def test_help_text():
    commands = [
        {"spec": {"trigger": "/lan devices list", "description": "List devices"}},
        {"spec": {"trigger": "/home docker restart {service}", "description": "Restart service"}},
    ]
    txt = help_text(commands)
    assert "*Available commands:*" in txt
    assert "`/lan devices list`" in txt
    assert "`/home docker restart {service}`" in txt

    # Prefix filter
    prefix_txt = help_text(commands, prefix="/lan")
    assert "`/lan devices list`" in prefix_txt
    assert "`/home docker restart {service}`" not in prefix_txt


@pytest.mark.asyncio
async def test_reply_output_chunks():
    post_mock = AsyncMock()
    # Output smaller than chunk limit
    await reply_output(post_mock, ":star: Title", "short message")
    post_mock.assert_awaited_once_with(":star: Title\n```short message```")

    post_mock.reset_mock()
    # Large output exceeding 3500 chars
    large_message = "A" * 7500
    await reply_output(post_mock, ":star: Title", large_message)
    assert post_mock.await_count == 3  # 3500, 3500, 500

    post_mock.reset_mock()
    # Empty output
    await reply_output(post_mock, ":star: Title", "")
    post_mock.assert_awaited_once_with(":star: Title\n```(no output)```")


def test_run_python_handler():
    spec = {"handler": "commands.devices:format_device_table"}
    out = _run_python(spec, {})
    assert "No devices discovered." in out


def test_run_shell_local():
    py = sys.executable.replace("\\", "/")
    out = _run_shell_local(f"{py} -c print(12345)", cwd=None, timeout=5)
    assert "12345" in out

    err_out = _run_shell_local("nonexistent_shell_cmd_123", cwd=None, timeout=5)
    assert "Error:" in err_out


@pytest.mark.asyncio
async def test_execute_command_kinds():
    # Local python
    spec_py = {
        "kind": "python",
        "run": "",
        "handler": "commands.devices:format_device_table",
    }
    out_py = await execute_command(spec_py, {})
    assert "No devices discovered." in out_py

    # Local shell
    py = sys.executable.replace("\\", "/")
    spec_sh = {
        "kind": "shell",
        "run": f"{py} -c print({{val}})",
    }
    out_sh = await execute_command(spec_sh, {"val": "123"})
    assert "123" in out_sh

    # ps1 without target should warn
    spec_ps = {"kind": "ps1", "run": "Get-Process"}
    out_ps = await execute_command(spec_ps, {})
    assert "requires a 'target'" in out_ps


@pytest.mark.asyncio
async def test_execute_command_target_with_hub(monkeypatch):
    mock_hub = AsyncMock()
    mock_hub.exec.return_value = "hub_agent_ok"

    spec_target = {
        "kind": "shell",
        "target": "agent-{host}",
        "run": "uptime",
    }
    res = await execute_command(spec_target, {"host": "pc1"}, hub=mock_hub)
    assert res == "hub_agent_ok"
    mock_hub.exec.assert_awaited_once_with("agent-pc1", "shell", "uptime", cwd=None, timeout=120)


@pytest.mark.asyncio
async def test_execute_command_target_hub_disabled():
    spec_target = {"kind": "shell", "target": "agent1", "run": "uptime"}
    with pytest.raises(RuntimeError, match="hub is disabled"):
        await execute_command(spec_target, {}, hub=None)


@pytest.mark.asyncio
async def test_handle_command_user_not_allowed():
    post_mock = AsyncMock()
    commands = []
    allowed = {"U12345"}

    await handle_command(
        text="/lan devices list",
        user_id="U99999",  # Unauthorized user
        channel_id="C111",
        post=post_mock,
        commands=commands,
        allowed_users=allowed,
    )
    post_mock.assert_awaited_once()
    assert "You are not authorized" in post_mock.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_command_channel_not_allowed():
    post_mock = AsyncMock()
    commands = []
    allowed_channels = {"C_ALLOWED"}

    await handle_command(
        text="/lan devices list",
        user_id="U12345",
        channel_id="C_FORBIDDEN",
        post=post_mock,
        commands=commands,
        allowed_channels=allowed_channels,
    )
    post_mock.assert_awaited_once()
    assert "channel is not allowed" in post_mock.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_command_empty():
    post_mock = AsyncMock()
    await handle_command(
        text="   ",
        user_id="U123",
        channel_id="C123",
        post=post_mock,
        commands=[],
    )
    post_mock.assert_awaited_once()
    assert "*Available commands:*" in post_mock.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_command_lan_agents_builtin():
    post_mock = AsyncMock()
    mock_hub = AsyncMock()
    mock_hub.agents_table = MagicMock(return_value="host1 | Linux")

    await handle_command(
        text="/lan agents list",
        user_id="U123",
        channel_id="C123",
        post=post_mock,
        commands=[],
        hub=mock_hub,
    )
    post_mock.assert_awaited_once()
    assert "Connected agents" in post_mock.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_command_unknown():
    post_mock = AsyncMock()
    commands = []

    await handle_command(
        text="/unknown command",
        user_id="U123",
        channel_id="C123",
        post=post_mock,
        commands=commands,
    )
    post_mock.assert_awaited_once()
    assert "Unknown command `/unknown command`" in post_mock.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_command_execution(monkeypatch):
    post_mock = AsyncMock()
    spec = {
        "name": "test-command",
        "trigger": "/test {name}",
        "kind": "python",
        "handler": "commands.devices:list_devices",
    }
    from bot.app import trigger_to_regex

    commands = [{"spec": spec, "regex": trigger_to_regex(spec["trigger"])}]

    # Mock execute_command
    async def mock_exec(cmd_spec, params, hub=None, default_timeout=120):
        return f"Executed with {params['name']}"

    monkeypatch.setattr("bot.app.execute_command", mock_exec)

    await handle_command(
        text="/test my-target",
        user_id="U123",
        channel_id="C123",
        post=post_mock,
        commands=commands,
    )

    assert post_mock.await_count == 2
    assert "Running `/test {name}`" in post_mock.await_args_list[0][0][0]
    assert "Executed with my-target" in post_mock.await_args_list[1][0][0]


def test_create_bot_app_factory():
    config = BotConfig(
        commands_config_path="config/commands.yaml",
        slack_bot_token="mock-test-bot-token",
        hub_enabled=True,
    )
    app, hub, commands = create_bot_app(config)
    assert app is not None
    assert hub is not None
    assert len(commands) >= 3


def test_bot_config_from_env(monkeypatch):
    monkeypatch.setenv("COMMANDS_CONFIG", "config/custom.yaml")
    monkeypatch.setenv("SHELL_TIMEOUT", "60")
    monkeypatch.setenv("ALLOWED_USERS", "U1, U2")
    monkeypatch.setenv("ALLOWED_CHANNELS", "C1, C2")
    monkeypatch.setenv("HUB_ENABLED", "false")
    monkeypatch.setenv("HUB_HOST", "127.0.0.1")
    monkeypatch.setenv("HUB_PORT", "9999")
    monkeypatch.setenv("AGENT_TOKEN", "agent-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "bot-tok")
    monkeypatch.setenv("SLACK_APP_TOKEN", "app-tok")

    cfg = BotConfig.from_env()
    assert cfg.commands_config_path == "config/custom.yaml"
    assert cfg.shell_timeout == 60
    assert cfg.allowed_users == {"U1", "U2"}
    assert cfg.allowed_channels == {"C1", "C2"}
    assert cfg.hub_enabled is False
    assert cfg.hub_host == "127.0.0.1"
    assert cfg.hub_port == 9999
    assert cfg.agent_token == "agent-secret"
    assert cfg.slack_bot_token == "bot-tok"
    assert cfg.slack_app_token == "app-tok"


def test_create_bot_app_default_env(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("HUB_ENABLED", "false")
    app, hub, _ = create_bot_app(None)
    assert app is not None
    assert hub is None


def test_run_shell_local_timeout(monkeypatch):
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="test", timeout=2)
    monkeypatch.setattr(subprocess, "run", mock_run)
    res = _run_shell_local("echo hi", cwd=None, timeout=2)
    assert "timed out after 2s" in res


def test_run_shell_local_os_error(monkeypatch):
    def mock_run(*args, **kwargs):
        raise OSError("Permission denied")
    monkeypatch.setattr(subprocess, "run", mock_run)
    res = _run_shell_local("echo hi", cwd=None, timeout=2)
    assert "Error executing command: Permission denied" in res


@pytest.mark.asyncio
async def test_execute_command_cwd_formatting():
    spec = {
        "kind": "shell",
        "run": "echo hello",
        "cwd": "/tmp/{target}",
    }
    with pytest.MonkeyPatch.context() as mp:
        recorded_cwd = []
        def mock_shell(cmdline, cwd, timeout):
            recorded_cwd.append(cwd)
            return "ok"
        mp.setattr("bot.app._run_shell_local", mock_shell)
        res = await execute_command(spec, {"target": "mybox"})
        assert res == "ok"
        assert recorded_cwd == ["/tmp/mybox"]


@pytest.mark.asyncio
async def test_handle_command_exception_reporting():
    post_mock = AsyncMock()
    spec = {
        "name": "failing-cmd",
        "trigger": "/fail",
        "kind": "shell",
        "run": "exit 1",
    }
    from bot.app import trigger_to_regex
    commands = [{"spec": spec, "regex": trigger_to_regex(spec["trigger"])}]

    async def failing_exec(*args, **kwargs):
        raise RuntimeError("Something exploded")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("bot.app.execute_command", failing_exec)
        await handle_command("/fail", "U1", "C1", post_mock, commands)

    assert post_mock.await_count == 2
    assert "Error: Something exploded" in post_mock.await_args_list[1][0][0]


@pytest.mark.asyncio
async def test_handle_command_prefix_filtering():
    post_mock = AsyncMock()
    commands = [
        {"spec": {"name": "c1", "trigger": "/home docker restart {srv}", "description": "restart"}, "regex": re.compile("^$")},
        {"spec": {"name": "c2", "trigger": "/home docker ps", "description": "ps"}, "regex": re.compile("^$")},
    ]
    await handle_command("/home docker", "U1", "C1", post_mock, commands)
    assert post_mock.await_count == 1
    call_text = post_mock.await_args_list[0][0][0]
    assert "*Available commands:*" in call_text
    assert "/home docker ps" in call_text


@pytest.mark.asyncio
async def test_main_missing_tokens(monkeypatch):
    from bot.app import main
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        await main()

    monkeypatch.setenv("SLACK_BOT_TOKEN", "valid-bot-token")
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
        await main()


def test_bot_config_validate_invalid_port():
    cfg = BotConfig(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        hub_enabled=True,
        hub_port=0,
    )
    with pytest.raises(BotConfigurationError, match="HUB_PORT"):
        cfg.validate()


def test_format_bot_preflight_error():
    issues = [
        ("SLACK_BOT_TOKEN", "Slack Bot User OAuth Token is required", "Set SLACK_BOT_TOKEN"),
    ]
    formatted = format_bot_preflight_error(issues)
    assert "[SHANGO-BOT] PREFLIGHT CONFIGURATION CHECK FAILED" in formatted
    assert "* SLACK_BOT_TOKEN:" in formatted
    assert "Slack App Setup Checklist:" in formatted


def test_run_main_configuration_error(capsys, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        run_main()
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "[SHANGO-BOT] PREFLIGHT CONFIGURATION CHECK FAILED" in captured.err
    assert "SLACK_BOT_TOKEN" in captured.err


def test_run_main_keyboard_interrupt(monkeypatch):
    import asyncio
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    def mock_run(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(asyncio, "run", mock_run)

    with pytest.raises(SystemExit) as exc_info:
        run_main()
    assert exc_info.value.code == 0


def test_run_main_unhandled_exception(monkeypatch):
    import asyncio
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    def mock_run(coro):
        coro.close()
        raise RuntimeError("simulated bot crash")

    monkeypatch.setattr(asyncio, "run", mock_run)

    with pytest.raises(SystemExit) as exc_info:
        run_main()
    assert exc_info.value.code == 1
