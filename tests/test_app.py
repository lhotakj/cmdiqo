"""Unit and integration tests for Slack bot application and command dispatching."""

import re
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.app import (
    WINDOWS_COMMAND_FALLBACKS,
    BotConfig,
    BotConfigurationError,
    _run_ps1_local,
    _run_python,
    _run_shell_local,
    create_bot_app,
    execute_command,
    format_bot_preflight_error,
    format_params,
    handle_command,
    help_text,
    load_commands,
    reply_output,
    run_main,
)


def test_help_text():
    commands = [
        {"spec": {"trigger": "/lan devices list", "description": "List devices"}},
        {
            "spec": {
                "trigger": "/host {host} docker restart {service}",
                "description": "Restart service",
            }
        },
    ]
    aliases = [{"name": "home", "input": "/home", "output": "/host home"}]
    txt = help_text(commands, aliases=aliases)
    assert "*Cmdiqo Command Manual*" in txt
    assert "`/lan devices list`" in txt
    assert "`/host {host} docker restart {service}`" in txt
    assert "`/home` → `/host home`" in txt

    # Prefix / category filter
    prefix_txt = help_text(commands, prefix="/lan", aliases=aliases)
    assert "*Cmdiqo Help: `lan`*" in prefix_txt
    assert "`/lan devices list`" in prefix_txt
    assert "`/host {host} docker restart {service}`" not in prefix_txt


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


def test_is_tabular_or_code():
    from bot.app import is_tabular_or_code

    # Rich status updates with emojis and bullets should be plain text
    assert not is_tabular_or_code("✅ 🚫 Application execution BLOCKED on `PC-KIDS`")
    assert not is_tabular_or_code("🛡️ [PC-KIDS] Parental Guard Status: ACTIVE")
    assert not is_tabular_or_code("• Roblox (roblox): 🚫 BLOCKED\n• Steam (steam): ✅ ALLOWED")
    assert not is_tabular_or_code("🔒 Workstation locked.")
    assert not is_tabular_or_code("💬 Message delivered: Hello!")

    # Raw tables, code, logs without emojis should be code blocks
    assert is_tabular_or_code("CONTAINER ID   IMAGE                 STATUS\nb8f9e0a12c4d   plex                  Up 14 days")
    assert is_tabular_or_code("IP ADDRESS     MAC ADDRESS        HOST NAME\n192.168.1.1    aa:bb:cc:dd:ee:ff  router.home")
    assert is_tabular_or_code("short message")
    assert is_tabular_or_code("")


@pytest.mark.asyncio
async def test_reply_output_plain_text_with_emojis():
    post_mock = AsyncMock()
    status_msg = "✅ 🚫 Application execution BLOCKED on `PC-KIDS`"
    await reply_output(post_mock, ":white_check_mark: `/blockapp PC-KIDS all`", status_msg)
    # Must NOT be wrapped in code block triple backticks
    post_mock.assert_awaited_once_with(f":white_check_mark: `/blockapp PC-KIDS all`\n{status_msg}")


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


def test_run_ps1_local():
    out = _run_ps1_local("Write-Output 'hello_cmdiqo'", cwd=None, timeout=5)
    assert "hello_cmdiqo" in out or "Error:" in out


