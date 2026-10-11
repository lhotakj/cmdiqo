"""LAN device discovery handlers."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

ARP_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F:]{17})\s*(.*)$")
NEIGH_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+.*lladdr\s+([0-9a-fA-F:]{17})")


def _run(argv: list[str], timeout: int = 180) -> str:
    """Run an external command and return combined output."""
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )  # nosec: B603
        output = p.stdout or ""
        if p.returncode != 0 and p.stderr:
            output += "\n" + p.stderr
        return output
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        return "timed out"


def parse_arp_scan_output(output: str) -> list[tuple[str, str, str]]:
    """Parse output from `arp-scan` command."""
    rows: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        m = ARP_RE.match(line.strip())
        if m:
            rows.append((m.group(1), m.group(2).lower(), m.group(3).strip()))
    return rows


def parse_ip_neigh_output(output: str) -> list[tuple[str, str, str]]:
    """Parse output from `ip neigh show` command."""
    rows: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        m = NEIGH_RE.match(line.strip())
        if m:
            rows.append((m.group(1), m.group(2).lower(), "(kernel neighbour table)"))
    return rows


def resolve_hostname(ip: str, timeout: float = 0.5) -> str:
    """Resolve an IP address to a host name via reverse DNS with a strict timeout."""
    import socket

    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        return socket.getnameinfo((ip, 0), socket.NI_NAMEREQD)[0]
    except Exception:  # noqa: BLE001
        return "(unknown)"
    finally:
        socket.setdefaulttimeout(old_timeout)


def format_device_table(rows: list[tuple[str, str, str]] | Any) -> str:
    """Format discovered devices into a clean ASCII table."""
    if not isinstance(rows, list) or not rows:
        return (
            "No devices discovered.\n"
            "If arp-scan failed, run the container with `network_mode: host` "
            "(see README) or check the bot logs."
        )
    header = ("IP ADDRESS", "MAC ADDRESS", "HOST NAME")
    data = [header] + sorted(set(rows))
    widths = [max(len(r[i]) for r in data) for i in range(3)]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in data
    ]
    return "\n".join(lines)


def list_devices(
    params: dict[str, str] | None = None,
    runner: Callable[[list[str]], str] = _run,
    resolver: Callable[[str], str] = resolve_hostname,
) -> str:
    """List devices on the local network.

    Strategy: full ARP scan via arp-scan (best). Fallback: the kernel
    neighbour table (`ip neigh`) which only shows recently seen devices.
    Hostnames are resolved via reverse DNS lookups.
    """
    from concurrent.futures import ThreadPoolExecutor

    raw_rows: list[tuple[str, str, str]] = []

    if shutil.which("arp-scan"):
        out = runner(["arp-scan", "-l", "--localnet", "--ignoredups", "--timeout=300"])
        raw_rows = parse_arp_scan_output(out)

    if not raw_rows and shutil.which("ip"):
        out = runner(["ip", "neigh", "show"])
        raw_rows = parse_ip_neigh_output(out)

    if not raw_rows:
        return format_device_table([])

    # Resolve hostnames concurrently for all discovered IPs
    ips = [ip for ip, _, _ in raw_rows]
    with ThreadPoolExecutor(max_workers=min(32, max(len(ips), 1))) as executor:
        hostnames = list(executor.map(resolver, ips))

    rows = [(ip, mac, host) for (ip, mac, _), host in zip(raw_rows, hostnames, strict=True)]
    return format_device_table(rows)


# ---------------------------------------------------------------------------
# Wake-on-LAN & Network Diagnostics
# ---------------------------------------------------------------------------

SAFE_HOST_RE = re.compile(r"^[a-zA-Z0-9_\-\.:]+$")


def extract_mac_from_cache(
    target: str,
    runner: Callable[[list[str]], str] = _run,
) -> str | None:
    """Attempt to resolve an IP or hostname to a MAC address using the local ARP table."""
    if shutil.which("arp-scan"):
        out = runner(["arp-scan", "-l", "--localnet", "--ignoredups", "--timeout=300"])
        for ip, mac, _ in parse_arp_scan_output(out):
            if target.lower() in (ip.lower(), mac.lower()):
                return mac

    if shutil.which("ip"):
        out = runner(["ip", "neigh", "show"])
        for ip, mac, _ in parse_ip_neigh_output(out):
            if target.lower() in (ip.lower(), mac.lower()):
                return mac

    return None


def _default_broadcast_sender(packet: bytes, destination: tuple[str, int]) -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, destination)


def send_wake_on_lan(
    params: dict[str, str],
    broadcast_ip: str = "255.255.255.255",
    port: int = 9,
    runner: Callable[[list[str]], str] = _run,
    sender: Callable[[bytes, tuple[str, int]], None] = _default_broadcast_sender,
) -> str:
    """Send a Wake-on-LAN (WOL) magic packet to boot a target device over the LAN."""
    target = params.get("target") or params.get("mac") or ""
    target = target.strip()
    if not target:
        return "Error: Target MAC or IP address is required (e.g. /lan wake 00:11:22:33:44:55)"

    mac_clean = re.sub(r"[^0-9a-fA-F]", "", target)
    resolved_from_ip = False

    if len(mac_clean) != 12:
        cached_mac = extract_mac_from_cache(target, runner=runner)
        if cached_mac:
            mac_clean = re.sub(r"[^0-9a-fA-F]", "", cached_mac)
            resolved_from_ip = True
        else:
            return (
                f"Error: Invalid MAC address '{target}'. "
                f"Expected 12 hexadecimal characters (e.g. 00:11:22:33:44:55) or a known LAN IP."
            )

    try:
        mac_bytes = bytes.fromhex(mac_clean)
        magic_packet = b"\xff" * 6 + mac_bytes * 16

        sender(magic_packet, (broadcast_ip, port))

        formatted_mac = ":".join(mac_clean[i : i + 2].lower() for i in range(0, 12, 2))
        info = f" (resolved from `{target}`)" if resolved_from_ip else ""
        return (
            f":electric_plug: Wake-on-LAN magic packet sent to `{formatted_mac}`{info} "
            f"via UDP broadcast `{broadcast_ip}:{port}`.\n"
            f"If the target machine has Wake-on-LAN (WOL) enabled in BIOS/UEFI, it will boot up shortly."
        )
    except (ValueError, OSError) as exc:
        return f"Error sending Wake-on-LAN packet: {exc}"


def ping_host(
    params: dict[str, str],
    count: int = 3,
    timeout: int = 5,
    runner: Callable[[list[str], int], str] | None = None,
) -> str:
    """Send ICMP ping to a target host or IP on the local network."""
    import sys

    host = params.get("host") or params.get("target") or ""
    host = host.strip()
    if not host:
        return "Error: Hostname or IP is required (e.g. /lan ping 192.168.1.1)"

    if not SAFE_HOST_RE.match(host):
        return f"Error: Invalid host or IP '{host}'"

    cmd = ["ping", "-n" if sys.platform == "win32" else "-c", str(count), host]

    if runner is not None:
        return runner(cmd, timeout)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + count * 2,
            check=False,
        )  # nosec: B603
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return out or f"(ping finished with exit code {proc.returncode})"
    except FileNotFoundError:
        return "Error: ping utility not found on host"
    except subprocess.TimeoutExpired:
        return f"Error: ping timed out after {timeout}s"
    except OSError as exc:
        return f"Error running ping: {exc}"
