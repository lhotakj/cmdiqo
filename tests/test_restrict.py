"""Unit tests for logon hours restriction command handler."""

import pytest

from bot.commands.restrict import (
    build_restrict_script_linux,
    build_restrict_script_windows,
    manage_restrict,
    validate_schedule_syntax,
)


def test_validate_schedule_syntax():
    assert validate_schedule_syntax("M-F,08:00-20:00;Sa-Su,09:00-21:00")
    assert validate_schedule_syntax("M-F,16:00-20:00")
    assert validate_schedule_syntax("Sa-Su,09:00-18:00")
    assert validate_schedule_syntax("all")
    assert not validate_schedule_syntax("")
    assert not validate_schedule_syntax("M-F;calc.exe")
    assert not validate_schedule_syntax("M-F && rm -rf /")
    assert not validate_schedule_syntax("M-F | whoami")


def test_build_restrict_script_windows():
    win_script_status = build_restrict_script_windows("status", "jonas")
    assert "$action = 'status'.ToLower()" in win_script_status
    assert "$targetUser = 'jonas'" in win_script_status
    assert '"' not in win_script_status

    win_script_set = build_restrict_script_windows("set", "jonas", "M-F,08:00-20:00")
    assert "$action = 'set'.ToLower()" in win_script_set
    assert "$scheduleStr = 'M-F,08:00-20:00'" in win_script_set
    assert "'net user ' + $u + ' /time:' + $scheduleStr" in win_script_set
    assert '"' not in win_script_set

    win_script_off = build_restrict_script_windows("off", "jonas")
    assert "$action = 'off'.ToLower()" in win_script_off
    assert "'net user ' + $u + ' /time:all'" in win_script_off
    assert '"' not in win_script_off


def test_build_restrict_script_linux():
    lin_script_status = build_restrict_script_linux("status", "jonas")
    assert 'ACTION="status"' in lin_script_status
    assert 'TARGET_USER="jonas"' in lin_script_status

    lin_script_set = build_restrict_script_linux("set", "jonas", "M-F,08:00-20:00")
    assert 'ACTION="set"' in lin_script_set
    assert 'SCHEDULE="M-F,08:00-20:00"' in lin_script_set
    assert "*;*;$TARGET_USER;$SCHEDULE" in lin_script_set

    lin_script_off = build_restrict_script_linux("off", "jonas")
    assert 'ACTION="off"' in lin_script_off
    assert "sed -i '/# cmdiqo-restrict:start/,/# cmdiqo-restrict:end/d'" in lin_script_off


@pytest.mark.asyncio
async def test_manage_restrict_validation():
    # Missing host
    res_no_host = await manage_restrict({})
    assert "Target host is required" in res_no_host

    # Invalid action
    res_inv_act = await manage_restrict({"host": "PC-KIDS", "action": "invalid"})
    assert "Invalid action" in res_inv_act

    # Set without schedule
    res_no_sched = await manage_restrict({"host": "PC-KIDS", "action": "set"})
    assert "Schedule string is required" in res_no_sched

    # Set with malicious schedule
    res_bad_sched = await manage_restrict(
        {"host": "PC-KIDS", "action": "set", "schedule": "M-F;calc.exe"}
    )
    assert "Invalid schedule format" in res_bad_sched


@pytest.mark.asyncio
async def test_manage_restrict_mock_hub():
    class MockHub:
        def __init__(self):
            self.agents = {
                "PC-WIN": {"platform": "Windows 11"},
                "PC-LIN": {"platform": "Linux"},
            }

        async def exec(self, host, kind, cmd, timeout=30):
            if host == "PC-WIN":
                assert kind == "ps1"
                assert '"' not in cmd
                if "$action = 'status'" in cmd:
                    return "STATUS:jonas:Monday 08:00 - 20:00, Tuesday 08:00 - 20:00"
                if "$action = 'set'" in cmd:
                    return "SUCCESS:set:jonas:M-F,08:00-20:00"
                if "$action = 'off'" in cmd:
                    return "SUCCESS:off:jonas"
            if host == "PC-LIN":
                assert kind == "shell"
                if 'ACTION="status"' in cmd:
                    return "STATUS:jonas:All"
                if 'ACTION="set"' in cmd:
                    return "SUCCESS:set:jonas:M-F,08:00-20:00"
                if 'ACTION="off"' in cmd:
                    return "SUCCESS:off:jonas"
            return ""

    mock_hub = MockHub()

    # Status check on Windows
    res_win_status = await manage_restrict(
        {"host": "PC-WIN", "action": "status", "user": "jonas"}, hub=mock_hub
    )
    assert "Logon Hours Status on `PC-WIN`" in res_win_status
    assert "Restricted" in res_win_status
    assert "Monday 08:00 - 20:00" in res_win_status

    # Set on Windows
    res_win_set = await manage_restrict(
        {"host": "PC-WIN", "action": "set", "user": "jonas", "schedule": "M-F,08:00-20:00"},
        hub=mock_hub,
    )
    assert "Logon hours restriction successfully applied" in res_win_set
    assert "M-F,08:00-20:00" in res_win_set

    # Off on Windows
    res_win_off = await manage_restrict(
        {"host": "PC-WIN", "action": "off", "user": "jonas"}, hub=mock_hub
    )
    assert "Logon hours restriction removed" in res_win_off
    assert "All" in res_win_off

    # Status on Linux (All)
    res_lin_status = await manage_restrict(
        {"host": "PC-LIN", "action": "status", "user": "jonas"}, hub=mock_hub
    )
    assert "Unrestricted" in res_lin_status
