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
    ]
    for param in unsafe_candidates:
        with pytest.raises(ValueError, match="contains unsafe characters"):
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
    # Verify no legacy /home commands remain in commands:
    assert not any(t.startswith("/home") for t in triggers)


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
