"""Remote agent update command handlers for Slack and WebGUI (Windows & Linux)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("cmdiqo-bot.commands.update")

GITHUB_RELEASES_API = "https://api.github.com/repos/lhotakj/cmdiqo/releases"
SAFE_VERSION_ARG_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

ASSET_BY_ARCH: dict[str, str] = {
    "win64": "cmdiqo-agent-windows-x64-setup.exe",
    "linux64": "cmdiqo-agent",
    "arm64": "cmdiqo-agent-linux-arm64",
    "armv6": "cmdiqo-agent-linux-armv6",
}


def normalize_target_arch(platform_str: str, arch_str: str) -> str | None:
    """Resolve agent platform/arch metadata to canonical release asset architecture."""
    p = (platform_str or "").strip().lower()
    a = (arch_str or "").strip().lower()
    if "win" in p or a == "win64":
        return "win64"
    if a in ("arm64", "aarch64"):
        return "arm64"
    if "armv6" in a or a in ("arm", "armhf", "armv6l"):
        return "armv6"
    if "linux" in p or a in ("linux64", "x86_64", "amd64", "x64"):
        return "linux64"
    return None


def parse_semver_tuple(version: str) -> tuple[int, int, int] | None:
    """Parse version strings like 'v0.37.55', '0.37.0', or '0.37.devel+ABC' into comparable tuples."""
    clean = (version or "").strip().lstrip("vV")
    m_num = re.match(r"^(\d+)\.(\d+)\.(\d+)", clean)
    if m_num:
        return int(m_num.group(1)), int(m_num.group(2)), int(m_num.group(3))
    m_dev = re.match(r"^(\d+)\.(\d+)\.devel(?:\+|$)", clean)
    if m_dev:
        return int(m_dev.group(1)), int(m_dev.group(2)), -1
    m_two = re.match(r"^(\d+)\.(\d+)$", clean)
    if m_two:
        return int(m_two.group(1)), int(m_two.group(2)), 0
    return None


def is_newer_version(current: str, target: str) -> bool:
    """Return True when target semver is strictly newer than current semver."""
    cur_t = parse_semver_tuple(current)
    tgt_t = parse_semver_tuple(target)
    if cur_t is None or tgt_t is None:
        return current.strip().lstrip("vV") != target.strip().lstrip("vV")
    return tgt_t > cur_t


def fetch_release_info(version: str = "latest") -> dict[str, Any]:
    """Query GitHub Releases API for release metadata and asset download URLs."""
    req_ver = (version or "latest").strip()
    if req_ver.lower() in ("", "latest"):
        url = f"{GITHUB_RELEASES_API}/latest"
    else:
        tag = req_ver if req_ver.lower().startswith("v") else f"v{req_ver}"
        url = f"{GITHUB_RELEASES_API}/tags/{tag}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "cmdiqo-bot-updater",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec: B310
        payload = json.loads(resp.read().decode("utf-8"))

    tag_name = str(payload.get("tag_name", "")).strip()
    assets = {
        str(a.get("name", "")): str(a.get("browser_download_url", ""))
        for a in payload.get("assets", [])
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url")
    }
    return {
        "tag": tag_name,
        "version": tag_name.lstrip("vV"),
        "assets": assets,
    }


def build_windows_update_script(download_url: str, tamper_protect: bool) -> str:
    """Generate single-quoted PowerShell script for Phase-1 legacy Windows agent update."""
    clean_url = download_url.replace("'", "''")
    opt_flag = "" if tamper_protect else " --no-tamper-protect"
    return f"""$ErrorActionPreference = 'Stop'
