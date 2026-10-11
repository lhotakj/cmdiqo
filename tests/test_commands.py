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

    # Verify appblock command with apps config
    assert "host-blockapp" in specs_by_name
    blockapp_spec = specs_by_name["host-blockapp"]
    assert blockapp_spec["command"]["python"] == "commands.appblock:manage_appblock"
    assert isinstance(blockapp_spec.get("config"), dict)
    assert "apps" in blockapp_spec["config"]
    assert "roblox" in blockapp_spec["config"]["apps"]
    assert "host-appblock" in specs_by_name


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
                return "SUCCESS:disabled:john"
            if "$action = 'enable'" in cmd or 'ACTION="enable"' in cmd:
                return "SUCCESS:enabled:john"
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

    res_ena = await manage_users({"host": "WIN-PC", "user": "john", "action": "enable"}, hub=mock_hub)
    assert "enabled" in res_ena

