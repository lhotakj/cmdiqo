"""Tests for grouped parental domain blocking."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.app import load_commands
from bot.commands.parental import (
    PARENTAL_DOMAINS,
    PARENTAL_GROUPS,
    build_parental_script_linux,
    build_parental_script_windows,
    manage_parental,
)


def test_parental_groups_are_complete_and_ordered() -> None:
    assert list(PARENTAL_GROUPS)[:4] == ["youtube", "roblox", "minecraft", "discord"]
    assert "youtube.com" in PARENTAL_GROUPS["youtube"]
    assert "kids.youtube.com" in PARENTAL_GROUPS["youtube"]
    assert "music.youtube.com" in PARENTAL_GROUPS["youtube"]
    assert "googlevideo.com" in PARENTAL_GROUPS["youtube"]
    assert "minecraft.net" in PARENTAL_GROUPS["minecraft"]
    assert "education.minecraft.net" in PARENTAL_GROUPS["minecraft"]
    assert "webgames" in PARENTAL_GROUPS
    assert "bloxd.io" in PARENTAL_GROUPS["webgames"]
    assert "1v1.lol" in PARENTAL_GROUPS["webgames"]
    assert "orteil.dashnet.org" in PARENTAL_GROUPS["webgames"]
    assert "neal.fun" in PARENTAL_GROUPS["webgames"]
    assert "tetr.io" in PARENTAL_GROUPS["webgames"]
    assert "eaglercraft.com" in PARENTAL_GROUPS["webgames"]
    assert set(PARENTAL_DOMAINS) == {domain for domains in PARENTAL_GROUPS.values() for domain in domains}


def test_group_scripts_replace_only_selected_marker() -> None:
    windows = build_parental_script_windows("on", {"youtube": PARENTAL_GROUPS["youtube"]}, "youtube")
    linux = build_parental_script_linux("off", {"minecraft": PARENTAL_GROUPS["minecraft"]}, "minecraft")
    assert "$lines = @('# parental:' + $current + ':start')" in windows
    assert 'echo "# parental:$current:start"' in linux
    assert "Cannot migrate a partially enabled legacy parental block" in windows
    assert "requires an all-groups migration before selective changes" in windows
    assert "requires an all-groups migration before selective changes" in linux
    assert '"' not in windows


def test_webgames_legacy_migration_in_generated_scripts() -> None:
    windows = build_parental_script_windows("on", {"webgames": PARENTAL_GROUPS["webgames"]}, "webgames")
    linux = build_parental_script_linux("on", {"webgames": PARENTAL_GROUPS["webgames"]}, "webgames")
    assert "# parental:web-games:start" in windows
    assert "# parental:web-games:start" in linux
    assert '"' not in windows


def test_group_and_legacy_command_patterns_are_loaded() -> None:
    specs = {command["spec"]["name"]: command["spec"] for command in load_commands("config/commands.yaml")}
    assert specs["host-parental-group"]["trigger"] == "/host {host} parental {group} {action}"
    assert specs["host-parental"]["trigger"] == "/host {host} parental {action}"
    assert "groups" in specs["host-parental-group"]["config"]
    yaml_groups = specs["host-parental-group"]["config"]["groups"]
    assert set(yaml_groups.keys()) == set(PARENTAL_GROUPS.keys())
    for grp, domains in PARENTAL_GROUPS.items():
        assert set(yaml_groups[grp]) == set(domains)


@pytest.mark.asyncio
async def test_manage_single_group_dispatches_only_that_group() -> None:
    hub = MagicMock()
    hub.agents = {"kids": {"platform": "Linux"}}
    hub.exec = AsyncMock(return_value="SUCCESS:on:youtube")
    result = await manage_parental({"host": "kids", "group": "youtube", "action": "on"}, hub=hub)
    assert "for `youtube`" in result
    script = hub.exec.await_args.kwargs["cmd"]
    assert "SELECTED='youtube'" in script
    assert 'for current in $SELECTED; do replace_group "$current" "$ACTION"; done' in script
    assert "roblox.com" not in script


@pytest.mark.asyncio
async def test_manage_parental_rejects_unknown_group() -> None:
    result = await manage_parental({"host": "kids", "group": "invalid", "action": "on"})
    assert "Unknown parental group" in result


@pytest.mark.asyncio
async def test_manage_parental_reports_one_disconnect_failure() -> None:
    hub = MagicMock()
    hub.agents = {"kids": {"platform": "Windows"}}
    hub.exec = AsyncMock(side_effect=ConnectionResetError("agent disconnected before command completed"))

    result = await manage_parental({"host": "kids", "group": "youtube", "action": "on"}, hub=hub)

    assert result.count("Failed to execute parental command") == 1
    hub.exec.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("group_alias", ["webgames", "web-games", "bloxdio", "games"])
async def test_manage_parental_webgames_aliases_dispatch_webgames(group_alias: str) -> None:
    hub = MagicMock()
    hub.agents = {"kids": {"platform": "Linux"}}
    hub.exec = AsyncMock(return_value="SUCCESS:on:webgames")

    result = await manage_parental({"host": "kids", "group": group_alias, "action": "on"}, hub=hub)

    assert "for `webgames`" in result
    script = hub.exec.await_args.kwargs["cmd"]
    assert "SELECTED='webgames'" in script
    assert "bloxd.io" in script


def test_windows_script_handles_readonly_and_error_output() -> None:
    windows = build_parental_script_windows("on", {"youtube": PARENTAL_GROUPS["youtube"]}, "youtube")
    assert "$fileItem.IsReadOnly = $false" in windows
    assert "Write-Output ('ERROR:' + $err)" in windows
    assert "& ipconfig /flushdns | Out-Null" in windows
    assert "Clear-DnsClientCache" not in windows
    assert '"' not in windows


@pytest.mark.asyncio
async def test_manage_parental_reports_clean_error_message() -> None:
    hub = MagicMock()
    hub.agents = {"JONAS-PC": {"platform": "Windows"}}
    hub.exec = AsyncMock(
        return_value="ERROR:Access to C:\\Windows\\System32\\drivers\\etc\\hosts is denied. Ensure cmdiqo-agent is running with Administrator privileges."
    )

    result = await manage_parental({"host": "JONAS-PC", "group": "all", "action": "on"}, hub=hub)

    assert "Parental control failed on `JONAS-PC` for all groups" in result
    assert "Access to C:\\Windows\\System32\\drivers\\etc\\hosts is denied." in result


@pytest.mark.asyncio
async def test_manage_parental_serializes_concurrent_calls_per_host() -> None:
    hub = MagicMock()
    hub.agents = {"JONAS-PC": {"platform": "Windows"}}

    active_executions = 0
    max_concurrent = 0

    async def mock_exec(*args: Any, **kwargs: Any) -> str:
        nonlocal active_executions, max_concurrent
        active_executions += 1
        max_concurrent = max(max_concurrent, active_executions)
        await asyncio.sleep(0.05)
        active_executions -= 1
        return "SUCCESS:on:youtube"

    hub.exec = AsyncMock(side_effect=mock_exec)

    res1, res2 = await asyncio.gather(
        manage_parental({"host": "JONAS-PC", "group": "youtube", "action": "on"}, hub=hub),
        manage_parental({"host": "JONAS-PC", "group": "youtube", "action": "on"}, hub=hub),
    )

    assert "ENABLED on `JONAS-PC` for `youtube`" in res1
    assert "ENABLED on `JONAS-PC` for `youtube`" in res2
    assert max_concurrent == 1, "Expected host lock to serialize concurrent executions"
