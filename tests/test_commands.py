"""Unit tests for command loading, regex parsing, aliases, and parameter validation."""

import pytest

from bot.app import (
    apply_aliases,
    load_aliases,
    load_commands,
    trigger_to_regex,
    validate_params,
)


def test_trigger_to_regex_exact_match():
    pattern = trigger_to_regex("/lan devices list")
    assert pattern.match("/lan devices list")
    assert pattern.match("/lan devices list   ")
    assert pattern.match("/LAN DEVICES LIST")
    assert not pattern.match("/lan devices")
    assert not pattern.match("/lan devices list extra")


def test_trigger_to_regex_with_placeholder():
    pattern = trigger_to_regex("/host {host} docker restart {service}")
    m = pattern.match("/host home docker restart plex")
    assert m is not None
    assert m.group("host") == "home"
    assert m.group("service") == "plex"

    # With hyphens, dots, numbers, underscores
    m2 = pattern.match("/host nas-01.lan docker restart web-api_v2.0")
    assert m2 is not None
    assert m2.group("host") == "nas-01.lan"
    assert m2.group("service") == "web-api_v2.0"

    # Reject invalid characters directly at regex level
    m3 = pattern.match("/host home docker restart plex;rm -rf /")
    assert m3 is None


def test_validate_params_safe():
    safe_params = {
        "service": "plex",
        "pc": "DESKTOP-1234",
        "version": "1.2.3",
        "ip_port": "192.168.1.1:8080",
        "schedule": "M-F,08:00-20:00;Sa-Su,09:00-21:00",
        "message": "Dinner is ready! Please save your game.",
    }
    # Should not raise
    validate_params(safe_params)


def test_validate_params_unsafe():
    unsafe_candidates = [
        {"service": "plex;calc.exe"},
        {"service": "plex&calc.exe"},
        {"service": "plex|calc.exe"},
        {"service": "`whoami`"},
        {"service": "$(whoami)"},
        {"service": "plex\nreboot"},
        {"schedule": "M-F;calc.exe"},
        {"message": "Hello `calc.exe`"},
    ]
    for param in unsafe_candidates:
        with pytest.raises(ValueError, match="contains unsafe"):
            validate_params(param)