@pytest.mark.asyncio
async def test_execute_command_kinds():
    # Local python without 'run' field
    spec_py = {
        "kind": "python",
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
    mock_hub.exec.assert_awaited_once_with(
        "agent-pc1", "shell", "uptime", cwd=None, timeout=120
    )


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
    assert "*Cmdiqo Command Manual*" in post_mock.await_args[0][0]


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
    assert "Running `/test my-target`" in post_mock.await_args_list[0][0][0]
    assert "Executed with my-target" in post_mock.await_args_list[1][0][0]


@pytest.mark.asyncio
async def test_handle_command_real_lan_devices_list(monkeypatch):
    commands = load_commands("config/commands.yaml")
    monkeypatch.setattr("bot.commands.devices.format_device_table", lambda rows: "MOCK_DEVICE_TABLE")
    posted = []

    async def post(text: str) -> None:
        posted.append(text)

    await handle_command(
        "/lan devices list",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
    )
    assert len(posted) == 2
    assert "Running `/lan devices list`" in posted[0]
    assert "MOCK_DEVICE_TABLE" in posted[1]


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
        {
            "spec": {
                "name": "c1",
                "trigger": "/host {host} docker restart {srv}",
                "description": "restart",
            },
            "regex": re.compile("^$"),
        },
        {
            "spec": {
                "name": "c2",
                "trigger": "/host {host} docker ps",
                "description": "ps",
            },
            "regex": re.compile("^$"),
        },
    ]
    await handle_command("/host", "U1", "C1", post_mock, commands)
    assert post_mock.await_count == 1
    call_text = post_mock.await_args_list[0][0][0]
    assert "*Cmdiqo Help: `host`*" in call_text
    assert "/host {host} docker ps" in call_text


@pytest.mark.asyncio
async def test_handle_command_with_alias_translation():
    post_mock = AsyncMock()
    py = sys.executable.replace("\\", "/")
    spec = {
        "name": "host-docker-ps",
        "trigger": "/host {host} docker ps",
        "kind": "shell",
        "run": f"{py} -c \"print('docker_ps_on_' + '{{host}}')\"",
        "target": "{host}",
    }
    from bot.app import trigger_to_regex

    commands = [{"spec": spec, "regex": trigger_to_regex(spec["trigger"])}]
    aliases = [{"name": "home", "input": "/home", "output": "/host home"}]

    # When user sends "/home docker ps", it should translate to "/host home docker ps"
    # and execute locally via local fallback when no agent named 'home' is on hub
    await handle_command(
        text="/home docker ps",
        user_id="U123",
        channel_id="C123",
        post=post_mock,
        commands=commands,
        aliases=aliases,
    )
    assert post_mock.await_count == 2
    assert (
        "Running `/host home docker ps` on `home`"
        in post_mock.await_args_list[0][0][0]
    )
    assert "docker_ps_on_home" in post_mock.await_args_list[1][0][0]


@pytest.mark.asyncio
async def test_execute_command_local_fallback():
    py = sys.executable.replace("\\", "/")
    # Local fallback when target='local' or 'container'
    spec = {
        "kind": "shell",
        "run": f"{py} -c \"print('fallback_ok')\"",
        "target": "local",
    }
    out = await execute_command(spec, {}, hub=None)
    assert "fallback_ok" in out

    # Local fallback when target='home' and agent 'home' is not on hub
    spec_home = {
        "kind": "shell",
        "run": f"{py} -c \"print('home_fallback_ok')\"",
        "target": "home",
    }
    out_home = await execute_command(spec_home, {}, hub=None)
    assert "home_fallback_ok" in out_home


@pytest.mark.asyncio
async def test_create_bot_app_slash_commands():
    config = BotConfig(
        commands_config_path="config/commands.yaml",
        slack_bot_token="mock-test-bot-token",
        hub_enabled=False,
    )
    app, _hub, _commands = create_bot_app(config)
    # Check registered slash commands and listeners in app (slash /host, /lan, /home, and message event)
    assert len(app._async_listeners) >= 4


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


