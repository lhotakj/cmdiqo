"""Unit tests for logon hours restriction command handler."""

import pytest

from bot.commands.restrict import (
    build_restrict_script_linux,
    build_restrict_script_windows,
    format_condensed_logon_hours,
    manage_restrict,
    normalize_schedule,
    normalize_windows_schedule,
    validate_schedule_syntax,
)


def test_validate_schedule_syntax():
    assert validate_schedule_syntax("M-F,08:00-20:00;Sa-Su,09:00-21:00")
    assert validate_schedule_syntax("M-F,16:00-20:00")
    assert validate_schedule_syntax("Sa-Su,09:00-18:00")
    assert validate_schedule_syntax("all")
    assert validate_schedule_syntax("M-F,08:00-10:00;M-F,11:00-20:00")
    assert validate_schedule_syntax(" M-F,08:00-10:00, M-F,11:00-20:00 ")
    assert validate_schedule_syntax("M-F,08:00-10:00,11:00-20:00")
    assert normalize_schedule(" M-F,08:00-10:00, M-F,11:00-20:00 ") == "M-F,08:00-10:00;M-F,11:00-20:00"
    assert normalize_schedule("M-F,08:00-20:00; Sa-Su,09:00-21:00") == "M-F,08:00-20:00;Sa-Su,09:00-21:00"

    # Windows 1-hour resolution normalization
    win_sched, notes = normalize_windows_schedule("Sa-Su,08:00-20:00;M-F,06:45-19:00")
    assert win_sched == "Sa-Su,08:00-20:00;M-F,06:00-19:00"
    assert len(notes) == 1
    assert "06:45" in notes[0] and "06:00" in notes[0]

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
    assert "('/time:' + $scheduleStr)" in win_script_set
    assert '"' not in win_script_set

    win_script_off = build_restrict_script_windows("off", "jonas")
    assert "$action = 'off'.ToLower()" in win_script_off
    assert "'/time:all'" in win_script_off
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
    assert "sed -i '/# cmdiqo-restrict:'$TARGET_USER':start/,/# cmdiqo-restrict:'$TARGET_USER':end/d'" in lin_script_off


