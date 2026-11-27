"""Unit tests for bot.commands.update remote agent update handlers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.commands.update import (
    build_linux_update_script,
    build_windows_update_script,
    format_update_output,
    is_newer_version,
    manage_update,
    normalize_target_arch,
    parse_semver_tuple,
    strip_clixml,
)
from bot.hub import Hub


def test_normalize_target_arch():
    assert normalize_target_arch("Windows/10.0", "AMD64") == "win64"
    assert normalize_target_arch("Windows/11", "win64") == "win64"
    assert normalize_target_arch("Linux/6.8", "x86_64") == "linux64"
    assert normalize_target_arch("Linux/6.8", "linux64") == "linux64"
    assert normalize_target_arch("Linux/6.8", "aarch64") == "arm64"
    assert normalize_target_arch("Linux/6.18", "armv6") == "armv6"


def test_semver_comparison():
    assert parse_semver_tuple("v0.37.12") == (0, 37, 12)
    assert parse_semver_tuple("0.37.devel+ABC") == (0, 37, -1)
    assert is_newer_version("0.36.5", "0.37.0") is True
    assert is_newer_version("0.37.devel+ABC", "0.37.1") is True
    assert is_newer_version("0.37.5", "0.37.5") is False
    assert is_newer_version("0.38.0", "0.37.9") is False


def test_generated_scripts_avoid_unescaped_quotes_and_preserve_tamper():
    ps_protected = build_windows_update_script(
        "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.0/cmdiqo-agent-windows-x64-setup.exe",
        tamper_protect=True,
    )
    assert '"' not in ps_protected
    assert "--cli --upgrade" in ps_protected
    assert "--no-tamper-protect" not in ps_protected
    assert "NT AUTHORITY\\SYSTEM" in ps_protected
    assert "$ProgressPreference = 'SilentlyContinue'" in ps_protected

    ps_optout = build_windows_update_script(
        "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.0/cmdiqo-agent-windows-x64-setup.exe",
        tamper_protect=False,
    )
    assert '"' not in ps_optout
    assert "--cli --upgrade --no-tamper-protect" in ps_optout
    assert "$ProgressPreference = 'SilentlyContinue'" in ps_optout

    sh_script = build_linux_update_script(
        "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.0/cmdiqo-agent-linux-arm64"
    )
    assert "cmdiqo-agent-linux-arm64" in sh_script
    assert "systemd-run" in sh_script
    assert "CHECKSUMS" not in sh_script


@pytest.mark.asyncio
async def test_manage_update_status_and_native_dispatch(monkeypatch):
    fake_release = {
        "tag": "v0.37.5",
        "version": "0.37.5",
        "assets": {
            "cmdiqo-agent-windows-x64-setup.exe": "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.5/cmdiqo-agent-windows-x64-setup.exe",
            "cmdiqo-agent": "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.5/cmdiqo-agent",
            "cmdiqo-agent-linux-arm64": "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.5/cmdiqo-agent-linux-arm64",
        },
    }
    monkeypatch.setattr("bot.commands.update.fetch_release_info", lambda _v="latest": fake_release)

    hub = Hub()
    hub.agents["AMD"] = {
        "version": "0.37.0",
        "platform": "Windows/10.0",
        "arch": "win64",
        "tamper_protect": True,
        "auto_update": False,
        "update_capable": True,
    }
    hub.agents["legacy-pi"] = {
        "version": "0.36.2",
        "platform": "Linux/6.8",
        "arch": "arm64",
        "tamper_protect": False,
        "auto_update": False,
        "update_capable": False,
    }
    hub.update_agent = AsyncMock(return_value="UPDATE_SCHEDULED: Windows upgrade queued.")
    hub.exec = AsyncMock(return_value="UPDATE_SCHEDULED: Linux agent binary replaced; service restarting.")

    status_out = await manage_update({"host": "AMD", "action": "status"}, hub=hub)
    assert "Update available" in status_out
    assert "Tamper-Protect: `enabled`" in status_out

    upd_native = await manage_update({"host": "AMD", "action": "latest"}, hub=hub)
    hub.update_agent.assert_awaited_once()
    assert "via native updater" in upd_native
    assert "Update scheduled" in upd_native

    upd_legacy = await manage_update({"host": "legacy-pi", "action": "latest"}, hub=hub)
    hub.exec.assert_awaited_once()
    assert hub.exec.await_args.args[1] == "shell"
    assert "cmdiqo-agent-linux-arm64" in hub.exec.await_args.args[2]
    assert "Update scheduled" in upd_legacy


@pytest.mark.asyncio
async def test_manage_update_fleet_and_up_to_date(monkeypatch):
    fake_release = {
        "tag": "v0.37.5",
        "version": "0.37.5",
        "assets": {
            "cmdiqo-agent": "https://github.com/lhotakj/cmdiqo/releases/download/v0.37.5/cmdiqo-agent",
        },
    }
    monkeypatch.setattr("bot.commands.update.fetch_release_info", lambda _v="latest": fake_release)

    hub = Hub()
    hub.agents["up2date"] = {
        "version": "0.37.5",
        "platform": "Linux/6.8",
        "arch": "linux64",
        "tamper_protect": False,
        "update_capable": True,
    }
    hub.update_agent = AsyncMock()
    out = await manage_update({"host": "", "action": "latest"}, hub=hub)
    assert "already up to date" in out
    hub.update_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_hub_update_agent_sends_update_frame():
    hub = Hub()
    ws = MagicMock()
    sent_payloads = []

    async def fake_send(raw):
        payload = json.loads(raw)
        sent_payloads.append(payload)
        req_id = payload["id"]
        fut = hub.pending[req_id]
        fut.set_result({"id": req_id, "type": "result", "ok": True, "code": 0, "output": "UPDATE_APPLIED"})

    ws.send = AsyncMock(side_effect=fake_send)
    hub.agents["home"] = {"ws": ws, "version": "0.37.0", "platform": "Linux/6.8", "arch": "linux64"}

    res = await hub.update_agent("home", version="0.37.5", url="https://github.com/lhotakj/cmdiqo/releases/download/v0.37.5/cmdiqo-agent")
    assert res == "UPDATE_APPLIED"
    assert sent_payloads[0]["type"] == "update"
    assert sent_payloads[0]["version"] == "0.37.5"


def test_strip_clixml_and_format_update_output():
    # Raw output containing PowerShell progress stream serialized as CLIXML
    raw_with_clixml = (
        "#< CLIXML\r\n"
        "<Objs Version=\"1.1.0.1\" xmlns=\"http://schemas.microsoft.com/powershell/2004/04\">"
        "<Obj S=\"progress\" RefId=\"0\"><TN RefId=\"0\"><T>System.Management.Automation.PSCustomObject</T>"
        "<T>System.Object</T></TN><MS><I64 N=\"SourceId\">1</I64><PR N=\"Record\"><AV>Preparing modules</AV>"
        "<AI>0</AI><Nil /><PI>-1</PI><PC>-1</PC><T>Completed</T><SR>-1</SR><SD> </SD></PR></MS></Obj>"
        "<Obj S=\"progress\" RefId=\"1\"><TNRef RefId=\"0\" /><MS><I64 N=\"SourceId\">2</I64><PR N=\"Record\">"
        "<AV>Writing web request...</AV><AI>174593042</AI><Nil /><PI>-1</PI><PC>-1</PC><T>Processing</T>"
        "<SR>-1</SR><SD> (Number of bytes written: 81920)</SD></PR></MS></Obj>"
        "</Objs>\r\n"
        "UPDATE_SCHEDULED:Windows agent upgrade queued; service restarting shortly."
    )

    cleaned = strip_clixml(raw_with_clixml)
    assert "#< CLIXML" not in cleaned
    assert "<Objs" not in cleaned
    assert "UPDATE_SCHEDULED:Windows agent upgrade queued; service restarting shortly." == cleaned

    # Formatted output should be clean, concise, and informative
    formatted = format_update_output("JONAS-PC", "0.31.40", "v0.37.48", raw_with_clixml, via_native=False)
    assert "#< CLIXML" not in formatted
    assert "<Objs" not in formatted
    assert ":package: *JONAS-PC* (`v0.31.40` → `v0.37.48`):" in formatted
    assert ":white_check_mark: Update scheduled: Windows agent upgrade queued; service restarting shortly." in formatted

    # Test error output formatting
    err_formatted = format_update_output("JONAS-PC", "0.31.40", "v0.37.48", "ERROR:Download failed: HTTP 404", via_native=False)
    assert ":x: Update failed: Download failed: HTTP 404" in err_formatted

    # Test already up to date formatting
    up2date_formatted = format_update_output("JONAS-PC", "0.37.48", "v0.37.48", "ALREADY_UP_TO_DATE: Running v0.37.48", via_native=True)
    assert "already up to date" in up2date_formatted
