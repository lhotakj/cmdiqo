"""Unit tests for LAN device discovery and table formatting."""

import subprocess
import sys

from bot.commands.devices import (
    _run,
    format_device_table,
    list_devices,
    parse_arp_scan_output,
    parse_ip_neigh_output,
)

SAMPLE_ARP_OUTPUT = """
Interface: eth0, type: EN10MB, MAC: 00:11:22:33:44:55, IPv4: 192.168.1.50
Starting arp-scan 1.9.7 with 256 hosts (https://github.com/royhills/arp-scan)
192.168.1.1\taa:bb:cc:dd:ee:ff\tTechnicolor CH7465VF
192.168.1.20\t11:22:33:44:55:66\tRaspberry Pi Trading Ltd
192.168.1.100\tb8:27:eb:12:34:56\tRaspberry Pi Foundation

3 packets received by filter, 0 packets dropped by kernel
Ending arp-scan 1.9.7: 256 hosts scanned in 1.482 seconds (172.74 hosts/sec). 3 responded
"""

SAMPLE_IP_NEIGH_OUTPUT = """
192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
192.168.1.20 dev eth0 lladdr 11:22:33:44:55:66 STALE
192.168.1.50 dev eth0  FAILED
"""


def test_parse_arp_scan_output():
    rows = parse_arp_scan_output(SAMPLE_ARP_OUTPUT)
    assert len(rows) == 3
    assert rows[0] == ("192.168.1.1", "aa:bb:cc:dd:ee:ff", "Technicolor CH7465VF")
    assert rows[1] == ("192.168.1.20", "11:22:33:44:55:66", "Raspberry Pi Trading Ltd")
    assert rows[2] == ("192.168.1.100", "b8:27:eb:12:34:56", "Raspberry Pi Foundation")


def test_parse_ip_neigh_output():
    rows = parse_ip_neigh_output(SAMPLE_IP_NEIGH_OUTPUT)
    assert len(rows) == 2
    assert rows[0] == ("192.168.1.1", "aa:bb:cc:dd:ee:ff", "(kernel neighbour table)")
    assert rows[1] == ("192.168.1.20", "11:22:33:44:55:66", "(kernel neighbour table)")


def test_format_device_table_empty():
    table = format_device_table([])
    assert "No devices discovered." in table
    assert "network_mode: host" in table


def test_format_device_table_content():
    rows = [
        ("192.168.1.1", "aa:bb:cc:dd:ee:ff", "Router"),
        ("192.168.1.20", "11:22:33:44:55:66", "Raspberry Pi"),
    ]
    table = format_device_table(rows)
    assert "IP ADDRESS" in table
    assert "MAC ADDRESS" in table
    assert "192.168.1.1" in table
    assert "Router" in table


def test_run_success():
    out = _run([sys.executable, "-c", "print('device_test_ok')"])
    assert "device_test_ok" in out


def test_run_not_found():
    out = _run(["nonexistent_command_xyz_123"])
    assert out == ""


def test_run_timeout(monkeypatch):
    def mock_subp(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="sleep", timeout=1)

    monkeypatch.setattr("subprocess.run", mock_subp)
    out = _run(["sleep", "10"])
    assert out == "timed out"


def test_list_devices_with_mock_runner(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/" + cmd if cmd == "arp-scan" else None)

    def mock_runner(argv):
        return SAMPLE_ARP_OUTPUT

    result = list_devices(runner=mock_runner)
    assert "192.168.1.1" in result
    assert "Technicolor CH7465VF" in result


def test_list_devices_fallback_ip(monkeypatch):
    # arp-scan missing, ip available
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ip" if cmd == "ip" else None)

    def mock_runner(argv):
        return SAMPLE_IP_NEIGH_OUTPUT

    result = list_devices(runner=mock_runner)
    assert "192.168.1.1" in result
    assert "kernel neighbour table" in result
