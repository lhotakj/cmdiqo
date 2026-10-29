"""Tests for grouped parental domain blocking."""

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
    assert "minecraft.net" in PARENTAL_GROUPS["minecraft"]
    assert set(PARENTAL_DOMAINS) == {domain for domains in PARENTAL_GROUPS.values() for domain in domains}


def test_group_scripts_replace_only_selected_marker() -> None:
    windows = build_parental_script_windows("on", {"youtube": PARENTAL_GROUPS["youtube"]}, "youtube")
    linux = build_parental_script_linux("off", {"minecraft": PARENTAL_GROUPS["minecraft"]}, "minecraft")
    assert "# parental:youtube:start" in windows
    assert "# parental:minecraft:start" in linux
    assert "Cannot migrate a partially enabled legacy parental block" in windows
    assert "requires an all-groups migration before selective changes" in windows
    assert "requires an all-groups migration before selective changes" in linux
    assert '"' not in windows


def test_group_and_legacy_command_patterns_are_loaded() -> None:
    specs = {command["spec"]["name"]: command["spec"] for command in load_commands("config/commands.yaml")}
    assert specs["host-parental-group"]["trigger"] == "/host {host} parental {group} {action}"
    assert specs["host-parental"]["trigger"] == "/host {host} parental {action}"


@pytest.mark.asyncio
async def test_manage_single_group_dispatches_only_that_group() -> None:
    hub = MagicMock()
    hub.agents = {"kids": {"platform": "Linux"}}
    hub.exec = AsyncMock(return_value="SUCCESS:on:youtube")
    result = await manage_parental({"host": "kids", "group": "youtube", "action": "on"}, hub=hub)
    assert "for `youtube`" in result
    script = hub.exec.await_args.kwargs["cmd"]
    assert "# parental:youtube:start" in script
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
