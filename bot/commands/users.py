"""User management command handlers: list users, check status, disable, and enable users on remote agents (Windows & Linux)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("cmdiqo-bot.commands.users")


def build_user_script_windows(action: str, username: str = "") -> str:
    """Generate PowerShell script payload for Windows user management."""
    clean_user = username.replace("'", "''")
    return f"""$ErrorActionPreference = "Stop"
try {{
    $action = '{action}'.ToLower()
    $targetUser = '{clean_user}'

    if ($action -eq "users") {{
        $users = Get-LocalUser | Sort-Object Name
        $lines = @()
        foreach ($u in $users) {{
            $icon = if ($u.Enabled) {{ "✔️" }} else {{ "🚫" }}
            $statusStr = if ($u.Enabled) {{ "active" }} else {{ "locked" }}
            $lines += "$icon $($u.Name) ($statusStr)"
        }}
        Write-Output ("USERS_LIST:`n" + ($lines -join "`n"))
        exit 0
    }}

    if ($action -eq "status") {{
        $u = Get-LocalUser -Name $targetUser -ErrorAction SilentlyContinue
        if (-not $u) {{
            Write-Output "ERROR:User '$targetUser' not found."
            exit 1
        }}
        $fullName = if ($u.FullName) {{ $u.FullName }} else {{ "N/A" }}
        $homeDir = (Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object LocalPath -like "*\\$targetUser").LocalPath
        if (-not $homeDir) {{ $homeDir = "$env:SystemDrive\\Users\\$targetUser" }}
        $statusStr = if ($u.Enabled) {{ "active" }} else {{ "locked" }}
        $icon = if ($u.Enabled) {{ "✔️" }} else {{ "🚫" }}

        $sessTime = "Not logged in"
        try {{
            $sessions = Get-CimInstance Win32_LogonSession -ErrorAction SilentlyContinue | Where-Object LogonType -in (2,10)
            foreach ($s in $sessions) {{
                $sessId = $s.LogonId
                $loggedOn = Get-CimInstance Win32_LoggedOnUser -ErrorAction SilentlyContinue | Where-Object {{ $_.Dependent.Antecedent -like "*$sessId*" }}
                if ($loggedOn -and $loggedOn.Antecedent -like "*Name=`"$targetUser`"*") {{
                    $diff = (Get-Date) - $s.StartTime
                    $hours = [int]$diff.TotalHours
                    $mins = $diff.Minutes
                    $secs = $diff.Seconds
                    $sessTime = "$hours" + "h " + "$mins" + "m " + "$secs" + "s"
                    break
                }}
            }}
        }} catch {{}}

        Write-Output "USER_STATUS|$($u.Name)|$fullName|$homeDir|$statusStr|$icon|$sessTime"
        exit 0
    }}

    if ($action -eq "disable") {{
        Disable-LocalUser -Name $targetUser
        Write-Output "SUCCESS:disabled:$targetUser"
        exit 0
    }}

    if ($action -eq "enable") {{
        Enable-LocalUser -Name $targetUser
        try {{ Unlock-LocalUser -Name $targetUser -ErrorAction SilentlyContinue }} catch {{}}
        Write-Output "SUCCESS:enabled:$targetUser"
        exit 0
    }}

    Write-Error "Invalid action: $action"
    exit 1
}} catch {{
    Write-Error $_
    exit 1
}}
"""


def build_user_script_linux(action: str, username: str = "") -> str:
    """Generate Bash script payload for Linux user management."""
    return f"""set -e
ACTION="{action}"
TARGET_USER="{username}"

if [ "$ACTION" = "users" ]; then
    echo "USERS_LIST:"
    getent passwd | awk -F: '$3 >= 1000 || $3 == 0 {{print $1}}' | sort -u | while read -r u; do
        is_locked=0
        if command -v passwd >/dev/null 2>&1; then
            pstat=$(passwd -S "$u" 2>/dev/null || true)
            if echo "$pstat" | grep -qE '\\sL\\s|\\sLK\\s'; then
                is_locked=1
            fi
        fi
        if [ $is_locked -eq 0 ] && [ -f /etc/shadow ]; then
            shadow_entry=$(grep -E "^${{u}}:" /etc/shadow 2>/dev/null || true)
            shadow_hash=$(echo "$shadow_entry" | cut -d: -f2)
            case "$shadow_hash" in
                !*|\\**|L) is_locked=1 ;;
            esac
        fi
        if [ $is_locked -eq 1 ]; then
            echo "🚫 $u (locked)"
        else
            echo "✔️ $u (active)"
        fi
    done
    exit 0
fi

if [ "$ACTION" = "status" ]; then
    if ! getent passwd "$TARGET_USER" >/dev/null 2>&1; then
        echo "ERROR:User '$TARGET_USER' not found."
        exit 1
    fi
    gecos=$(getent passwd "$TARGET_USER" | cut -d: -f5 | cut -d, -f1)
    if [ -z "$gecos" ]; then gecos="N/A"; fi
    homedir=$(getent passwd "$TARGET_USER" | cut -d: -f6)

    is_locked=0
    if command -v passwd >/dev/null 2>&1; then
        pstat=$(passwd -S "$TARGET_USER" 2>/dev/null || true)
        if echo "$pstat" | grep -qE '\\sL\\s|\\sLK\\s'; then
            is_locked=1
        fi
    fi
    if [ $is_locked -eq 0 ] && [ -f /etc/shadow ]; then
        shadow_entry=$(grep -E "^${{TARGET_USER}}:" /etc/shadow 2>/dev/null || true)
        shadow_hash=$(echo "$shadow_entry" | cut -d: -f2)
        case "$shadow_hash" in
            !*|\\**|L) is_locked=1 ;;
        esac
    fi
    if [ $is_locked -eq 1 ]; then
        status_str="locked"
        icon="🚫"
    else
        status_str="active"
        icon="✔️"
    fi

    sess_time="Not logged in"
    w_out=$(w -h "$TARGET_USER" 2>/dev/null | head -n 1 || true)
    if [ -n "$w_out" ]; then
        sess_sec=$(ps -u "$TARGET_USER" -o etimes= 2>/dev/null | sort -n | tail -n 1 | tr -d ' ' || true)
        if [ -n "$sess_sec" ] && [ "$sess_sec" -eq "$sess_sec" ] 2>/dev/null; then
            h=$((sess_sec / 3600))
            m=$(((sess_sec % 3600) / 60))
            s=$((sess_sec % 60))
            sess_time="${{h}}h ${{m}}m ${{s}}s"
        fi
    fi

    echo "USER_STATUS|${{TARGET_USER}}|${{gecos}}|${{homedir}}|${{status_str}}|${{icon}}|${{sess_time}}"
    exit 0
fi

if [ "$ACTION" = "disable" ]; then
    usermod -L "$TARGET_USER"
    echo "SUCCESS:disabled:$TARGET_USER"
    exit 0
fi

if [ "$ACTION" = "enable" ]; then
    usermod -U "$TARGET_USER"
    echo "SUCCESS:enabled:$TARGET_USER"
    exit 0
fi

echo "Invalid action: $ACTION"
exit 1
"""


async def manage_users(params: dict[str, str], hub: Hub | None = None) -> str:
    """Handle /host {host} users and /host {host} user {user} [status|disable|enable] requests."""
    host = params.get("host", "").strip()
    username = params.get("user", "").strip()

    # Deduce action based on params or text
    action = params.get("action", "").lower().strip()
    if not action:
        spec_name = params.get("_spec_name", "").lower()
        spec_trig = params.get("_spec_trigger", "").lower()
        if "enable" in spec_name or "enable" in spec_trig:
            action = "enable"
        elif "disable" in spec_name or "disable" in spec_trig:
            action = "disable"
        elif "status" in spec_name or "status" in spec_trig:
            action = "status"
        elif "users" in spec_name or "users" in spec_trig:
            action = "users"
        elif username:
            action = "status"
        else:
            action = "users"

    if not host:
        return ":no_entry_sign: Target host is required."

    if action not in ("users", "status", "disable", "enable"):
        return (
            f":warning: Invalid user action `{action}`.\n"
            f"Supported commands:\n"
            f"• `/host {host} users`\n"
            f"• `/host {host} user <user> status`\n"
            f"• `/host {host} user <user> disable`\n"
            f"• `/host {host} user <user> enable`"
        )

    if action in ("status", "disable", "enable") and not username:
        return f":no_entry_sign: Username is required for `/host {host} user <user> {action}`."

    # 1. Determine execution target and platform
    is_local_target = host.lower() in ("local", "localhost", "container", "self")
    is_home_unregistered = host.lower() == "home" and (
        hub is None or "home" not in getattr(hub, "agents", {})
    )

    if is_local_target or is_home_unregistered:
        cmd = build_user_script_linux(action, username)
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            raw_output = (stdout or b"").decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not raw_output:
                raw_output = (stderr or b"").decode("utf-8", errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            return f":x: Failed to execute user command locally: {exc}"
    else:
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
            cmd = build_user_script_windows(action, username)
        else:
            kind = "shell"
            cmd = build_user_script_linux(action, username)

        try:
            raw_output = await hub.exec(host, kind=kind, cmd=cmd, timeout=60)
        except Exception as exc:
            log.exception("Remote user command failed on agent '%s'", host)
            return f":x: Failed to execute user command on `{host}`: {exc}"

    output = (raw_output or "").strip()

    if output.startswith("ERROR:"):
        return f":x: {output[6:].strip()}"

    # 2. Format responses
    if action == "users":
        if "USERS_LIST:" in output:
            lines = [line.strip() for line in output.split("USERS_LIST:", 1)[1].splitlines() if line.strip()]
            formatted_list = "\n".join(lines) if lines else "No local users found."
            return f"👥 *Users on `{host}`:*\n{formatted_list}"
        return f"👥 *Users on `{host}`:*\n```{output}```"

    if action == "status":
        if "USER_STATUS|" in output or "USER_STATUS:" in output:
            sep = "|" if "USER_STATUS|" in output else ":"
            parts = output.split(f"USER_STATUS{sep}", 1)[1].strip().split(sep)
            if len(parts) >= 6:
                u_name, full_name, home_dir, status_str, icon, sess_time = parts[:6]
                return (
                    f"👤 *User Status for `{u_name}` on `{host}`:*\n"
                    f"• **Full Name:** {full_name}\n"
                    f"• **Home Directory:** `{home_dir}`\n"
                    f"• **Status:** {icon} {status_str}\n"
                    f"• **Current Session:** {sess_time}"
                )
        return f"👤 *User Status for `{username}` on `{host}`:*\n```{output}```"

    if "SUCCESS:disabled:" in output:
        return f"🚫 *User `{username}` on `{host}` disabled (locked).*"

    if "SUCCESS:enabled:" in output:
        return f"✔️ *User `{username}` on `{host}` enabled (active).*"

    return f":x: Failed to {action} user `{username}` on `{host}`:\n```{output}```"
