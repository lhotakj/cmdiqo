"""Logon hours restriction command handler for Windows and Linux hosts.

Enforces, inspects, and resets allowed user login hours (e.g. M-F,08:00-20:00;Sa-Su,09:00-21:00).
On Windows, configures user SAM account restrictions via `net user <user> /time:<schedule>`.
On Linux, configures PAM logon time controls via `/etc/security/time.conf`.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("cmdiqo-bot.commands.restrict")

SAFE_SCHEDULE_PATTERN = re.compile(
    r"^(?:all|(?:(?:(?:[A-Za-z]{1,2}(?:-[A-Za-z]{1,2})?|\*),\d{1,2}(?::\d{2})?(?:am|pm)?-\d{1,2}(?::\d{2})?(?:am|pm)?)(?:;\s*)?)+)$",
    re.IGNORECASE,
)


def validate_schedule_syntax(schedule: str) -> bool:
    """Validate that the schedule string contains valid day/time specifiers without shell metacharacters."""
    return bool(schedule and SAFE_SCHEDULE_PATTERN.match(schedule))


def build_restrict_script_windows(action: str, username: str = "", schedule: str = "") -> str:
    """Generate self-contained PowerShell script payload for Windows logon hours restrictions."""
    clean_user = username.replace("'", "''")
    clean_sched = schedule.replace("'", "''")

    return f"""$ErrorActionPreference = 'Stop'
try {{
    $action = '{action}'.ToLower()
    $targetUser = '{clean_user}'
    $scheduleStr = '{clean_sched}'

    # Resolve target user(s) if not explicitly provided
    $usersToProcess = @()
    if ($targetUser) {{
        $usersToProcess = @($targetUser)
    }} else {{
        $systemAccounts = @('administrator', 'guest', 'defaultaccount', 'wdagutilityaccount')
        $allUsers = Get-LocalUser -ErrorAction SilentlyContinue | Where-Object {{
            $_.Enabled -and ($systemAccounts -notcontains $_.Name.ToLower())
        }}
        if ($allUsers) {{
            $usersToProcess = @($allUsers | ForEach-Object {{ $_.Name }})
        }}
    }}

    if (-not $usersToProcess -or $usersToProcess.Count -eq 0) {{
        Write-Output 'ERROR:No eligible local user accounts found.'
        exit 1
    }}

    if ($action -eq 'status') {{
        $results = @()
        foreach ($u in $usersToProcess) {{
            $raw = net user $u 2>$null
            $hours = 'All'
            if ($raw) {{
                foreach ($line in $raw) {{
                    if ($line -match '(Logon hours allowed|hours allowed|hodiny)\\s+(.+)$') {{
                        $hours = $matches[2].Trim()
                        break
                    }}
                }}
            }}
            $results += ('STATUS:' + $u + ':' + $hours)
        }}
        $nl = [Environment]::NewLine
        Write-Output ($results -join $nl)
        exit 0
    }}

    if ($action -eq 'set') {{
        if (-not $scheduleStr) {{
            Write-Output 'ERROR:Schedule string is required for set action.'
            exit 1
        }}
        $results = @()
        foreach ($u in $usersToProcess) {{
            $cmd = 'net user ' + $u + ' /time:' + $scheduleStr
            Invoke-Expression $cmd 2>$null
            if ($LASTEXITCODE -ne 0) {{
                Write-Output ('ERROR:Failed setting logon hours for user ' + $u + ' (error code ' + $LASTEXITCODE + ')')
                exit 1
            }}
            $results += ('SUCCESS:set:' + $u + ':' + $scheduleStr)
        }}
        $nl = [Environment]::NewLine
        Write-Output ($results -join $nl)
        exit 0
    }}

    if ($action -eq 'off') {{
        $results = @()
        foreach ($u in $usersToProcess) {{
            $cmd = 'net user ' + $u + ' /time:all'
            Invoke-Expression $cmd 2>$null
            if ($LASTEXITCODE -ne 0) {{
                Write-Output ('ERROR:Failed resetting logon hours for user ' + $u + ' (error code ' + $LASTEXITCODE + ')')
                exit 1
            }}
            $results += ('SUCCESS:off:' + $u)
        }}
        $nl = [Environment]::NewLine
        Write-Output ($results -join $nl)
        exit 0
    }}

    Write-Error ('Invalid action: ' + $action)
    exit 1
}} catch {{
    Write-Error $_
    exit 1
}}
"""


def build_restrict_script_linux(action: str, username: str = "", schedule: str = "") -> str:
    """Generate self-contained Bash script payload for Linux PAM time.conf restrictions."""
    return f"""set -e