def test_load_commands_valid_file():
    commands = load_commands("config/commands.yaml")
    assert len(commands) >= 5
    triggers = [c["spec"]["trigger"] for c in commands]
    assert "/lan devices list" in triggers
    assert "/lan wake {target}" in triggers
    assert "/lan ping {host}" in triggers
    assert "/host {host} docker restart {service}" in triggers
    assert "/host {host} docker ps" in triggers
    assert "/host {host} uptime" in triggers
    assert "/host {host} memory" in triggers
    assert "/host {host} reboot" in triggers
    assert "/host {host} shutdown" in triggers
    assert "/host {host} services" in triggers
    assert "/host {host} lock" in triggers
    assert "/host {host} msg {message}" in triggers
    assert "/host {host} screentime" in triggers
    assert "/host {host} events" in triggers
    assert "/host {host} ping {target}" in triggers
    assert "/host {host} ports" in triggers
    assert "/host {host} speedtest" in triggers
    assert "/host {host} restrict set {schedule}" in triggers
    assert "/host {host} restrict status" in triggers
    assert "/host {host} restrict off" in triggers
    assert "/host {host} restrict {user} set {schedule}" in triggers
    assert "/host {host} restrict {user} status" in triggers
    assert "/host {host} restrict {user} off" in triggers
    # Verify no legacy /home commands remain in commands:
    assert not any(t.startswith("/home") for t in triggers)

    # Verify cross-platform OS-agnostic specs (contain both shell and ps1)
    specs_by_name = {c["spec"]["name"]: c["spec"] for c in commands}
    for name in (
        "host-uptime",
        "host-memory",
        "host-disk",
        "host-top",
        "host-reboot",
        "host-shutdown",
        "host-services",
        "host-service-status",
        "host-service-restart",
        "host-lock",
        "host-msg",
        "host-screentime",
        "host-events",
        "host-ping",
        "host-ports",
        "host-speedtest",
    ):
        assert name in specs_by_name
        cmd = specs_by_name[name]["command"]
        assert "shell" in cmd
        assert "ps1" in cmd

    # Verify no target or category in commands.yaml
    for c in commands:
        spec = c["spec"]
        assert "target" not in spec, f"Found target in command '{spec.get('name')}'"
        assert "category" not in spec, f"Found category in command '{spec.get('name')}'"

    # Verify parental command with general config.domains list
    assert "host-parental" in specs_by_name
    parental_spec = specs_by_name["host-parental"]
    assert "command" in parental_spec
    assert parental_spec["command"]["python"] == "commands.parental:manage_parental"
    assert "config" in parental_spec
    assert isinstance(parental_spec["config"], dict)
    assert "domains" in parental_spec["config"]
    assert isinstance(parental_spec["config"]["domains"], list)
    assert len(parental_spec["config"]["domains"]) >= 50
    assert "youtube.com" in parental_spec["config"]["domains"]

    # Verify unified user commands
    assert "host-users" in specs_by_name
    assert specs_by_name["host-users"]["command"]["python"] == "commands.users:manage_users"
    assert "host-user" in specs_by_name
    assert specs_by_name["host-user"]["trigger"] == "/host {host} user {user} {action}"

    # Verify poweruser / sudo / administrator privilege commands
    for pname in (
        "host-poweruser",
        "host-poweruser-status",
        "host-sudo",
        "host-sudo-status",
        "host-administrator",
        "host-administrator-status",
    ):
        assert pname in specs_by_name
        assert specs_by_name[pname]["command"]["python"] == "commands.users:manage_poweruser"

    # Verify appblock command with apps config
    assert "host-blockapp" in specs_by_name
    blockapp_spec = specs_by_name["host-blockapp"]
    assert blockapp_spec["command"]["python"] == "commands.appblock:manage_appblock"
    assert isinstance(blockapp_spec.get("config"), dict)
    assert "apps" in blockapp_spec["config"]
    assert "roblox" in blockapp_spec["config"]["apps"]
    assert "tlauncher" in blockapp_spec["config"]["apps"]
    assert "host-appblock" in specs_by_name
    assert "host-blockapp-query" in specs_by_name
    assert "host-blockapp-all" in specs_by_name
    assert "direct-blockapp-query" in specs_by_name
    assert "direct-blockapp-all" in specs_by_name

    # Verify remote agent update commands
    assert "host-agent-update-arg" in specs_by_name
    assert "host-agent-update" in specs_by_name
    assert "lan-agents-update-arg" in specs_by_name
    assert "lan-agents-update" in specs_by_name
    assert specs_by_name["host-agent-update"]["command"]["python"] == "commands.update:manage_update"
    assert specs_by_name["lan-agents-update"]["command"]["python"] == "commands.update:manage_update"


def test_load_commands_nonexistent_file():
    commands = load_commands("config/nonexistent_commands.yaml")
    assert commands == []


def test_load_aliases():
    aliases = load_aliases("config/commands.yaml")
    assert len(aliases) >= 2
    home_alias = next((a for a in aliases if a["name"] == "home"), None)
    assert home_alias is not None
    assert home_alias["input"] == "/home"
    assert home_alias["output"] == "/host home"

    wol_alias = next((a for a in aliases if a["name"] == "wol"), None)
    assert wol_alias is not None
    assert wol_alias["input"] == "/wol"
    assert wol_alias["output"] == "/lan wake"


def test_load_aliases_nonexistent_file():
    aliases = load_aliases("config/nonexistent_commands.yaml")
    assert aliases == []


