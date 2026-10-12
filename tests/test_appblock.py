"""Unit and integration tests for application execution blocking command handler (Windows & Linux)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.app import handle_command, load_commands
from bot.commands.appblock import (
    DEFAULT_APPS,
    _run_local_bash,
    build_appblock_script_linux,
    build_appblock_script_windows,
    manage_appblock,
)


def test_default_apps_structure():
    assert "roblox" in DEFAULT_APPS
    assert "steam" in DEFAULT_APPS
    assert "epic" in DEFAULT_APPS
    assert "discord" in DEFAULT_APPS
    assert "minecraft" in DEFAULT_APPS
    assert "riot" in DEFAULT_APPS
    assert "battlenet" in DEFAULT_APPS
    assert "tiktok" in DEFAULT_APPS
    assert "gfn" in DEFAULT_APPS
    assert "tlauncher" in DEFAULT_APPS

    roblox = DEFAULT_APPS["roblox"]
    assert "RobloxPlayerBeta.exe" in roblox["windows"]["executables"]
    assert "Bloxstrap.exe" in roblox["windows"]["executables"]
    assert "ROBLOXCORPORATION.ROBLOX" in roblox["windows"]["appx_packages"]
    assert "org.sober.Sober" in roblox["linux"]["flatpaks"]
    assert "org.vinegarhq.Vinegar" in roblox["linux"]["flatpaks"]
    assert "sober" in roblox["linux"]["process_names"]

    minecraft = DEFAULT_APPS["minecraft"]
    assert "TLauncher.exe" in minecraft["windows"]["executables"]

    tlauncher = DEFAULT_APPS["tlauncher"]
    assert "TLauncher.exe" in tlauncher["windows"]["executables"]
    assert "TLauncherBeta.exe" in tlauncher["windows"]["executables"]
    assert "tlauncher.exe" in tlauncher["windows"]["executables"]
    assert "TLauncher" in tlauncher["linux"]["process_names"]
    assert "TLauncher.jar" in tlauncher["linux"]["process_names"]

    riot = DEFAULT_APPS["riot"]
    assert "RiotClientServices.exe" in riot["windows"]["executables"]
    assert "VALORANT.exe" in riot["windows"]["executables"]

    battlenet = DEFAULT_APPS["battlenet"]
    assert "Battle.net.exe" in battlenet["windows"]["executables"]
    assert "net.battle.BattleNet" in battlenet["linux"]["flatpaks"]

    tiktok = DEFAULT_APPS["tiktok"]
    assert "ByteDance.TikTok" in tiktok["windows"]["appx_packages"]

    gfn = DEFAULT_APPS["gfn"]
    assert "GeForceNOW.exe" in gfn["windows"]["executables"]
    assert "com.nvidia.geforcenow" in gfn["linux"]["flatpaks"]


def test_build_appblock_script_windows_on():
    target = {"roblox": DEFAULT_APPS["roblox"]}
    script = build_appblock_script_windows("on", target)
    assert "$action = 'on'.ToLower()" in script
    assert "Image File Execution Options" in script
    assert "Set-ItemProperty" in script
    assert "Debugger" in script
    assert "systray.exe" in script
    assert "Set-AppxPackage -Status Blocked" in script
    assert "Stop-Process" in script
    assert "Write-Output ('SUCCESS:on:' + $appKey)" in script
    assert '"' not in script


def test_build_appblock_script_windows_off():
    target = {"roblox": DEFAULT_APPS["roblox"]}
    script = build_appblock_script_windows("off", target)
    assert "$action = 'off'.ToLower()" in script
    assert "Remove-Item -Path $keyPath -Recurse -Force" in script
    assert "Set-AppxPackage -Status Normal" in script
    assert "Write-Output ('SUCCESS:off:' + $appKey)" in script
    assert '"' not in script


def test_build_appblock_script_windows_status():
    target = {"roblox": DEFAULT_APPS["roblox"], "steam": DEFAULT_APPS["steam"]}
    script = build_appblock_script_windows("status", target)
    assert "$action = 'status'.ToLower()" in script
    assert "Write-Output ('STATUS:' + $appKey + ':BLOCKED')" in script
    assert "Write-Output ('STATUS:' + $appKey + ':ALLOWED')" in script
    assert '"' not in script


def test_build_appblock_script_linux_on():
    target = {"roblox": DEFAULT_APPS["roblox"]}
    script = build_appblock_script_linux("on", target)
    assert 'ACTION="on"' in script
    assert "flatpak override --system --nosocket=wayland" in script
    assert "pkill -9 -f" in script
    assert "org.sober.Sober" in script
    assert 'echo "SUCCESS:on:roblox"' in script


def test_build_appblock_script_linux_off():
    target = {"roblox": DEFAULT_APPS["roblox"]}
    script = build_appblock_script_linux("off", target)
    assert 'ACTION="off"' in script
    assert "flatpak override --system --reset" in script
    assert "org.sober.Sober" in script
    assert 'echo "SUCCESS:off:roblox"' in script


def test_build_appblock_script_linux_status():
    target = {"roblox": DEFAULT_APPS["roblox"]}
    script = build_appblock_script_linux("status", target)
    assert 'ACTION="status"' in script
    assert '/var/lib/flatpak/overrides/$fp' in script
    assert 'echo "STATUS:roblox:BLOCKED"' in script
    assert 'echo "STATUS:roblox:ALLOWED"' in script


@pytest.mark.asyncio
async def test_manage_appblock_validation():
    # Missing host
    res = await manage_appblock({"host": "", "app": "roblox", "action": "on"})
    assert "Target host is required" in res

    # Missing app
    res = await manage_appblock({"host": "PC-KIDS", "app": "", "action": "on"})
    assert "Application identifier is required" in res

    # Invalid action
    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "invalid"})
    assert "Invalid action" in res

    # Unknown app
    res = await manage_appblock({"host": "PC-KIDS", "app": "nonexistent", "action": "on"})
    assert "Unknown application `nonexistent`" in res
    assert "Available apps" in res

    # Hub disabled
    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "on"}, hub=None)
    assert "Hub is disabled" in res


@pytest.mark.asyncio
async def test_manage_appblock_host_not_connected():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {}}
    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "on"}, hub=mock_hub)
    assert "Agent `PC-KIDS` is not connected" in res


@pytest.mark.asyncio
async def test_manage_appblock_windows_agent_on():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:roblox")

    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "on"}, hub=mock_hub)
    assert "Application execution BLOCKED on `PC-KIDS`" in res
    assert "Roblox" in res
    assert "Windows (`PC-KIDS` IFEO/AppX)" in res
    mock_hub.exec.assert_awaited_once()
    assert mock_hub.exec.await_args[1]["kind"] == "ps1"


@pytest.mark.asyncio
async def test_manage_appblock_windows_agent_off():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:off:roblox")

    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "off"}, hub=mock_hub)
    assert "Application execution UNBLOCKED on `PC-KIDS`" in res
    assert "Roblox" in res
    assert mock_hub.exec.await_args[1]["kind"] == "ps1"


@pytest.mark.asyncio
async def test_manage_appblock_windows_agent_action_aliases():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:roblox")

    # "block" alias for "on"
    res_block = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "block"}, hub=mock_hub)
    assert "Application execution BLOCKED" in res_block

    # "unblock" alias for "off"
    mock_hub.exec = AsyncMock(return_value="SUCCESS:off:roblox")
    res_unblock = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "unblock"}, hub=mock_hub)
    assert "Application execution UNBLOCKED" in res_unblock


@pytest.mark.asyncio
async def test_manage_appblock_linux_agent_all_on():
    mock_hub = MagicMock()
    mock_hub.agents = {"nas": {"platform": "Linux/6.1.0"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:roblox\nSUCCESS:on:steam")

    res = await manage_appblock({"host": "nas", "app": "all", "action": "on"}, hub=mock_hub)
    assert "Application execution BLOCKED on `nas`" in res
    assert "Linux (`nas` Flatpak/Process)" in res
    mock_hub.exec.assert_awaited_once()
    assert mock_hub.exec.await_args[1]["kind"] == "shell"


@pytest.mark.asyncio
async def test_manage_appblock_status_reporting():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}

    status_output = (
        "STATUS:roblox:BLOCKED\n"
        "STATUS:steam:ALLOWED\n"
        "STATUS:epic:ALLOWED\n"
        "STATUS:discord:BLOCKED\n"
        "STATUS:minecraft:ALLOWED\n"
    )
    mock_hub.exec = AsyncMock(return_value=status_output)

    res = await manage_appblock({"host": "PC-KIDS", "app": "all", "action": "status"}, hub=mock_hub)
    assert "Application Execution Status on `PC-KIDS`" in res
    assert "• **Roblox** (`roblox`): :no_entry: `BLOCKED`" in res
    assert "• **Steam** (`steam`): :white_check_mark: `ALLOWED`" in res
    assert "• **Discord** (`discord`): :no_entry: `BLOCKED`" in res
    assert "• **Minecraft** (`minecraft`): :white_check_mark: `ALLOWED`" in res


@pytest.mark.asyncio
async def test_manage_appblock_local_host():
    with patch("bot.commands.appblock._run_local_bash", new=AsyncMock(return_value="SUCCESS:on:roblox")):
        res = await manage_appblock({"host": "local", "app": "roblox", "action": "on"})
        assert "Application execution BLOCKED on `local`" in res
        assert "Linux (local)" in res


@pytest.mark.asyncio
async def test_manage_appblock_exec_exception():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(side_effect=RuntimeError("timeout waiting for agent"))

    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "on"}, hub=mock_hub)
    assert "Failed to execute appblock command on `PC-KIDS`" in res
    assert "timeout waiting for agent" in res


@pytest.mark.asyncio
async def test_run_local_bash_errors():
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        err = await _run_local_bash("echo test")
        assert "bash not found" in err

    with patch("asyncio.create_subprocess_exec", side_effect=OSError("pipe error")):
        err_os = await _run_local_bash("echo test")
        assert "pipe error" in err_os

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        err_rc = await _run_local_bash("echo test")
        assert "permission denied" in err_rc


@pytest.mark.asyncio
async def test_manage_appblock_custom_config():
    custom_cfg = {
        "apps": {
            "customgame": {
                "name": "Custom Game",
                "windows": {"executables": ["customgame.exe"]},
                "linux": {"flatpaks": ["com.custom.Game"], "process_names": ["customgame"]},
            }
        }
    }
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:customgame")

    res = await manage_appblock(
        {"host": "PC-KIDS", "app": "customgame", "action": "on"},
        hub=mock_hub,
        config=custom_cfg,
    )
    assert "Application execution BLOCKED on `PC-KIDS`" in res
    assert "Custom Game" in res


@pytest.mark.asyncio
async def test_handle_command_blockapp_integration():
    commands = load_commands("config/commands.yaml")
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:roblox")

    posted = []

    async def post(text: str) -> None:
        posted.append(text)

    # Test /host {host} blockapp {app} {action}
    await handle_command(
        "/host PC-KIDS blockapp roblox on",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )

    assert len(posted) == 2
    assert "Running `/host PC-KIDS blockapp roblox on` on `PC-KIDS`" in posted[0]
    assert "Application execution BLOCKED on `PC-KIDS`" in posted[1]

    # Test alias /host {host} appblock {app} {action}
    posted.clear()
    await handle_command(
        "/host PC-KIDS appblock roblox on",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )

    assert len(posted) == 2
    assert "Running `/host PC-KIDS appblock roblox on` on `PC-KIDS`" in posted[0]
    assert "Application execution BLOCKED on `PC-KIDS`" in posted[1]

    # Test direct shortcut /blockapp {host} {app} {action}
    posted.clear()
    await handle_command(
        "/blockapp PC-KIDS roblox on",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )
    assert len(posted) == 2
    assert "Running `/blockapp PC-KIDS roblox on` on `PC-KIDS`" in posted[0]
    assert "Application execution BLOCKED on `PC-KIDS`" in posted[1]

    # Test /host {host} blockapp all (omitted action defaults to status)
    mock_hub.exec = AsyncMock(
        return_value="STATUS:roblox:BLOCKED\nSTATUS:tlauncher:BLOCKED\nSTATUS:steam:ALLOWED"
    )
    posted.clear()
    await handle_command(
        "/host PC-KIDS blockapp all",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )
    assert len(posted) == 2
    assert "Running `/host PC-KIDS blockapp all` on `PC-KIDS`" in posted[0]
    assert "Application Execution Status on `PC-KIDS`" in posted[1]
    assert "• **TLauncher** (`tlauncher`): :no_entry: `BLOCKED`" in posted[1]

    # Test /blockapp {host} all (shortcut omitted action defaults to status)
    posted.clear()
    await handle_command(
        "/blockapp PC-KIDS all",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )
    assert len(posted) == 2
    assert "Running `/blockapp PC-KIDS all` on `PC-KIDS`" in posted[0]
    assert "Application Execution Status on `PC-KIDS`" in posted[1]

    # Test /host {host} blockapp (bare blockapp defaults to all + status)
    posted.clear()
    await handle_command(
        "/host PC-KIDS blockapp",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )
    assert len(posted) == 2
    assert "Running `/host PC-KIDS blockapp` on `PC-KIDS`" in posted[0]
    assert "Application Execution Status on `PC-KIDS`" in posted[1]

    # Test /host {host} blockapp tlauncher on
    mock_hub.exec = AsyncMock(return_value="SUCCESS:on:tlauncher")
    posted.clear()
    await handle_command(
        "/host PC-KIDS blockapp tlauncher on",
        user_id="U1",
        channel_id="C1",
        post=post,
        commands=commands,
        hub=mock_hub,
    )
    assert len(posted) == 2
    assert "Running `/host PC-KIDS blockapp tlauncher on` on `PC-KIDS`" in posted[0]
    assert "Application execution BLOCKED on `PC-KIDS`" in posted[1]
    assert "TLauncher" in posted[1]


@pytest.mark.asyncio
async def test_manage_appblock_default_action():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="STATUS:tlauncher:ALLOWED")

    # app="all", action omitted -> status
    res = await manage_appblock({"host": "PC-KIDS", "app": "all"}, hub=mock_hub)
    assert "Application Execution Status on `PC-KIDS`" in res
    assert "TLauncher" in res

    # app="tlauncher", action omitted -> status
    res = await manage_appblock({"host": "PC-KIDS", "app": "tlauncher"}, hub=mock_hub)
    assert "Application Execution Status on `PC-KIDS`" in res
    assert "• **TLauncher** (`tlauncher`): :white_check_mark: `ALLOWED`" in res

    # bare host, both app and action omitted -> all status
    res = await manage_appblock({"host": "PC-KIDS"}, hub=mock_hub)
    assert "Application Execution Status on `PC-KIDS`" in res



@pytest.mark.asyncio
async def test_manage_appblock_error_output():
    mock_hub = MagicMock()
    mock_hub.agents = {"PC-KIDS": {"platform": "Windows/11"}}
    mock_hub.exec = AsyncMock(return_value="ERROR: access denied writing IFEO key")

    res = await manage_appblock({"host": "PC-KIDS", "app": "roblox", "action": "on"}, hub=mock_hub)
    assert ":x: Failed to execute appblock command on `PC-KIDS`" in res
    assert "access denied" in res