ACTION="{action}"
TARGET_USER="{username}"
SCHEDULE="{schedule}"
TIME_CONF="/etc/security/time.conf"

if [ -z "$TARGET_USER" ]; then
    # Default to first non-system user (UID >= 1000)
    TARGET_USER=$(awk -F: '$3 >= 1000 && $3 < 60000 && $1 != "nobody" {{print $1; exit}}' /etc/passwd 2>/dev/null || echo "")
fi

if [ -z "$TARGET_USER" ]; then
    echo "ERROR:No eligible non-root user found on system."
    exit 1
fi

if [ "$ACTION" = "status" ]; then
    if [ ! -f "$TIME_CONF" ] || ! grep -q "# cmdiqo-restrict:start" "$TIME_CONF" 2>/dev/null; then
        echo "STATUS:$TARGET_USER:All"
        exit 0
    fi
    ACTIVE=$(sed -n '/# cmdiqo-restrict:start/,/# cmdiqo-restrict:end/p' "$TIME_CONF" | grep -v '^#' | head -n 1)
    if [ -n "$ACTIVE" ]; then
        echo "STATUS:$TARGET_USER:$ACTIVE"
    else
        echo "STATUS:$TARGET_USER:All"
    fi
    exit 0
fi

if [ "$ACTION" = "set" ]; then
    if [ -z "$SCHEDULE" ]; then
        echo "ERROR:Schedule string is required for set action."
        exit 1
    fi
    # Clean previous block
    if [ -f "$TIME_CONF" ]; then
        sed -i '/# cmdiqo-restrict:start/,/# cmdiqo-restrict:end/d' "$TIME_CONF" 2>/dev/null || true
    fi
    # Format PAM rule: login services; all ttys; user; schedule
    cat << EOF >> "$TIME_CONF"
# cmdiqo-restrict:start
*;*;$TARGET_USER;$SCHEDULE
# cmdiqo-restrict:end
EOF
    echo "SUCCESS:set:$TARGET_USER:$SCHEDULE"
    exit 0
fi

if [ "$ACTION" = "off" ]; then
    if [ -f "$TIME_CONF" ]; then
        sed -i '/# cmdiqo-restrict:start/,/# cmdiqo-restrict:end/d' "$TIME_CONF" 2>/dev/null || true
    fi
    echo "SUCCESS:off:$TARGET_USER"
    exit 0
fi

