"""LAN device discovery handlers."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable

ARP_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F:]{17})\s*(.*)$")
NEIGH_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+.*lladdr\s+([0-9a-fA-F:]{17})")


def _run(argv: list[str], timeout: int = 180) -> str:
    """Run an external command and return combined output."""
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )  # nosec: B603
        output = (p.stdout or "")
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


def format_device_table(rows: list[tuple[str, str, str]]) -> str:
    """Format discovered devices into a clean ASCII table."""
    if not rows:
        return (
            "No devices discovered.\n"
            "If arp-scan failed, run the container with `network_mode: host` "
            "(see README) or check the bot logs."
        )
    header = ("IP ADDRESS", "MAC ADDRESS", "VENDOR / INFO")
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
) -> str:
    """List devices on the local network.

    Strategy: full ARP scan via arp-scan (best). Fallback: the kernel
    neighbour table (`ip neigh`) which only shows recently seen devices.
    """
    rows: list[tuple[str, str, str]] = []

    if shutil.which("arp-scan"):
        out = runner(["arp-scan", "-l", "--localnet", "--ignoredups", "--timeout=300"])
        rows = parse_arp_scan_output(out)

    if not rows and shutil.which("ip"):
        out = runner(["ip", "neigh", "show"])
        rows = parse_ip_neigh_output(out)

    return format_device_table(rows)
