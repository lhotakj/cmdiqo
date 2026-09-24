"""Parental control command handlers: manage YouTube and Roblox domain blocks on remote agents (Windows & Linux)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("shango-bot.commands.parental")

YOUTUBE_DOMAINS: list[str] = [
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "youtubekids.com",
    "www.youtubekids.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "ytimg.com",
    "s.ytimg.com",
    "i.ytimg.com",
    "yt3.ggpht.com",
    "youtube.googleapis.com",
    "youtubei.googleapis.com",
]

ROBLOX_DOMAINS: list[str] = [
    "roblox.com",
    "www.roblox.com",
    "api.roblox.com",
    "web.roblox.com",
    "assetgame.roblox.com",
    "setup.roblox.com",
    "rbxcdn.com",
    "www.rbxcdn.com",
    "robloxlabs.com",
]

PARENTAL_DOMAINS: list[str] = sorted(set(YOUTUBE_DOMAINS + ROBLOX_DOMAINS))
ALL_DOMAINS: list[str] = PARENTAL_DOMAINS


def build_parental_script_windows(action: str, domains: list[str] | None = None) -> str:
    """Generate the self-contained PowerShell script payload to execute on a Windows agent."""
    domain_list = domains if domains is not None else PARENTAL_DOMAINS
    domain_items = ", ".join(f"'{d}'" for d in domain_list)

    return f"""$ErrorActionPreference = "Stop"
try {{
    $action = '{action}'.ToLower()
    $hostsPath = "$env:SystemRoot\\System32\\drivers\\etc\\hosts"
    $content = if (Test-Path $hostsPath) {{ [IO.File]::ReadAllText($hostsPath) }} else {{ "" }}
    $hasBlock = $content -match "(?s)# parental:start.*?# parental:end"

    if ($action -eq "status") {{
        if (-not $hasBlock) {{
            Write-Output "STATUS:NOT_CONFIGURED"
            exit 0
        }}
        $block = [regex]::Match($content, "(?s)# parental:start(.*?)# parental:end").Groups[1].Value
        $active = @([regex]::Matches($block, "(?m)^\\s*0\\.0\\.0\\.0\\s+")).Count
        $commented = @([regex]::Matches($block, "(?m)^\\s*#\\s*0\\.0\\.0\\.0\\s+")).Count
        if ($active -gt 0 -and $commented -eq 0) {{
            Write-Output "STATUS:ON:$active"
        }} elseif ($commented -gt 0 -and $active -eq 0) {{
            Write-Output "STATUS:OFF:$commented"
        }} else {{
            Write-Output "STATUS:PARTIAL:active=$active:commented=$commented"
        }}
        exit 0
    }}

    $clean = ($content -replace "(?s)# parental:start.*?# parental:end\\r?\\n?", "").TrimEnd()
    $domains = @({domain_items})
    $blockLines = @("# parental:start")
    foreach ($d in $domains) {{
        if ($action -eq "on") {{
            $blockLines += "0.0.0.0 $d"
        }} else {{
            $blockLines += "# 0.0.0.0 $d"
        }}
    }}
    $blockLines += "# parental:end"

    $finalContent = ($clean + "`r`n`r`n" + ($blockLines -join "`r`n")).Trim() + "`r`n"
    [IO.File]::WriteAllText($hostsPath, $finalContent, (New-Object System.Text.UTF8Encoding($false)))
    Clear-DnsClientCache -ErrorAction SilentlyContinue
    & ipconfig /flushdns | Out-Null
    Write-Output "SUCCESS:${{action}}:$($domains.Count)"
}} catch {{
    Write-Error $_
    exit 1
}}
"""


# Alias for backward-compatibility
build_parental_script = build_parental_script_windows


def build_parental_script_linux(action: str, domains: list[str] | None = None) -> str:
    """Generate the self-contained Bash script payload to execute on a Linux agent."""
    domain_list = domains if domains is not None else PARENTAL_DOMAINS
    prefix = "0.0.0.0" if action == "on" else "# 0.0.0.0"
    block_lines = "\n".join(f"{prefix} {d}" for d in domain_list)

    return f"""set -e
HOSTS_FILE="/etc/hosts"
ACTION="{action}"

if [ "$ACTION" = "status" ]; then
    if ! grep -q "# parental:start" "$HOSTS_FILE" 2>/dev/null; then
        echo "STATUS:NOT_CONFIGURED"
        exit 0
    fi
    ACTIVE=$(sed -n '/# parental:start/,/# parental:end/p' "$HOSTS_FILE" | grep -E '^[[:space:]]*0\\.0\\.0\\.0[[:space:]]+' | wc -l)
    COMMENTED=$(sed -n '/# parental:start/,/# parental:end/p' "$HOSTS_FILE" | grep -E '^[[:space:]]*#[[:space:]]*0\\.0\\.0\\.0[[:space:]]+' | wc -l)
    if [ "$ACTIVE" -gt 0 ] && [ "$COMMENTED" -eq 0 ]; then
        echo "STATUS:ON:$ACTIVE"
    elif [ "$COMMENTED" -gt 0 ] && [ "$ACTIVE" -eq 0 ]; then
        echo "STATUS:OFF:$COMMENTED"
    else
        echo "STATUS:PARTIAL:active=$ACTIVE:commented=$COMMENTED"
    fi
    exit 0