echo "ERROR:Invalid action: $ACTION"
exit 1
"""


async def manage_restrict(
    params: dict[str, Any],
    hub: Hub | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Handle /host {host} restrict [user] {action} requests for Windows and Linux hosts."""
    host = str(params.get("host", "")).strip()
    action = str(params.get("action", "")).lower().strip()
    user = str(params.get("user", "")).strip()
    schedule = str(params.get("schedule", "")).strip()

    if not host:
        return ":no_entry_sign: Target host is required."

    # Validate action
    if action not in ("status", "set", "off"):
        return (
            f":warning: Invalid action `{action}`.\n"
            f"*Usage:*\n"
            f"• Query status: `/host {host} restrict status` or `/host {host} restrict <user> status`\n"
            f"• Set restriction: `/host {host} restrict set <schedule>` or `/host {host} restrict <user> set <schedule>`\n"
            f"• Clear restriction: `/host {host} restrict off` or `/host {host} restrict <user> off`\n\n"
            f"*Schedule Examples:*\n"
            f"• Weekdays 8am-8pm, Weekends 8am-9pm: `M-F,08:00-20:00;Sa-Su,08:00-21:00`\n"
            f"• Weekdays after school: `M-F,16:00-20:00`"
        )

    if action == "set" and not schedule:
        return (
            ":warning: Schedule string is required when setting logon restrictions.\n"
            f"Example: `/host {host} restrict set M-F,08:00-20:00;Sa-Su,08:00-21:00`"
        )

    if action == "set" and not validate_schedule_syntax(schedule):
        return (
            f":warning: Invalid schedule format `{schedule}`.\n"
            "Only alphanumeric day names (M, T, W, Th, F, Sa, Su), numbers, and hyphens/colons/commas/semicolons are allowed.\n"
            "Example: `M-F,08:00-20:00;Sa-Su,08:00-21:00`"
        )

    # Resolve agent platform
    is_local_target = host.lower() in ("local", "localhost", "container", "self")
    is_home_unregistered = host.lower() == "home" and (
        hub is None or "home" not in getattr(hub, "agents", {})
    )

    if is_local_target or is_home_unregistered:
        return ":warning: Logon restrictions are designed for remote desktop hosts (Windows/Linux PC agents)."

    if hub is None:
        return ":warning: Hub is not running; cannot communicate with remote agents."

    agents = getattr(hub, "agents", {})
    if host not in agents:
        online = ", ".join(f"`{h}`" for h in sorted(agents.keys())) or "none"
        return f":warning: Host `{host}` is not connected. Connected agents: {online}."

    platform_str = str(agents[host].get("platform", "")).lower()
    is_windows = "win" in platform_str

    if is_windows:
        script = build_restrict_script_windows(action, username=user, schedule=schedule)
        kind = "ps1"
    else:
        script = build_restrict_script_linux(action, username=user, schedule=schedule)
        kind = "shell"

    try:
        raw_output = await hub.exec(host, kind, script, timeout=30)
    except TimeoutError:
        return f":stopwatch: Restriction command timed out on `{host}`."
    except Exception as exc:
        log.exception("Error executing restrict command on %s", host)
        return f":x: Error executing restriction command on `{host}`: `{exc}`"

    output_lines = [line.strip() for line in (raw_output or "").splitlines() if line.strip()]

    # Check for error in output
    for line in output_lines:
        if line.startswith("ERROR:"):
            return f":warning: Failed on `{host}`: {line[6:].strip()}"

    # Format responses
    if action == "status":
        status_items = []
        for line in output_lines:
            if line.startswith("STATUS:"):
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    u_name = parts[1]
                    u_hours = parts[2]
                    if u_hours.lower() == "all":
                        status_items.append(f"• User *{u_name}*: 🔓 *Unrestricted* (24/7 unlimited access)")
                    else:
                        status_items.append(f"• User *{u_name}*: 🔒 *Restricted* → `{u_hours}`")
        if status_items:
            items_formatted = "\n".join(status_items)
            return (
                f":clock3: *Logon Hours Status on `{host}`:*\n"
                f"{items_formatted}\n\n"
                f"_To update schedule: `/host {host} restrict set <schedule>`_\n"
                f"_To remove restriction: `/host {host} restrict off`_"
            )
        return f":information_source: No user restriction status reported from `{host}`."

    if action == "set":
        users_affected = []
        for line in output_lines:
            if line.startswith("SUCCESS:set:"):
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    users_affected.append(parts[2])
        users_str = ", ".join(f"*{u}*" for u in users_affected) or f"*{user}*" if user else "user"
        return (
            f":lock: *Logon hours restriction successfully applied on `{host}`:*\n"
            f"• Affected accounts: {users_str}\n"
            f"• Allowed schedule: `{schedule}`\n"
            f"• The OS will now prevent logon outside these hours.\n\n"
            f"_To remove restrictions later, run:_ `/host {host} restrict off`"
        )

    if action == "off":
        users_cleared = []
        for line in output_lines:
            if line.startswith("SUCCESS:off:"):
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    users_cleared.append(parts[2])
        users_str = ", ".join(f"*{u}*" for u in users_cleared) or f"*{user}*" if user else "user"
        return (
            f":unlock: *Logon hours restriction removed on `{host}`:*\n"
            f"• Affected accounts: {users_str}\n"
            f"• Restored to 24/7 unlimited access (`All`)."
        )

    return f":white_check_mark: Restriction command completed on `{host}`:\n```{raw_output}```"
