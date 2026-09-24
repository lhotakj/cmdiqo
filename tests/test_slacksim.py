"""Unit tests for Slack simulator (slacksim) and terminal formatter."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slacksim.slack_formatter import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_ITALIC,
    ANSI_LIGHT_GRAY,
    ANSI_STRIKE,
    format_slack_to_ansi,
    replace_emojis,
)
from slacksim.slacksim import (
    SimConfig,
    SlackSimulator,
    async_main,
    build_arg_parser,
    run_main,
)


def test_replace_emojis():
    raw = ":white_check_mark: Success! :hourglass_flowing_sand: Running... :gear: Setup"
    out = replace_emojis(raw)
    assert "✅" in out
    assert "⏳" in out
    assert "⚙️" in out
    assert ":white_check_mark:" not in out


def test_format_slack_to_ansi_styling():
    raw = "Here is *bold text* and _italic text_ and ~strike~ and `inline_code`"
    formatted = format_slack_to_ansi(raw, use_color=True)
    assert ANSI_BOLD in formatted
    assert ANSI_ITALIC in formatted
    assert ANSI_STRIKE in formatted
    assert ANSI_CYAN in formatted
    assert "bold text" in formatted
    assert "italic text" in formatted
    assert "inline_code" in formatted


def test_format_slack_to_ansi_code_block():
    raw = ":white_check_mark: Output:\n```\nLine 1: 100%\nLine 2: OK\n```"
    formatted = format_slack_to_ansi(raw, use_color=True)
    assert "✅" in formatted
    assert ANSI_LIGHT_GRAY in formatted
    assert "Line 1: 100%" in formatted
    assert "Line 2: OK" in formatted
    assert "┌" in formatted
    assert "└" in formatted


def test_format_slack_to_ansi_no_color():
    raw = "Here is *bold text* and _italic text_ and `code`"
    formatted = format_slack_to_ansi(raw, use_color=False)
    assert "\033[" not in formatted
    assert "bold text" in formatted
    assert "italic text" in formatted
    assert "`code`" in formatted


def test_format_slack_to_ansi_empty():
    assert format_slack_to_ansi("") == ""


def test_sim_config_defaults():
    config = SimConfig()
    assert config.hub_enabled is True
    assert config.hub_port == 8765
    assert config.sim_user == "U_ADMIN"
    assert config.sim_channel == "C_GENERAL"
    assert config.use_color is True


def test_sim_config_from_env_and_args():
    env = {
        "ALLOWED_USERS": "U111,U222",
        "ALLOWED_CHANNELS": "C111",
        "HUB_PORT": "9000",
        "AGENT_TOKEN": "secret-test",
    }
    with patch.dict(os.environ, env, clear=False):
        parser = build_arg_parser()
        args = parser.parse_args(["--user", "U_CUSTOM", "--channel", "C_CUSTOM", "--no-hub", "--no-color"])
        config = SimConfig.from_env_and_args(args)
        assert config.sim_user == "U_CUSTOM"
        assert config.sim_channel == "C_CUSTOM"
        assert config.hub_enabled is False
        assert config.hub_port == 9000
        assert config.agent_token == "secret-test"
        assert config.use_color is False
        assert "U111" in config.allowed_users


@pytest.mark.asyncio
async def test_slacksim_start_client_mode():
    config = SimConfig(hub_enabled=True, hub_port=8788)
    sim = SlackSimulator(config)
    sim.load_specifications()

    mock_client = AsyncMock()
    with patch("slacksim.slacksim.RemoteHubClient", return_value=mock_client):
        await sim.start()
        assert sim.hub is mock_client
        mock_client.start.assert_awaited_once()
        await sim.stop()
        mock_client.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_slacksim_start_embedded_fallback():
    config = SimConfig(hub_enabled=True, hub_port=8788)
    sim = SlackSimulator(config)
    sim.load_specifications()

    # RemoteHubClient fails to connect -> fallback to Hub
    with patch("slacksim.slacksim.RemoteHubClient") as mock_client_cls:
        mock_client_cls.side_effect = Exception("No hub running")
        with patch("websockets.serve") as mock_ws_serve:
            mock_ws_serve.return_value.__aenter__.return_value = MagicMock()
            await sim.start()
            assert sim.hub is not None
            assert sim._running is True
            await sim.stop()
            assert sim._running is False


@pytest.mark.asyncio
async def test_slacksim_dispatch_meta_commands(capsys):
    config = SimConfig(hub_enabled=False, allowed_users={"U_ADMIN", "U_VALID"})
    sim = SlackSimulator(config)

    # Empty text
    res = await sim.dispatch("")
    assert res is True

    # User switching
    res = await sim.dispatch("/user U_VALID")
    assert res is True
    assert sim.current_user == "U_VALID"
    out = capsys.readouterr().out
    assert "Simulated user switched to" in out
    assert "ALLOWED" in out

    # Channel switching
    res = await sim.dispatch("/channel C_DEVOPS")
    assert res is True
    assert sim.current_channel == "C_DEVOPS"
    out = capsys.readouterr().out
    assert "Simulated channel switched to" in out

    # /whoami
    res = await sim.dispatch("/whoami")
    assert res is True
    out = capsys.readouterr().out
    assert "Simulator Context:" in out
    assert "U_VALID" in out

    # /clear
    res = await sim.dispatch("/clear")
    assert res is True

    # Exit
    res = await sim.dispatch("exit")
    assert res is False
    res_quit = await sim.dispatch("/quit")
    assert res_quit is False


@pytest.mark.asyncio
async def test_slacksim_dispatch_command_execution(capsys):
    config = SimConfig(hub_enabled=False)
    sim = SlackSimulator(config)
    sim.load_specifications()

    # Dispatch /lan version
    res = await sim.dispatch("/lan version")
    assert res is True
    out = capsys.readouterr().out
    assert "shango-bot" in out
    assert "Shango Version" in out


def test_slacksim_print_banner(capsys):
    config = SimConfig(hub_enabled=False)
    sim = SlackSimulator(config)
    sim.load_specifications()
    sim.print_banner()
    out = capsys.readouterr().out
    assert "Shango Slack Simulator" in out
    assert "Active User:" in out


@pytest.mark.asyncio
async def test_slacksim_run_interactive_exit():
    config = SimConfig(hub_enabled=False)
    sim = SlackSimulator(config)
    sim.load_specifications()
    sim._running = True

    # Simulate user typing "exit"
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_input:
        mock_input.side_effect = ["exit"]
        await sim.run_interactive()
    assert sim._running is True


@pytest.mark.asyncio
async def test_async_main_one_shot():
    parser = build_arg_parser()
    args = parser.parse_args(["-c", "/lan version", "--no-hub"])
    code = await async_main(args)
    assert code == 0


def test_run_main_exit():
    with patch("sys.argv", ["slacksim", "-c", "/lan version", "--no-hub"]), pytest.raises(SystemExit) as exc:
        run_main()
    assert exc.value.code == 0