$url = '{clean_url}'
$exePath = Join-Path $env:TEMP 'cmdiqo-agent-remote-setup.exe'
$cmdPath = Join-Path $env:TEMP 'cmdiqo-agent-remote-upgrade.cmd'
$taskName = 'CmdiqoRemoteUpdate-' + $PID
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $url -OutFile $exePath -UseBasicParsing -TimeoutSec 120
$item = Get-Item -LiteralPath $exePath
if ($item.Length -lt 102400) {{ throw 'Downloaded setup binary is unexpectedly small.' }}
$bytes = [IO.File]::ReadAllBytes($exePath)
if ($bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {{ throw 'Downloaded file is not a valid Windows executable.' }}
$q = [char]34
$line = 'ping -n 3 127.0.0.1 >nul && ' + $q + $exePath + $q + ' --cli --upgrade{opt_flag}'
[IO.File]::WriteAllText($cmdPath, '@echo off' + [Environment]::NewLine + $line + [Environment]::NewLine + 'del /f /q ' + $q + $cmdPath + $q + [Environment]::NewLine)
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ' + $q + $cmdPath + $q)
$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'UPDATE_SCHEDULED:Windows agent upgrade queued; service restarting shortly.'"""


def build_linux_update_script(download_url: str) -> str:
    """Generate shell script for Phase-1 legacy Linux agent update."""
    clean_url = download_url.replace("'", "'\"'\"'")
    return f"""set -e
URL='{clean_url}'
BIN_DEST='/usr/local/bin/cmdiqo-agent'
if [ ! -f "$BIN_DEST" ] && command -v cmdiqo-agent >/dev/null 2>&1; then
    BIN_DEST=$(command -v cmdiqo-agent)
fi
TMP_BIN="${{BIN_DEST}}.new.$$"
trap 'rm -f "$TMP_BIN"' EXIT
if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 15 --max-time 180 "$URL" -o "$TMP_BIN"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TMP_BIN" "$URL"
else
    echo 'ERROR:Neither curl nor wget is installed on target host.'
    exit 1
fi
SIZE=$(wc -c < "$TMP_BIN" | tr -d ' ')
if [ "${{SIZE:-0}}" -lt 102400 ]; then
    echo "ERROR:Downloaded binary is too small (${{SIZE:-0}} bytes)."
    exit 1
fi
chmod 0755 "$TMP_BIN"
if ! "$TMP_BIN" --version >/dev/null 2>&1; then
    echo 'ERROR:Downloaded binary failed --version sanity check.'
    exit 1
fi
mv -f "$TMP_BIN" "$BIN_DEST"
trap - EXIT
if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --quiet --on-active=1s systemctl restart cmdiqo-agent.service 2>/dev/null || true
elif command -v systemctl >/dev/null 2>&1; then
    nohup sh -c 'sleep 1 && systemctl restart cmdiqo-agent.service' >/dev/null 2>&1 &
fi
echo 'UPDATE_SCHEDULED:Linux agent binary replaced; service restarting.'"""


async def _update_single_host(
    host: str,
    action: str,
    hub: Hub,
    release: dict[str, Any],
) -> str:
    agent_info = hub.agents.get(host) if isinstance(getattr(hub, "agents", None), dict) else None
    if not agent_info:
        return f":warning: Agent `{host}` is not connected."

    current_ver = str(agent_info.get("version", "?"))
    platform_str = str(agent_info.get("platform", ""))
    arch_raw = str(agent_info.get("arch", ""))
    tamper_protect = bool(agent_info.get("tamper_protect", False))
    auto_update = bool(agent_info.get("auto_update", False))
    update_capable = bool(agent_info.get("update_capable", False))

    canonical_arch = normalize_target_arch(platform_str, arch_raw)
    target_ver = str(release.get("version", ""))
    tag = str(release.get("tag", ""))
    assets: dict[str, str] = release.get("assets", {})

    if action in ("status", "check"):
        state = (
            ":arrow_up: Update available"
            if is_newer_version(current_ver, target_ver)
            else ":white_check_mark: Up to date"
        )
        tp_label = "enabled" if tamper_protect else "disabled"
        au_label = "enabled" if auto_update else "disabled"
        return (
            f"Agent `{host}` ({canonical_arch or arch_raw}, {platform_str}):\n"
            f"• Installed: `v{current_ver}`\n"
            f"• Latest Release: `{tag}` ({state})\n"
            f"• Tamper-Protect: `{tp_label}` | Auto-Update: `{au_label}`"
        )

    if not canonical_arch or canonical_arch not in ASSET_BY_ARCH:
        return f":warning: Unsupported platform/architecture for remote update on `{host}` ({platform_str} / {arch_raw})."

    asset_name = ASSET_BY_ARCH[canonical_arch]
    download_url = assets.get(asset_name)
    if not download_url:
        return f":warning: Release `{tag}` does not contain asset `{asset_name}`."

    if action == "latest" and not is_newer_version(current_ver, target_ver):
        return f":white_check_mark: Agent `{host}` is already up to date (`v{current_ver}` vs `{tag}`)."

    if update_capable and hasattr(hub, "update_agent"):
        raw = await hub.update_agent(host, version=target_ver, url=download_url, timeout=180)
        return f"🔄 *{host}* (`v{current_ver}` → `{tag}` via native updater):\n{raw}"

    if canonical_arch == "win64":
        script = build_windows_update_script(download_url, tamper_protect)
        raw = await hub.exec(host, "ps1", script, timeout=180)
    else:
        script = build_linux_update_script(download_url)
        raw = await hub.exec(host, "shell", script, timeout=180)
    return f"🔄 *{host}* (`v{current_ver}` → `{tag}`):\n{raw}"


async def manage_update(
    params: dict[str, str],
    hub: Hub | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Check or trigger remote agent updates from GitHub Releases."""
    del config
    if hub is None:
        return ":warning: Agent Hub is disabled; remote agent update is unavailable."

    host = (params.get("host") or "").strip()
    raw_action = (params.get("action") or params.get("version") or "latest").strip()
    if raw_action and not SAFE_VERSION_ARG_RE.match(raw_action):
        return ":warning: Invalid update version/action parameter."

    lower_action = raw_action.lower()
    if lower_action in ("status", "check"):
        action = "status"
        release_ref = "latest"
    elif lower_action in ("latest", "update", ""):
        action = "latest"
        release_ref = "latest"
    else:
        action = "pin"
        release_ref = raw_action

    try:
        release = await asyncio.to_thread(fetch_release_info, release_ref)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Failed fetching GitHub release info (%s): %s", release_ref, exc)
        return f":warning: Could not fetch GitHub release metadata (`{release_ref}`): {exc}"

    if not host or host.lower() == "all":
        connected = sorted(hub.agents) if isinstance(getattr(hub, "agents", None), dict) else []
        if not connected:
            return "No agents connected."
        results: list[str] = []
        for agent_name in connected:
            try:
                res = await _update_single_host(agent_name, action, hub, release)
            except (OSError, RuntimeError, ValueError, TypeError, TimeoutError, urllib.error.URLError) as exc:
                log.exception("Fleet update failed for %s", agent_name)
                res = f":x: *{agent_name}*: {exc}"
            results.append(res)
        return "\n\n".join(results)

    return await _update_single_host(host, action, hub, release)
