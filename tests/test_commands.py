"""Unit tests for command loading, regex parsing, and parameter validation."""

import pytest

from bot.app import load_commands, trigger_to_regex, validate_params


def test_trigger_to_regex_exact_match():
    pattern = trigger_to_regex("/lan devices list")
    assert pattern.match("/lan devices list")
    assert pattern.match("/lan devices list   ")
    assert pattern.match("/LAN DEVICES LIST")
    assert not pattern.match("/lan devices")
    assert not pattern.match("/lan devices list extra")


def test_trigger_to_regex_with_placeholder():
    pattern = trigger_to_regex("/home docker restart {service}")
    m = pattern.match("/home docker restart kinetiqo")
    assert m is not None
    assert m.group("service") == "kinetiqo"

    # With hyphens, dots, numbers, underscores
    m2 = pattern.match("/home docker restart web-api_v2.0")
    assert m2 is not None
    assert m2.group("service") == "web-api_v2.0"

    # Reject invalid characters directly at regex level
    m3 = pattern.match("/home docker restart kinetiqo;rm -rf /")
    assert m3 is None


def test_validate_params_safe():
    safe_params = {
        "service": "kinetiqo",
        "pc": "DESKTOP-1234",
        "version": "1.2.3",
        "ip_port": "192.168.1.1:8080",
    }
    # Should not raise
    validate_params(safe_params)


def test_validate_params_unsafe():
    unsafe_candidates = [
        {"service": "kinetiqo;calc.exe"},
        {"service": "kinetiqo&calc.exe"},
        {"service": "kinetiqo|calc.exe"},
        {"service": "`whoami`"},
        {"service": "$(whoami)"},
        {"service": "kinetiqo\nreboot"},
    ]
    for param in unsafe_candidates:
        with pytest.raises(ValueError, match="contains unsafe characters"):
            validate_params(param)


def test_load_commands_valid_file():
    commands = load_commands("config/commands.yaml")
    assert len(commands) >= 3
    triggers = [c["spec"]["trigger"] for c in commands]
    assert "/lan devices list" in triggers
    assert "/home docker restart {service}" in triggers


def test_load_commands_nonexistent_file():
    commands = load_commands("config/nonexistent_commands.yaml")
    assert commands == []