fi

# Remove existing block
sed -i '/# parental:start/,/# parental:end/d' "$HOSTS_FILE" 2>/dev/null || true

# Append updated block
cat << 'EOF' >> "$HOSTS_FILE"
# parental:start
{block_lines}
# parental:end
EOF

# Flush system resolver cache if available
systemd-resolve --flush-caches 2>/dev/null || resolvectl flush-caches 2>/dev/null || true

echo "SUCCESS:$ACTION:{len(domain_list)}"
"""


async def _run_local_bash(script: str) -> str:
    """Execute bash script locally for self / home fallback."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return f"ERROR: {err or out or f'exit code {proc.returncode}'}"
        return out
    except FileNotFoundError:
        return "ERROR: bash not found on local host"
    except (OSError, RuntimeError) as exc:
        return f"ERROR: {exc}"


async def manage_parental(params: dict[str, str], hub: Hub | None = None) -> str:
    """Handle /host {host} parental {action} requests for Windows and Linux hosts."""
    host = params.get("host", "").strip()
    action = params.get("action", "").lower().strip()

    if not host:
        return ":no_entry_sign: Target host is required."

    if action not in ("on", "off", "status"):
        return (
            f":warning: Invalid action `{action}`.\n"
            f"Usage: `/host {host} parental on` | `/host {host} parental off` | `/host {host} parental status`"
        )

    # 1. Determine execution target and platform
    is_local_target = host.lower() in ("local", "localhost", "container", "self")
    is_home_unregistered = host.lower() == "home" and (
        hub is None or "home" not in getattr(hub, "agents", {})
    )

    if is_local_target or is_home_unregistered:
        # Local Linux execution
        os_label = "Linux (local /etc/hosts)"
        cmd = build_parental_script_linux(action)
        raw_output = await _run_local_bash(cmd)
    else:
        # Remote agent execution
        if hub is None:
            return ":no_entry_sign: Hub is disabled. Cannot communicate with remote agents."

        if host not in hub.agents:
            connected = ", ".join(sorted(hub.agents)) or "(none)"
            return f":no_entry_sign: Agent `{host}` is not connected. Connected agents: {connected}"

        agent_info = hub.agents[host]
        platform_str = str(agent_info.get("platform", "")).lower()
        is_windows = "windows" in platform_str or "win" in platform_str

        if is_windows:
            kind = "ps1"
            cmd = build_parental_script_windows(action)
            os_label = f"Windows (`{host}` hosts)"
        else:
            kind = "shell"
            cmd = build_parental_script_linux(action)
            os_label = f"Linux (`{host}` /etc/hosts)"

        try:
            raw_output = await hub.exec(host, kind=kind, cmd=cmd, timeout=60)
        except Exception as exc:
            log.exception("Remote parental command failed on agent '%s'", host)
            return f":x: Failed to execute parental command on `{host}`: {exc}"

    output = (raw_output or "").strip()

    # 2. Format response based on output
    if action == "status":
        if "STATUS:ON" in output:
            count = output.split(":")[-1] if ":" in output else len(PARENTAL_DOMAINS)
            return (
                f":shield: *Parental Controls on `{host}`: ACTIVE*\n"
                f"• **Target:** {os_label}\n"
                f"• YouTube & Roblox are currently **BLOCKED** ({count} domains redirected to 0.0.0.0)."
            )
        if "STATUS:OFF" in output:
            count = output.split(":")[-1] if ":" in output else len(PARENTAL_DOMAINS)
            return (
                f":unlock: *Parental Controls on `{host}`: INACTIVE*\n"
                f"• **Target:** {os_label}\n"
                f"• YouTube & Roblox are currently **UNBLOCKED** ({count} domains commented out in hosts)."
            )
        if "STATUS:NOT_CONFIGURED" in output:
            return (
                f":information_source: *Parental Controls on `{host}`: NOT CONFIGURED*\n"
                f"• **Target:** {os_label}\n"
                f"• No parental block found in hosts. Run `/host {host} parental on` to enable."
            )
        return f":warning: *Parental Controls on `{host}` Status:*\n```{output}```"

    if "SUCCESS:on" in output:
        return (
            f":white_check_mark: :shield: *Parental controls ENABLED on `{host}`!*\n"
            f"• **Target:** {os_label}\n"
            f"• **Blocked:** {len(PARENTAL_DOMAINS)} domains (YouTube, YouTube Kids, Roblox) redirected to `0.0.0.0`.\n"
            f"• **DNS:** Resolver cache flushed successfully."
        )

    if "SUCCESS:off" in output:
        return (
            f":white_check_mark: :unlock: *Parental controls DISABLED on `{host}`!*\n"
            f"• **Target:** {os_label}\n"
            f"• **Unblocked:** {len(PARENTAL_DOMAINS)} domains commented out in hosts.\n"
            f"• **DNS:** Resolver cache flushed successfully."
        )

    return f":warning: Parental control completed with output from `{host}`:\n```{output}```"