def test_apply_aliases():
    aliases = [
        {"name": "home", "input": "/home", "output": "/host home"},
        {"name": "nas", "input": "/nas", "output": "/host 192.168.1.50"},
        {"name": "wol", "input": "/wol", "output": "/lan wake"},
    ]
    # Exact match
    assert apply_aliases("/home", aliases) == "/host home"
    assert apply_aliases("/HOME", aliases) == "/host home"
    # Subcommand match
    assert apply_aliases("/home docker ps", aliases) == "/host home docker ps"
    assert (
        apply_aliases("/home   docker restart plex", aliases)
        == "/host home docker restart plex"
    )
    assert apply_aliases("/nas disk", aliases) == "/host 192.168.1.50 disk"
    assert (
        apply_aliases("/wol 00:11:22:33:44:55", aliases)
        == "/lan wake 00:11:22:33:44:55"
    )
    # Unmatched commands unchanged
    assert apply_aliases("/lan devices list", aliases) == "/lan devices list"
    assert apply_aliases("/host server1 disk", aliases) == "/host server1 disk"


def test_trigger_to_regex_mac_address_placeholder():
    pattern = trigger_to_regex("/lan wake {target}")
    m = pattern.match("/lan wake 00:11:22:33:44:55")
    assert m is not None
    assert m.group("target") == "00:11:22:33:44:55"

    m_ip = pattern.match("/lan wake 192.168.1.50")
    assert m_ip is not None
    assert m_ip.group("target") == "192.168.1.50"


@pytest.mark.asyncio
async def test_manage_users_handlers():
    from bot.commands.users import (
        build_user_script_linux,
        build_user_script_windows,
        manage_users,
    )

    # Test script generators
    win_script = build_user_script_windows("users")
    assert "Get-LocalUser" in win_script
    assert '"' not in win_script

    win_status = build_user_script_windows("status", "john")
    assert "john" in win_status
    assert '"' not in win_status

    win_enable = build_user_script_windows("enable", "john")
    assert "Refresh-CmdiqoLogonUI -EnsureUserSwitch $true" in win_enable
    assert "UserSwitch" in win_enable
    assert "Stop-Process -Force" in win_enable
    assert '"' not in win_enable

    win_disable = build_user_script_windows("disable", "john")
    assert "Refresh-CmdiqoLogonUI -EnsureUserSwitch $false" in win_disable
    assert '"' not in win_disable

    linux_script = build_user_script_linux("users")
    assert "getent passwd" in linux_script

    linux_status = build_user_script_linux("status", "john")
    assert "john" in linux_status

    # Test invalid inputs
    res_missing_host = await manage_users({})
    assert "Target host is required" in res_missing_host

    res_missing_user = await manage_users({"host": "WIN-PC", "action": "status"})
    assert "Username is required" in res_missing_user

    # Test mock remote execution via Hub
    class MockHub:
        def __init__(self):
            self.agents = {"WIN-PC": {"platform": "Windows"}}

        async def exec(self, host, kind, cmd, timeout=60):
            if "$action = 'users'" in cmd or 'ACTION="users"' in cmd:
                return "USERS_LIST:\n✔️ john (active)\n🚫 guest (locked)"
            if "$action = 'status'" in cmd or 'ACTION="status"' in cmd:
                return "USER_STATUS|john|John Doe|C:\\Users\\john|active|✔️|1h 20m 5s"
            if "$action = 'disable'" in cmd or 'ACTION="disable"' in cmd:
                return "LOGONUI_REFRESHED\nSUCCESS:disabled:john"
            if "$action = 'enable'" in cmd or 'ACTION="enable"' in cmd:
                return "LOGONUI_REFRESHED\nSUCCESS:enabled:john"
            return ""

    mock_hub = MockHub()

    res_users = await manage_users({"host": "WIN-PC", "action": "users"}, hub=mock_hub)
    assert "Users on `WIN-PC`" in res_users
    assert "✔️ john" in res_users

    res_status = await manage_users({"host": "WIN-PC", "user": "john", "action": "status"}, hub=mock_hub)
    assert "John Doe" in res_status
    assert "1h 20m 5s" in res_status

    res_dis = await manage_users({"host": "WIN-PC", "user": "john", "action": "disable"}, hub=mock_hub)
    assert "disabled" in res_dis
    assert "LogonUI.exe" in res_dis

    res_ena = await manage_users({"host": "WIN-PC", "user": "john", "action": "enable"}, hub=mock_hub)
    assert "enabled" in res_ena
    assert "LogonUI.exe" in res_ena


