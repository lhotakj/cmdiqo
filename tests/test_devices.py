"""Unit tests for LAN device discovery and table formatting."""

import subprocess
import sys

from bot.commands.devices import (
    _run,
    extract_mac_from_cache,
    format_device_table,
    list_devices,
    parse_arp_scan_output,
    parse_ip_neigh_output,
    ping_host,
    resolve_hostname,
    send_wake_on_lan,
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
        ("192.168.1.1", "aa:bb:cc:dd:ee:ff", "router.local"),
        ("192.168.1.20", "11:22:33:44:55:66", "raspberrypi"),
    ]
    table = format_device_table(rows)
    assert "IP ADDRESS" in table
    assert "MAC ADDRESS" in table
    assert "HOST NAME" in table
    assert "192.168.1.1" in table
    assert "router.local" in table
    assert "raspberrypi" in table


def test_resolve_hostname_success(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getnameinfo", lambda sockaddr, flags: ("my-host.lan", "0"))
    assert resolve_hostname("192.168.1.50") == "my-host.lan"


def test_resolve_hostname_failure(monkeypatch):
    import socket

    def mock_getnameinfo(sockaddr, flags):
        raise OSError("Host not found")

    monkeypatch.setattr(socket, "getnameinfo", mock_getnameinfo)
    assert resolve_hostname("192.168.1.99") == "(unknown)"


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
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/" + cmd if cmd == "arp-scan" else None
    )

    def mock_runner(argv):
        return SAMPLE_ARP_OUTPUT

    def mock_resolver(ip):
        names = {
            "192.168.1.1": "router.home",
            "192.168.1.20": "rpi.home",
            "192.168.1.100": "nas.home",
        }
        return names.get(ip, "(unknown)")

    result = list_devices(runner=mock_runner, resolver=mock_resolver)
    assert "IP ADDRESS" in result
    assert "HOST NAME" in result
    assert "192.168.1.1" in result
    assert "router.home" in result
    assert "rpi.home" in result


def test_list_devices_fallback_ip(monkeypatch):
    # arp-scan missing, ip available
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/ip" if cmd == "ip" else None
    )

    def mock_runner(argv):
        return SAMPLE_IP_NEIGH_OUTPUT

    def mock_resolver(ip):
        return "device-" + ip.replace(".", "-")

    result = list_devices(runner=mock_runner, resolver=mock_resolver)
    assert "192.168.1.1" in result
    assert "device-192-168-1-1" in result
    assert "HOST NAME" in result


def test_send_wake_on_lan_missing_target():
    res = send_wake_on_lan({})
    assert "Error: Target MAC or IP address is required" in res


def test_send_wake_on_lan_invalid_mac():
    res = send_wake_on_lan({"target": "not-a-mac"})
    assert "Error: Invalid MAC address" in res


def test_send_wake_on_lan_valid_mac():
    sent_packets = []

    def mock_sender(packet, destination):
        sent_packets.append((packet, destination))

    res = send_wake_on_lan(
        {"target": "aa:bb:cc:dd:ee:ff"},
        broadcast_ip="192.168.1.255",
        port=9,
        sender=mock_sender,
    )
    assert "Wake-on-LAN magic packet sent to `aa:bb:cc:dd:ee:ff`" in res
    assert "192.168.1.255:9" in res
    assert len(sent_packets) == 1
    packet, dest = sent_packets[0]
    assert dest == ("192.168.1.255", 9)
    # Length of WOL packet = 6 bytes (FF) + 16 * 6 bytes (MAC) = 102 bytes
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    expected_mac_bytes = bytes.fromhex("aabbccddeeff")
    assert packet[6:12] == expected_mac_bytes
    assert packet[12:18] == expected_mac_bytes


def test_send_wake_on_lan_from_ip_resolution(monkeypatch):
    sent_packets = []

    def mock_sender(packet, destination):
        sent_packets.append((packet, destination))

    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/" + cmd if cmd == "arp-scan" else None
    )

    def mock_runner(argv):
        return SAMPLE_ARP_OUTPUT

    res = send_wake_on_lan(
        {"target": "192.168.1.20"},
        runner=mock_runner,
        sender=mock_sender,
    )
    assert (
        "Wake-on-LAN magic packet sent to `11:22:33:44:55:66` (resolved from `192.168.1.20`)"
        in res
    )
    assert len(sent_packets) == 1


def test_send_wake_on_lan_sender_exception():
    def broken_sender(packet, destination):
        raise OSError("Network is unreachable")

    res = send_wake_on_lan({"target": "00:11:22:33:44:55"}, sender=broken_sender)
    assert "Error sending Wake-on-LAN packet: Network is unreachable" in res


def test_ping_host_missing_target():
    res = ping_host({})
    assert "Error: Hostname or IP is required" in res


def test_ping_host_invalid_target():
    res = ping_host({"host": "192.168.1.1;rm -rf /"})
    assert "Error: Invalid host or IP" in res


def test_ping_host_success():
    def mock_runner(cmd, timeout):
        return "64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=1.23 ms"

    res = ping_host({"host": "192.168.1.1"}, runner=mock_runner)
    assert "64 bytes from 192.168.1.1" in res


def test_ping_host_real_subprocess():
    # Ping localhost with 1 count
    res = ping_host({"host": "127.0.0.1"}, count=1, timeout=3)
    assert isinstance(res, str)
    assert len(res) > 0


def test_extract_mac_from_cache_ip_fallback(monkeypatch):
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/ip" if cmd == "ip" else None
    )

    def mock_runner(argv):
        return SAMPLE_IP_NEIGH_OUTPUT

    mac = extract_mac_from_cache("192.168.1.20", runner=mock_runner)
    assert mac == "11:22:33:44:55:66"

    # Not found
    assert extract_mac_from_cache("192.168.1.99", runner=mock_runner) is None


def test_ping_host_errors(monkeypatch):
    def mock_fnf(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", mock_fnf)
    assert "ping utility not found" in ping_host({"host": "127.0.0.1"})

    def mock_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ping", timeout=1)

    monkeypatch.setattr(subprocess, "run", mock_timeout)
    assert "ping timed out" in ping_host({"host": "127.0.0.1"})

    def mock_oserror(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(subprocess, "run", mock_oserror)
    assert "Error running ping: Permission denied" in ping_host({"host": "127.0.0.1"})


def test_default_broadcast_sender(monkeypatch):
    from unittest.mock import MagicMock

    from bot.commands.devices import _default_broadcast_sender

    mock_socket = MagicMock()
    monkeypatch.setattr("socket.socket", MagicMock(return_value=mock_socket))
    mock_socket.__enter__.return_value = mock_socket

    _default_broadcast_sender(b"packet", ("255.255.255.255", 9))
    mock_socket.sendto.assert_called_once_with(b"packet", ("255.255.255.255", 9))


def test_run_stderr(monkeypatch):
    def mock_run_stderr(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["test"], returncode=1, stdout="out", stderr="err"
        )

    monkeypatch.setattr(subprocess, "run", mock_run_stderr)
    out = _run(["test"])
    assert "out" in out
    assert "err" in out