def test_bot_config_from_env_hub_port_override(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    # 1. Default port is 8765
    monkeypatch.delenv("HUB_PORT", raising=False)
    cfg_default = BotConfig.from_env()
    assert cfg_default.hub_port == 8765

    # 2. HUB_PORT sets port
    monkeypatch.setenv("HUB_PORT", "9000")
    cfg_hub = BotConfig.from_env()
    assert cfg_hub.hub_port == 9000

    # 3. Invalid HUB_PORT causes validation error
    monkeypatch.setenv("HUB_PORT", "invalid")
    cfg_invalid = BotConfig.from_env()
    assert cfg_invalid.hub_port == -1
    with pytest.raises(BotConfigurationError, match="HUB_PORT"):
        cfg_invalid.validate()


def test_bot_config_from_env_web_port(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    # 1. Default web_port is None
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.delenv("WEBGUI", raising=False)
    cfg = BotConfig.from_env()
    assert cfg.web_port is None

    # 2. WEB_PORT configures port
    monkeypatch.setenv("WEB_PORT", "8888")
    cfg_web = BotConfig.from_env()
    assert cfg_web.web_port == 8888

    # 3. Invalid WEB_PORT caught on validate
    cfg_invalid = BotConfig(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        web_port=0,
    )
    with pytest.raises(BotConfigurationError, match="WEB_PORT"):
        cfg_invalid.validate()


def test_format_bot_preflight_error():
    issues = [
        (
            "SLACK_BOT_TOKEN",
            "Slack Bot User OAuth Token is required",
            "Set SLACK_BOT_TOKEN",
        ),
    ]
    formatted = format_bot_preflight_error(issues)
    assert "[CMDIQO-BOT] PREFLIGHT CONFIGURATION CHECK FAILED" in formatted
    assert "* SLACK_BOT_TOKEN:" in formatted
    assert "Slack App Setup Checklist:" in formatted


def test_run_main_configuration_error(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.py"])
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        run_main()
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "[CMDIQO-BOT] PREFLIGHT CONFIGURATION CHECK FAILED" in captured.err
    assert "SLACK_BOT_TOKEN" in captured.err


def test_run_main_keyboard_interrupt(monkeypatch):
    import asyncio

    monkeypatch.setattr(sys, "argv", ["app.py"])
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

    monkeypatch.setattr(sys, "argv", ["app.py"])
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    def mock_run(coro):
        coro.close()
        raise RuntimeError("simulated bot crash")

    monkeypatch.setattr(asyncio, "run", mock_run)

    with pytest.raises(SystemExit) as exc_info:
        run_main()
    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_handle_command_version():
    post_mock = AsyncMock()
    await handle_command("/lan version", "U1", "C1", post_mock, commands=[])
    post_mock.assert_awaited_once()
    assert "Cmdiqo Version" in post_mock.await_args[0][0]
    assert "cmdiqo-bot v" in post_mock.await_args[0][0]


def test_bot_get_version():
    from bot.app import get_version

    ver = get_version()
    assert isinstance(ver, str)
    assert len(ver) > 0


@pytest.mark.asyncio
async def test_handle_command_help_nested():
    from bot.app import load_aliases, load_commands

    commands = load_commands("config/commands.yaml")
    aliases = load_aliases("config/commands.yaml")
    post_mock = AsyncMock()

    # General /help
    await handle_command(
        "/help", "U1", "C1", post_mock, commands=commands, aliases=aliases
    )
    assert post_mock.await_count >= 1
    assert "Cmdiqo Command Manual" in post_mock.call_args_list[0][0][0]

    post_mock.reset_mock()
    # Nested: /help lan
    await handle_command(
        "/help lan", "U1", "C1", post_mock, commands=commands, aliases=aliases
    )
    assert post_mock.await_count == 1
    lan_text = post_mock.await_args[0][0]
    assert "*Cmdiqo Help: `lan`*" in lan_text
    assert "/lan devices list" in lan_text
    assert "/lan wake {target}" in lan_text
    assert "_Examples:_" in lan_text

    post_mock.reset_mock()
    # Nested: /help docker
    await handle_command(
        "/help docker", "U1", "C1", post_mock, commands=commands, aliases=aliases
    )
    assert post_mock.await_count == 1
    docker_text = post_mock.await_args[0][0]
    assert "*Cmdiqo Help: `docker`*" in docker_text
    assert "/host {host} docker ps" in docker_text
    assert "/host {host} docker restart" in docker_text

    post_mock.reset_mock()
    # Nested: /help home (alias topic - may split across blocks if detailed)
    await handle_command(
        "/help home", "U1", "C1", post_mock, commands=commands, aliases=aliases
    )
    assert post_mock.await_count >= 1
    home_text = "".join(call[0][0] for call in post_mock.await_args_list)
    assert "*Matching Aliases:*" in home_text
    assert "`/home` → `/host home`" in home_text

    post_mock.reset_mock()
    # Unknown topic
    await handle_command(
        "/help completely_unknown_xyz",
        "U1",
        "C1",
        post_mock,
        commands=commands,
        aliases=aliases,
    )
    assert post_mock.await_count == 1
    assert (
        ":question: No help found for topic `completely_unknown_xyz`"
        in post_mock.await_args[0][0]
    )


@pytest.mark.asyncio
async def test_handle_command_trailing_help():
    from bot.app import load_aliases, load_commands

    commands = load_commands("config/commands.yaml")
    aliases = load_aliases("config/commands.yaml")
    post_mock = AsyncMock()

    # Test /lan help
    await handle_command("/lan help", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count == 1
    lan_help = post_mock.await_args[0][0]
    assert "*Cmdiqo Help: `lan`*" in lan_help
    assert "/lan devices list" in lan_help

    # Test plain 'lan help' without slash
    post_mock.reset_mock()
    await handle_command("lan help", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count == 1
    assert "*Cmdiqo Help: `lan`*" in post_mock.await_args[0][0]

    # Test /host help
    post_mock.reset_mock()
    await handle_command("/host help", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count >= 1
    host_help = "".join(call[0][0] for call in post_mock.await_args_list)
    assert "*Cmdiqo Help: `host`*" in host_help
    assert "/host {host} uptime" in host_help

    # Test /home help
    post_mock.reset_mock()
    await handle_command("/home help", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count >= 1
    home_help = "".join(call[0][0] for call in post_mock.await_args_list)
    assert "*Cmdiqo Help: `home`*" in home_help
    assert "*Matching Aliases:*" in home_help

    # Test /docker help
    post_mock.reset_mock()
    await handle_command("/docker help", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count == 1
    assert "*Cmdiqo Help: `docker`*" in post_mock.await_args[0][0]

    # Test question mark /lan ?
    post_mock.reset_mock()
    await handle_command("/lan ?", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count == 1
    assert "*Cmdiqo Help: `lan`*" in post_mock.await_args[0][0]

    # Test direct category without slash: 'docker'
    post_mock.reset_mock()
    await handle_command("docker", "U1", "C1", post_mock, commands=commands, aliases=aliases)
    assert post_mock.await_count == 1
    assert "*Cmdiqo Help: `docker`*" in post_mock.await_args[0][0]


@pytest.mark.asyncio
async def test_reply_help_chunking():
    from bot.app import reply_help

    post_mock = AsyncMock()
    # Section smaller than 3500
    await reply_help(post_mock, "Short help text")
    post_mock.assert_awaited_once_with("Short help text")

    post_mock.reset_mock()
    # Big text with multiple sections exceeding 3500 chars
    large_text = ("Section A\n" + "A" * 2500) + "\n\n" + ("Section B\n" + "B" * 2500)
    await reply_help(post_mock, large_text)
    assert post_mock.await_count == 2


def test_format_params_preserves_powershell_braces():
    # Regular parameter replacement
    template = "Get-Service -Name '{service}' | Where-Object {$_.Status -eq 'Running'}"
    formatted = format_params(template, {"service": "wuauserv"})
    assert formatted == "Get-Service -Name 'wuauserv' | Where-Object {$_.Status -eq 'Running'}"

    # String format specifiers {0}, {1}, {4:yyyy-MM-dd...} are left untouched
    ps_fmt = "'Uptime: {0}d {1}h (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f $d, $h on {host}"
    res = format_params(ps_fmt, {"host": "AMD"})
    assert res == "'Uptime: {0}d {1}h (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f $d, $h on AMD"

    # Edge cases
    assert format_params("", {"host": "AMD"}) == ""
    assert format_params("echo test", {}) == "echo test"


@pytest.mark.asyncio
async def test_execute_command_windows_agent_routing():
    mock_hub = AsyncMock()
    mock_hub.agents = {
        "AMD": {"platform": "Windows/11", "ws": MagicMock()},
        "nas": {"platform": "Linux/6.1.0", "ws": MagicMock()},
    }
    mock_hub.exec.return_value = "win_ok"

    # Command with explicit windows_run and windows_kind
    spec_uptime = {
        "name": "host-uptime",
        "kind": "shell",
        "target": "{host}",
        "run": "uptime",
        "windows_kind": "ps1",
        "windows_run": "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; 'Uptime: {0}' -f $b",
    }

    # 1. Routing to Windows agent AMD -> switches to ps1 and windows_run
    res_win = await execute_command(spec_uptime, {"host": "AMD"}, hub=mock_hub)
    assert res_win == "win_ok"
    mock_hub.exec.assert_awaited_with(
        "AMD",
        "ps1",
        "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; 'Uptime: {0}' -f $b",
        cwd=None,
        timeout=120,
    )

    # 2. Routing to Linux agent nas -> keeps shell and run
    mock_hub.exec.reset_mock()
    mock_hub.exec.return_value = "linux_ok"
    res_linux = await execute_command(spec_uptime, {"host": "nas"}, hub=mock_hub)
    assert res_linux == "linux_ok"
    mock_hub.exec.assert_awaited_with(
        "nas",
        "shell",
        "uptime",
        cwd=None,
        timeout=120,
    )

    # 3. Command without windows_run but present in WINDOWS_COMMAND_FALLBACKS (e.g. free -h)
    spec_fallback = {
        "name": "host-memory",
        "kind": "shell",
        "target": "{host}",
        "run": "free -h",
    }
    mock_hub.exec.reset_mock()
    await execute_command(spec_fallback, {"host": "AMD"}, hub=mock_hub)
    expected_kind, expected_cmd = WINDOWS_COMMAND_FALLBACKS["free -h"]
    mock_hub.exec.assert_awaited_with(
        "AMD",
        expected_kind,
        expected_cmd,
        cwd=None,
        timeout=120,
    )


@pytest.mark.asyncio
async def test_execute_command_windows_service_parameter_routing():
    mock_hub = AsyncMock()
    mock_hub.agents = {
        "AMD": {"platform": "Windows/11", "ws": MagicMock()},
    }
    mock_hub.exec.return_value = "service_ok"

    spec_status = {
        "name": "host-service-status",
        "kind": "shell",
        "target": "{host}",
        "run": "systemctl status {service} --no-pager",
        "windows_kind": "ps1",
        "windows_run": "Get-Service -Name '{service}' | Select-Object Name, DisplayName, Status, StartType | Format-List",
    }
    res = await execute_command(spec_status, {"host": "AMD", "service": "Spooler"}, hub=mock_hub)
    assert res == "service_ok"
    mock_hub.exec.assert_awaited_with(
        "AMD",
        "ps1",
        "Get-Service -Name 'Spooler' | Select-Object Name, DisplayName, Status, StartType | Format-List",
        cwd=None,
        timeout=120,
    )


@pytest.mark.asyncio
async def test_execute_command_os_agnostic_command_mapping():
    mock_hub = AsyncMock()
    mock_hub.agents = {
        "win-node": {"platform": "Windows/11"},
        "linux-node": {"platform": "Linux/6.1.0"},
    }
    mock_hub.exec.return_value = "exec_ok"

    spec = {
        "name": "host-test-uptime",
        "trigger": "/host {host} uptime",
        "command": {
            "shell": "uptime",
            "ps1": "Get-Uptime",
        },
    }

    # 1. Routing to Linux agent
    res_linux = await execute_command(spec, {"host": "linux-node"}, hub=mock_hub)
    assert res_linux == "exec_ok"
    mock_hub.exec.assert_awaited_with("linux-node", "shell", "uptime", cwd=None, timeout=120)

    # 2. Routing to Windows agent
    mock_hub.exec.reset_mock()
    res_win = await execute_command(spec, {"host": "win-node"}, hub=mock_hub)
    assert res_win == "exec_ok"
    mock_hub.exec.assert_awaited_with("win-node", "ps1", "Get-Uptime", cwd=None, timeout=120)


@pytest.mark.asyncio
async def test_execute_command_os_specific_unsupported_warning():
    mock_hub = AsyncMock()
    mock_hub.agents = {
        "win-node": {"platform": "Windows/11"},
        "linux-node": {"platform": "Linux/6.1.0"},
    }

    # Shell-only command (e.g. docker) on Windows
    spec_docker = {
        "name": "host-docker-ps",
        "trigger": "/host {host} docker ps",
        "command": {
            "shell": "docker ps",
        },
    }
    res_docker_win = await execute_command(spec_docker, {"host": "win-node"}, hub=mock_hub)
    assert res_docker_win == "ℹ️ Sorry, this command is not available for the target OS"

    # PS1-only command (e.g. win updates) on Linux
    spec_win = {
        "name": "host-win-updates",
        "trigger": "/host {host} win updates",
        "command": {
            "ps1": "Get-HotFix",
        },
    }
    res_win_linux = await execute_command(spec_win, {"host": "linux-node"}, hub=mock_hub)
    assert res_win_linux == "ℹ️ Sorry, this command is not available for the target OS"


@pytest.mark.asyncio
async def test_execute_command_with_general_config():
    spec = {
        "name": "custom-cmd",
        "command": {
            "shell": "echo {config.key} and {config.number}",
        },
        "config": {
            "key": "custom_val",
            "number": 42,
            "items": ["a", "b"],
        },
    }
    formatted = format_params(spec["command"]["shell"], {}, config=spec["config"])
    assert formatted == "echo custom_val and 42"


@pytest.mark.asyncio
async def test_handle_command_implicit_host_targeting():
    post_mock = AsyncMock()
    mock_hub = AsyncMock()
    mock_hub.agents = {"node1": {"platform": "Linux/6.1.0"}}
    mock_hub.exec.return_value = "uptime_123"

    spec = {
        "name": "host-uptime",
        "trigger": "/host {host} uptime",
        "command": {
            "shell": "uptime",
            "ps1": "Get-Uptime",
        },
    }
    from bot.app import trigger_to_regex

    commands = [{"spec": spec, "regex": trigger_to_regex(spec["trigger"])}]

    await handle_command(
        text="/host node1 uptime",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=mock_hub,
    )

    assert post_mock.await_count == 2
    assert "Running `/host node1 uptime` on `node1`" in post_mock.await_args_list[0][0][0]
    assert "uptime_123" in post_mock.await_args_list[1][0][0]
    mock_hub.exec.assert_awaited_once_with("node1", "shell", "uptime", cwd=None, timeout=120)


@pytest.mark.asyncio
async def test_agent_event_notification_logs_at_debug(caplog):
    cfg = BotConfig(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        agent_token="",
        hub_port=8765,
        hub_host="127.0.0.1",
        allowed_channels={"C_TEST"},
    )
    mock_app, mock_hub, _ = create_bot_app(cfg)
    mock_app.client.chat_postMessage = AsyncMock()

    with caplog.at_level("DEBUG"):
        await mock_hub.on_agent_event("connected", "rpi5", {"agent_connection_notify": True})

    debug_records = [
        r for r in caplog.records
        if r.levelname == "DEBUG" and "Successfully posted agent event notification" in r.message
    ]
    info_records = [
        r for r in caplog.records
        if r.levelname == "INFO" and "Successfully posted agent event notification" in r.message
    ]
    assert len(debug_records) == 1
    assert len(info_records) == 0
    assert "channel C_TEST" in debug_records[0].message