def test_host_reboot_and_restart_triggers():
    commands = load_commands("config/commands.yaml")
    reboot_cmd = next((c for c in commands if c["spec"]["name"] == "host-reboot"), None)
    restart_cmd = next((c for c in commands if c["spec"]["name"] == "host-restart"), None)

    assert reboot_cmd is not None
    assert restart_cmd is not None

    assert reboot_cmd["spec"]["command"] == restart_cmd["spec"]["command"]

    m_reboot = reboot_cmd["regex"].match("/host AMD reboot")
    assert m_reboot is not None
    assert m_reboot.group("host") == "AMD"

    m_restart = restart_cmd["regex"].match("/host AMD restart")
    assert m_restart is not None
    assert m_restart.group("host") == "AMD"


def test_poweruser_trigger_combinations():
    commands = load_commands("config/commands.yaml")
    by_name = {c["spec"]["name"]: c["regex"] for c in commands}

    for keyword, spec_name in (
        ("poweruser", "host-poweruser"),
        ("sudo", "host-sudo"),
        ("administrator", "host-administrator"),
    ):
        for action in ("enable", "disable", "add", "remove", "status"):
            m = by_name[spec_name].match(f"/host nas-01 {keyword} john.doe {action}")
            assert m is not None, f"Failed to match {keyword} {action}"
            assert m.group("host") == "nas-01"
            assert m.group("user") == "john.doe"
            assert m.group("action") == action

        # Reject shell injection in username or action
        assert by_name[spec_name].match(f"/host nas-01 {keyword} john;id enable") is None
        assert by_name[spec_name].match(f"/host nas-01 {keyword} john enable|id") is None

    for keyword, spec_name in (
        ("poweruser", "host-poweruser-status"),
        ("sudo", "host-sudo-status"),
        ("administrator", "host-administrator-status"),
    ):
        m = by_name[spec_name].match(f"/host AMD {keyword} jonas")
        assert m is not None
        assert m.group("host") == "AMD"
        assert m.group("user") == "jonas"


