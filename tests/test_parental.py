"""Unit and integration tests for parental control command handler (Windows & Linux)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.app import handle_command, load_commands
from bot.commands.parental import (
    AI_DOMAINS,
    DISCORD_DOMAINS,
    GAMING_BYPASS_DOMAINS,
    INSTAGRAM_DOMAINS,
    MINECRAFT_DOMAINS,
    PARENTAL_DOMAINS,
    PROXY_DOMAINS,
    REDDIT_DOMAINS,
    ROBLOX_DOMAINS,
    TIKTOK_DOMAINS,
    TWITCH_DOMAINS,
    YOUTUBE_DOMAINS,
    build_parental_script_linux,
    build_parental_script_windows,
    manage_parental,
)


def test_parental_domains():
    assert len(YOUTUBE_DOMAINS) >= 10
    assert len(ROBLOX_DOMAINS) >= 5
    assert len(MINECRAFT_DOMAINS) >= 30
    assert len(DISCORD_DOMAINS) >= 10
    assert len(TIKTOK_DOMAINS) >= 4
    assert len(TWITCH_DOMAINS) >= 4
    assert len(INSTAGRAM_DOMAINS) >= 3
    assert len(REDDIT_DOMAINS) >= 3
    assert len(AI_DOMAINS) >= 2
    assert len(GAMING_BYPASS_DOMAINS) >= 4
    assert len(PROXY_DOMAINS) >= 3
    assert "youtube.com" in PARENTAL_DOMAINS
    assert "www.youtube.com" in PARENTAL_DOMAINS
    assert "youtubekids.com" in PARENTAL_DOMAINS
    assert "roblox.com" in PARENTAL_DOMAINS
    assert "www.roblox.com" in PARENTAL_DOMAINS
    assert "minecraft.net" in PARENTAL_DOMAINS
    assert "mojang.com" in PARENTAL_DOMAINS
    assert "login.live.com" in PARENTAL_DOMAINS
    assert "discord.com" in PARENTAL_DOMAINS
    assert "discord.gg" in PARENTAL_DOMAINS
    assert "tiktok.com" in PARENTAL_DOMAINS
    assert "twitch.tv" in PARENTAL_DOMAINS
    assert "instagram.com" in PARENTAL_DOMAINS
    assert "reddit.com" in PARENTAL_DOMAINS
    assert "character.ai" in PARENTAL_DOMAINS
    assert "now.gg" in PARENTAL_DOMAINS
    assert "poki.com" in PARENTAL_DOMAINS
    assert "croxyproxy.com" in PARENTAL_DOMAINS


def test_build_parental_script_windows_on():
    script = build_parental_script_windows("on", ["youtube.com", "roblox.com"])
    assert "$action = 'on'.ToLower()" in script
    assert "# parental:start" in script
    assert "# parental:end" in script
    assert "0.0.0.0 $d" in script
    assert "Clear-DnsClientCache" in script
    assert "ipconfig /flushdns" in script


def test_build_parental_script_windows_off():
    script = build_parental_script_windows("off", ["youtube.com", "roblox.com"])
    assert "$action = 'off'.ToLower()" in script
    assert "# parental:start" in script
    assert "# parental:end" in script
    assert "# 0.0.0.0 $d" in script


def test_build_parental_script_windows_status():
    script = build_parental_script_windows("status", ["youtube.com"])
    assert "$action = 'status'.ToLower()" in script
    assert 'Write-Output "STATUS:NOT_CONFIGURED"' in script


def test_build_parental_script_linux_on():
    script = build_parental_script_linux("on", ["youtube.com", "roblox.com"])
    assert 'HOSTS_FILE="/etc/hosts"' in script
    assert 'ACTION="on"' in script
    assert "0.0.0.0 youtube.com" in script
    assert "0.0.0.0 roblox.com" in script
    assert "# parental:start" in script
    assert "# parental:end" in script
    assert "systemd-resolve --flush-caches" in script


def test_build_parental_script_linux_off():
    script = build_parental_script_linux("off", ["youtube.com", "roblox.com"])
    assert 'ACTION="off"' in script
    assert "# 0.0.0.0 youtube.com" in script
    assert "# 0.0.0.0 roblox.com" in script


def test_build_parental_script_linux_status():
    script = build_parental_script_linux("status", ["youtube.com"])
    assert 'ACTION="status"' in script
    assert 'echo "STATUS:NOT_CONFIGURED"' in script


@pytest.mark.asyncio
async def test_manage_parental_validation():
    # Missing host
    res_no_host = await manage_parental({"host": "", "action": "on"})
    assert "Target host is required" in res_no_host

    # Invalid action
    res_inv = await manage_parental({"host": "AMD", "action": "invalid"})
    assert "Invalid action" in res_inv

    # Hub is None for remote host
    res_no_hub = await manage_parental({"host": "AMD", "action": "on"}, hub=None)
    assert "Hub is disabled" in res_no_hub


@pytest.mark.asyncio
async def test_manage_parental_host_not_connected():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {}}
    res = await manage_parental({"host": "AMD", "action": "on"}, hub=mock_hub)
    assert "Agent `AMD` is not connected" in res


@pytest.mark.asyncio
async def test_manage_parental_windows_agent_success():
    mock_hub = MagicMock()
    mock_hub.agents = {"AMD": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:23")

    res = await manage_parental({"host": "AMD", "action": "on"}, hub=mock_hub)
    assert "Parental controls ENABLED on `AMD`" in res
    assert f"{len(PARENTAL_DOMAINS)} domains" in res
    mock_hub.exec.assert_awaited_once()
    # Ensure kind='ps1' was dispatched for Windows
    assert mock_hub.exec.await_args[1]["kind"] == "ps1"


@pytest.mark.asyncio
async def test_manage_parental_linux_agent_success():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:23")

    res = await manage_parental({"host": "nas", "action": "on"}, hub=mock_hub)
    assert "Parental controls ENABLED on `nas`" in res
    assert "Linux (`nas` /etc/hosts)" in res
    mock_hub.exec.assert_awaited_once()
    # Ensure kind='shell' was dispatched for Linux
    assert mock_hub.exec.await_args[1]["kind"] == "shell"


@pytest.mark.asyncio
async def test_manage_parental_linux_agent_off():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:off:23")

    res = await manage_parental({"host": "nas", "action": "off"}, hub=mock_hub)
    assert "Parental controls DISABLED on `nas`" in res
    assert "unblocked" in res.lower()
    assert mock_hub.exec.await_args[1]["kind"] == "shell"


@pytest.mark.asyncio
async def test_manage_parental_status_reporting():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}

    # Status: ON
    mock_hub.exec = AsyncMock(return_value="STATUS:ON:23")
    res_on = await manage_parental({"host": "nas", "action": "status"}, hub=mock_hub)
    assert "ACTIVE" in res_on
    assert "BLOCKED" in res_on

    # Status: OFF
    mock_hub.exec = AsyncMock(return_value="STATUS:OFF:23")
    res_off = await manage_parental({"host": "nas", "action": "status"}, hub=mock_hub)
    assert "INACTIVE" in res_off
    assert "UNBLOCKED" in res_off

    # Status: NOT_CONFIGURED
    mock_hub.exec = AsyncMock(return_value="STATUS:NOT_CONFIGURED")
    res_none = await manage_parental({"host": "nas", "action": "status"}, hub=mock_hub)
    assert "NOT CONFIGURED" in res_none


@pytest.mark.asyncio
async def test_manage_parental_local_host():
    with patch("bot.commands.parental._run_local_bash", new=AsyncMock(return_value="SUCCESS:on:23")):
        res = await manage_parental({"host": "local", "action": "on"})
        assert "Parental controls ENABLED on `local`" in res
        assert "local /etc/hosts" in res


@pytest.mark.asyncio
async def test_manage_parental_exec_exception():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}
    mock_hub.exec = AsyncMock(side_effect=RuntimeError("connection dropped"))

    res = await manage_parental({"host": "nas", "action": "on"}, hub=mock_hub)
    assert "Failed to execute parental command" in res
    assert "connection dropped" in res


@pytest.mark.asyncio
async def test_handle_command_parental_linux_integration():
    commands = load_commands("config/commands.yaml")
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:23")

    posted = []

    async def post(text: str) -> None:
        posted.append(text)

    await handle_command(
        "/host nas parental on",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )

    assert len(posted) == 2
    assert "Running `/host nas parental on` on `nas`" in posted[0]
    assert "Parental controls ENABLED on `nas`" in posted[1]
    assert "Linux (`nas` /etc/hosts)" in posted[1]


@pytest.mark.asyncio
async def test_run_local_bash_errors():
    from bot.commands.parental import _run_local_bash

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        err = await _run_local_bash("echo test")
        assert "bash not found" in err

    with patch("asyncio.create_subprocess_exec", side_effect=OSError("spawn error")):
        err_os = await _run_local_bash("echo test")
        assert "spawn error" in err_os

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        err_rc = await _run_local_bash("echo test")
        assert "permission denied" in err_rc


@pytest.mark.asyncio
async def test_manage_parental_with_custom_config_domains():
    custom_domains = ["custom-game.net", "custom-video.com", "social-distraction.app"]
    cfg = {"domains": custom_domains}

    # Test Linux agent execution with custom config
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}
    captured_cmd = []

    async def mock_exec(host, kind, cmd, timeout=60):
        captured_cmd.append((host, kind, cmd))
        return "SUCCESS:on:3"

    mock_hub.exec = AsyncMock(side_effect=mock_exec)

    res = await manage_parental({"host": "nas", "action": "on"}, hub=mock_hub, config=cfg)
    assert "Parental controls ENABLED on `nas`" in res
    assert "**Blocked:** 3 domains" in res
    assert len(captured_cmd) == 1
    host, kind, script = captured_cmd[0]
    assert host == "nas"
    assert kind == "shell"
    for d in custom_domains:
        assert f"0.0.0.0 {d}" in script

    # Test Windows agent execution with custom config
    mock_hub.agents = {"winpc": {"platform": "Windows/11"}}
    captured_cmd.clear()

    async def mock_win_exec(host, kind, cmd, timeout=60):
        captured_cmd.append((host, kind, cmd))
        return "SUCCESS:on:3"

    mock_hub.exec = AsyncMock(side_effect=mock_win_exec)

    res_win = await manage_parental({"host": "winpc", "action": "on"}, hub=mock_hub, config=cfg)
    assert "Parental controls ENABLED on `winpc`" in res_win
    assert len(captured_cmd) == 1
    win_host, win_kind, win_script = captured_cmd[0]
    assert win_host == "winpc"
    assert win_kind == "ps1"
    for d in custom_domains:
        assert f"'{d}'" in win_script