@pytest.mark.asyncio
async def test_manage_restrict_validation():
    # Missing host
    res_no_host = await manage_restrict({})
    assert "Target host is required" in res_no_host

    # Missing user
    res_no_user = await manage_restrict(
        {"host": "PC-KIDS", "action": "set", "schedule": "M-F,07:00-18:00"}
    )
    assert "Username is required" in res_no_user

    # Invalid action
    res_inv_act = await manage_restrict(
        {"host": "PC-KIDS", "user": "jonas", "action": "invalid"}
    )
    assert "Invalid action" in res_inv_act

    # Set without schedule
    res_no_sched = await manage_restrict(
        {"host": "PC-KIDS", "user": "jonas", "action": "set"}
    )
    assert "Schedule string is required" in res_no_sched

    # Set with malicious schedule
    res_bad_sched = await manage_restrict(
        {"host": "PC-KIDS", "user": "jonas", "action": "set", "schedule": "M-F;calc.exe"}
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

    # Auto-deduce action from _spec_name when executed via command registry
    res_deduced_status = await manage_restrict(
        {"host": "PC-WIN", "user": "jonas", "_spec_name": "host-restrict-status"}, hub=mock_hub
    )
    assert "Logon Hours Status on `PC-WIN`" in res_deduced_status

    res_deduced_off = await manage_restrict(
        {"host": "PC-WIN", "user": "jonas", "_spec_name": "host-restrict-off"}, hub=mock_hub
    )
    assert "Logon hours restriction removed" in res_deduced_off

    res_deduced_set = await manage_restrict(
        {"host": "PC-WIN", "user": "jonas", "schedule": "M-F,08:00-20:00", "_spec_name": "host-restrict-set"},
        hub=mock_hub,
    )
    assert "Logon hours restriction successfully applied" in res_deduced_set

    # Auto-deduce action from config
    res_cfg_status = await manage_restrict(
        {"host": "PC-WIN", "user": "jonas"}, hub=mock_hub, config={"action": "status"}
    )
    assert "Logon Hours Status on `PC-WIN`" in res_cfg_status

    # Missing user prompts for username
    res_no_user = await manage_restrict(
        {"host": "PC-WIN", "_spec_name": "host-restrict-status"}, hub=mock_hub
    )
    assert "Username is required" in res_no_user

    # Normalize user keyword when user="off" or user="status"
    res_norm_user = await manage_restrict(
        {"host": "PC-WIN", "user": "off"}, hub=mock_hub
    )
    assert "Username is required" in res_norm_user


@pytest.mark.asyncio
async def test_handle_command_restrict_slack_integration():
    from unittest.mock import AsyncMock

    from bot.app import handle_command, load_commands

    class HubFake:
        def __init__(self):
            self.agents = {
                "PC-WIN": {"platform": "Windows 11"},
            }

        async def exec(self, host, kind, cmd, timeout=30):
            if "$action = 'status'" in cmd:
                return "STATUS:jonas:Monday 08:00 - 20:00, Tuesday 08:00 - 20:00"
            if "$action = 'set'" in cmd:
                return "SUCCESS:set:jonas:M-F,08:00-20:00"
            if "$action = 'off'" in cmd:
                return "SUCCESS:off:jonas"
            return ""

    fake_hub = HubFake()
    commands = load_commands("config/commands.yaml")
    post_mock = AsyncMock()

    # 1. Test /host PC-WIN restrict jonas status
    await handle_command(
        text="/host PC-WIN restrict jonas status",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    assert post_mock.await_count >= 2
    reply_status = post_mock.await_args_list[-1][0][0]
    assert "Logon Hours Status on `PC-WIN`" in reply_status

    # 2. Test /host PC-WIN restrict jonas off
    post_mock.reset_mock()
    await handle_command(
        text="/host PC-WIN restrict jonas off",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_off = post_mock.await_args_list[-1][0][0]
    assert "Logon hours restriction removed" in reply_off

    # 3. Test /host PC-WIN restrict jonas set M-F,08:00-20:00
    post_mock.reset_mock()
    await handle_command(
        text="/host PC-WIN restrict jonas set M-F,08:00-20:00",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_set = post_mock.await_args_list[-1][0][0]
    assert "Logon hours restriction successfully applied" in reply_set

    # 4. Test shorthand /host PC-WIN restrict jonas
    post_mock.reset_mock()
    await handle_command(
        text="/host PC-WIN restrict jonas",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_shorthand = post_mock.await_args_list[-1][0][0]
    assert "Logon Hours Status on `PC-WIN`" in reply_shorthand

    # 5. Test direct shortcut /restrict PC-WIN jonas set M-F,08:00-20:00
    post_mock.reset_mock()
    await handle_command(
        text="/restrict PC-WIN jonas set M-F,08:00-20:00",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_direct_set = post_mock.await_args_list[-1][0][0]
    assert "Logon hours restriction successfully applied" in reply_direct_set

    # 6. Test direct shortcut /restrict PC-WIN jonas off
    post_mock.reset_mock()
    await handle_command(
        text="/restrict PC-WIN jonas off",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_direct_off = post_mock.await_args_list[-1][0][0]
    assert "Logon hours restriction removed" in reply_direct_off

    # 7. Test direct shorthand /restrict PC-WIN jonas
    post_mock.reset_mock()
    await handle_command(
        text="/restrict PC-WIN jonas",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_direct_status = post_mock.await_args_list[-1][0][0]
    assert "Logon Hours Status on `PC-WIN`" in reply_direct_status

    # 8. Test split schedule with comma and spaces
    post_mock.reset_mock()
    await handle_command(
        text="/host PC-WIN restrict jonas set  M-F,08:00-10:00, M-F,11:00-20:00 ",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_split = post_mock.await_args_list[-1][0][0]
    assert "Logon hours restriction successfully applied" in reply_split
    assert "M-F,08:00-10:00;M-F,11:00-20:00" in reply_split

    # 9. Test legacy invocation without username prompts with usage guide
    post_mock.reset_mock()
    await handle_command(
        text="/host PC-WIN restrict set M-F,08:00-20:00",
        user_id="U1",
        channel_id="C1",
        post=post_mock,
        commands=commands,
        hub=fake_hub,
    )
    reply_legacy = post_mock.await_args_list[-1][0][0]
    assert "Username is required for logon hours restrictions" in reply_legacy


def test_format_condensed_logon_hours():
    # 1. Compound schedule (M-F + Sa-Su)
    compound = (
        "Sunday 08:00:00 - 18:00:00; Monday 07:00:00 - 18:00:00; Tuesday 07:00:00 - 18:00:00; "
        "Wednesday 07:00:00 - 18:00:00; Thursday 07:00:00 - 18:00:00; Friday 07:00:00 - 18:00:00; "
        "Saturday 08:00:00 - 18:00:00"
    )
    assert format_condensed_logon_hours(compound) == "M-F: 07:00-18:00; Sa-Su: 08:00-18:00"

    # 2. Single day
    single = "Sunday 08:00:00 - 18:00:00"
    assert format_condensed_logon_hours(single) == "Su: 08:00-18:00"

    # 3. Weekend only
    weekend = "Saturday 08:00:00 - 18:00:00; Sunday 08:00:00 - 18:00:00"
    assert format_condensed_logon_hours(weekend) == "Sa-Su: 08:00-18:00"

    # 4. Weekdays only
    weekdays = (
        "Monday 07:00:00 - 18:00:00; Tuesday 07:00:00 - 18:00:00; Wednesday 07:00:00 - 18:00:00; "
        "Thursday 07:00:00 - 18:00:00; Friday 07:00:00 - 18:00:00"
    )
    assert format_condensed_logon_hours(weekdays) == "M-F: 07:00-18:00"

    # 5. All / Unrestricted
    assert format_condensed_logon_hours("All") == "All"
    assert format_condensed_logon_hours("all") == "All"
    assert format_condensed_logon_hours("") == ""

    # 6. Fallback for unparseable or custom string
    fallback = "Custom PAM Rule"
    assert format_condensed_logon_hours(fallback) == "Custom PAM Rule"


@pytest.mark.asyncio
async def test_manage_restrict_multiline_windows_status():
    class MockWinHub:
        def __init__(self):
            self.agents = {"JONAS-PC": {"platform": "Windows 11"}}

        async def exec(self, host, kind, cmd, timeout=30):
            # Simulates multi-line joined hours from PowerShell
            raw_hours = (
                "Sunday 08:00:00 - 18:00:00; Monday 07:00:00 - 18:00:00; Tuesday 07:00:00 - 18:00:00; "
                "Wednesday 07:00:00 - 18:00:00; Thursday 07:00:00 - 18:00:00; Friday 07:00:00 - 18:00:00; "
                "Saturday 08:00:00 - 18:00:00"
            )
            return f"STATUS:jonas:{raw_hours}"

    hub = MockWinHub()
    res = await manage_restrict({"host": "JONAS-PC", "user": "jonas", "action": "status"}, hub=hub)
    assert "Logon Hours Status on `JONAS-PC`" in res
    assert "• User *jonas*: 🔒 *Restricted* → `M-F: 07:00-18:00; Sa-Su: 08:00-18:00`" in res


def test_parse_schedule_boundaries():
    from bot.commands.restrict import parse_schedule_boundaries

    boundaries = parse_schedule_boundaries("M-F,07:00-18:00;Sa-Su,08:00-18:00")
    assert len(boundaries) == 2
    assert boundaries[0]["days"] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert boundaries[0]["end_time"] == "18:00"
    assert boundaries[0]["warn5_time"] == "17:55"
    assert boundaries[0]["warn1_time"] == "17:59"

    assert boundaries[1]["days"] == ["Saturday", "Sunday"]
    assert boundaries[1]["end_time"] == "18:00"
    assert boundaries[1]["warn5_time"] == "17:55"
    assert boundaries[1]["warn1_time"] == "17:59"


def test_second_level_help_matching():
    from bot.app import help_text, load_commands

    commands = load_commands("config/commands.yaml")

    # 1. /help host restriction
    h_restrict = help_text(commands, topic="host restriction")
    assert "Cmdiqo Help: `host restriction`" in h_restrict
    assert "/host {host} restrict {user} set {schedule}" in h_restrict
    assert "/host {host} restrict {user} status" in h_restrict
    assert "-legacy-" not in h_restrict

    # 2. /help host restrict
    h_host_restrict = help_text(commands, topic="host restrict")
    assert "/host {host} restrict {user} set {schedule}" in h_host_restrict

    # 3. /help parental
    h_parental = help_text(commands, topic="parental")
    assert "/host {host} restrict {user}" in h_parental
    assert "/host {host} parental" in h_parental