@pytest.mark.asyncio
async def test_manage_poweruser_handlers():
    from bot.commands.users import (
        build_poweruser_script_linux,
        build_poweruser_script_windows,
        manage_poweruser,
    )

    # 1. Verify Windows PowerShell script safety (strict single quotes, SID S-1-5-32-544, ADSI fallback)
    for act in ("status", "enable", "disable"):
        win_script = build_poweruser_script_windows(act, "john")
        assert '"' not in win_script
        assert "S-1-5-32-544" in win_script
        assert "Get-LocalGroup" in win_script
        assert "Add-LocalGroupMember" in win_script
        assert "Remove-LocalGroupMember" in win_script
        assert "WinNT://./" in win_script

    # 2. Verify Linux Bash script surgical sudoers editing & visudo verification
    linux_enable = build_poweruser_script_linux("enable", "john.doe")
    assert 'SUDOERS_FILE="/etc/sudoers"' in linux_enable
    assert "visudo -cf" in linux_enable
    assert "chmod 0440" in linux_enable
    assert "$1 == u && NF >= 2" in linux_enable
    assert "ALL=(ALL:ALL) ALL" in linux_enable

    linux_disable = build_poweruser_script_linux("disable", "john.doe")
    assert 'if [ "$TARGET_USER" = "root" ]; then' in linux_disable
    assert "awk -v u=" in linux_disable

    # 3. Validation error cases
    assert "Target host is required" in await manage_poweruser({})
    assert "Username is required" in await manage_poweruser({"host": "WIN-PC", "action": "enable"})
    assert "Invalid privilege action" in await manage_poweruser(
        {"host": "WIN-PC", "user": "john", "action": "promote"}
    )
    assert "Invalid username" in await manage_poweruser(
        {"host": "WIN-PC", "user": "john;rm", "action": "enable"}
    )

    # 4. Mock remote execution across Windows and Linux, including all action aliases
    class MockHub:
        def __init__(self):
            self.agents = {
                "WIN-PC": {"platform": "Windows/11"},
                "LINUX-SRV": {"platform": "Linux/6.8"},
            }
            self.calls = []

        async def exec(self, host, kind, cmd, timeout=60):
            self.calls.append((host, kind, cmd))
            if host == "WIN-PC":
                assert kind == "ps1"
                if "$action = 'status'" in cmd:
                    return "POWERUSER_STATUS:enabled:jonas:Administrators"
                if "$action = 'enable'" in cmd:
                    return "SUCCESS:enabled:jonas:Administrators"
                if "$action = 'disable'" in cmd:
                    return "SUCCESS:disabled:jonas:Administrators"
            else:
                assert kind == "shell"
                if 'ACTION="status"' in cmd:
                    return "POWERUSER_STATUS:disabled:john:/etc/sudoers\nEXTRA_GROUPS:sudo"
                if 'ACTION="enable"' in cmd:
                    if "already" in cmd:
                        pass
                    return "SUCCESS:enabled:john:/etc/sudoers"
                if 'ACTION="disable"' in cmd:
                    return "SUCCESS:disabled:john:/etc/sudoers"
            return ""

    hub = MockHub()

    # Windows enable / add / disable / remove / status
    res_win_add = await manage_poweruser({"host": "WIN-PC", "user": "jonas", "action": "add"}, hub=hub)
    assert "enabled" in res_win_add
    assert "Administrators" in res_win_add

    res_win_remove = await manage_poweruser({"host": "WIN-PC", "user": "jonas", "action": "remove"}, hub=hub)
    assert "disabled" in res_win_remove
    assert "Administrators" in res_win_remove

    res_win_status = await manage_poweruser({"host": "WIN-PC", "user": "jonas"}, hub=hub)
    assert "ENABLED" in res_win_status
    assert "Administrators" in res_win_status

    # Linux enable / disable / status with extra group notice
    res_lin_en = await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "enable"}, hub=hub)
    assert "enabled" in res_lin_en
    assert "/etc/sudoers" in res_lin_en

    res_lin_dis = await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "disable"}, hub=hub)
    assert "disabled" in res_lin_dis

    res_lin_st = await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "status"}, hub=hub)
    assert "DISABLED" in res_lin_st
    assert "sudo" in res_lin_st

    # 5. Edge cases: already enabled/disabled, error response, unknown host, disabled hub
    assert "Hub is disabled" in await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "status"}, hub=None)
    assert "is not connected" in await manage_poweruser({"host": "MISSING", "user": "john", "action": "status"}, hub=hub)

    class EdgeHub:
        def __init__(self):
            self.agents = {"LINUX-SRV": {"platform": "Linux/6.8"}}
            self.responses = [
                "SUCCESS:already_enabled:john:/etc/sudoers",
                "SUCCESS:already_disabled:john:/etc/sudoers\nEXTRA_SUDOERS_D:/etc/sudoers.d/john",
                "ERROR:User 'ghost' not found.",
                "UNEXPECTED_RAW_OUTPUT",
            ]

        async def exec(self, host, kind, cmd, timeout=60):
            return self.responses.pop(0)

    edge_hub = EdgeHub()
    assert "already enabled" in await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "enable"}, hub=edge_hub)
    res_already_dis = await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "disable"}, hub=edge_hub)
    assert "already disabled" in res_already_dis
    assert "/etc/sudoers.d/john" in res_already_dis
    assert ":x: User 'ghost' not found." in await manage_poweruser({"host": "LINUX-SRV", "user": "ghost", "action": "enable"}, hub=edge_hub)
    assert "Failed to enable power user privileges" in await manage_poweruser({"host": "LINUX-SRV", "user": "john", "action": "enable"}, hub=edge_hub)


