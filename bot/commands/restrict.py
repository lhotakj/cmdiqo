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
    r"^(?:all|(?:(?:(?:[A-Za-z]{1,2}(?:-[A-Za-z]{1,2})?|\*),\s*\d{1,2}(?::\d{2})?(?:am|pm)?-\d{1,2}(?::\d{2})?(?:am|pm)?(?:,\s*\d{1,2}(?::\d{2})?(?:am|pm)?-\d{1,2}(?::\d{2})?(?:am|pm)?)*)(?:[;,]\s*)?)+)$",
    re.IGNORECASE,
)


def normalize_schedule(schedule: str) -> str:
    """Normalize user-entered schedule into canonical semicolon-separated format.

    Handles leading/trailing whitespace, converts commas separating day-entries into semicolons
    (e.g. 'M-F,08:00-10:00, M-F,11:00-20:00' -> 'M-F,08:00-10:00;M-F,11:00-20:00'),
    and strips redundant spaces around delimiters.
    """
    clean = schedule.strip()
    # Convert commas followed by a day specifier into semicolons
    clean = re.sub(r",\s*(?=[A-Za-z]{1,2}(?:-[A-Za-z]{1,2})?,)", ";", clean)
    # Strip spaces around semicolons and commas
    clean = re.sub(r"\s*;\s*", ";", clean)
    clean = re.sub(r"\s*,\s*", ",", clean)
    return clean


def normalize_windows_schedule(schedule: str) -> tuple[str, list[str]]:
    """Normalize schedule for Windows net user /time: which requires 1-hour resolution.

    Rounds start time down to the hour and end time to full hour, generating friendly notes.
    """
    notes: list[str] = []

    def repl(m: re.Match[str]) -> str:
        s_h = int(m.group(1))
        s_m = int(m.group(2)) if m.group(2) else 0
        s_ampm = m.group(3) or ""

        e_h = int(m.group(4))
        e_m = int(m.group(5)) if m.group(5) else 0
        e_ampm = m.group(6) or ""

        if s_m != 0:
            notes.append(f"Adjusted start '{s_h:02d}:{s_m:02d}' to '{s_h:02d}:00' (Windows requires 1-hour increments)")
            s_m = 0

        if e_m != 0:
            rounded_e_h = min(24, e_h + (1 if e_m >= 30 else 0))
            notes.append(f"Adjusted end '{e_h:02d}:{e_m:02d}' to '{rounded_e_h:02d}:00' (Windows requires 1-hour increments)")
            e_h = rounded_e_h
            e_m = 0

        start_str = f"{s_h:02d}:00{s_ampm}"
        end_str = f"{e_h:02d}:00{e_ampm}"
        return f"{start_str}-{end_str}"

    pattern = re.compile(
        r"(\d{1,2})(?::(\d{2}))?([a-zA-Z]{2})?-(\d{1,2})(?::(\d{2}))?([a-zA-Z]{2})?"
    )
    result = pattern.sub(repl, schedule)
    return result, notes


def validate_schedule_syntax(schedule: str) -> bool:
    """Validate that the schedule string contains valid day/time specifiers without shell metacharacters."""
    if not schedule:
        return False
    return bool(SAFE_SCHEDULE_PATTERN.match(schedule.strip()))


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
            $raw = & net.exe user $u 2>&1
            $hoursList = @()
            $collecting = $false
            if ($raw) {{
                foreach ($line in $raw) {{
                    if ($line -match '(Logon hours allowed|hours allowed|hodiny)\\s+(.+)$') {{
                        $firstVal = $matches[2].Trim()
                        if ($firstVal.ToLower() -eq 'all' -or $firstVal.ToLower() -eq 'vše') {{
                            $hoursList = @('All')
                            break
                        }}
                        $hoursList += $firstVal
                        $collecting = $true
                        continue
                    }}
                    if ($collecting) {{
                        if ($line -match '^\\s{{10,}}(.+)$') {{
                            $nextVal = $matches[1].Trim()
                            if ($nextVal) {{
                                $hoursList += $nextVal
                            }}
                        }} else {{
                            if ($line.Trim()) {{
                                $collecting = $false
                            }}
                        }}
                    }}
                }}
            }}
            $hoursStr = 'All'
            if ($hoursList.Count -gt 0) {{
                $hoursStr = $hoursList -join '; '
            }}
            $results += ('STATUS:' + $u + ':' + $hoursStr)
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
            $out = & net.exe user $u ('/time:' + $scheduleStr) 2>&1
            if ($LASTEXITCODE -ne 0) {{
                $errDetail = ($out | Out-String).Trim()
                Write-Output ('ERROR:Failed setting logon hours for user ' + $u + ': ' + $errDetail)
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
            $out = & net.exe user $u '/time:all' 2>&1
            if ($LASTEXITCODE -ne 0) {{
                $errDetail = ($out | Out-String).Trim()
                Write-Output ('ERROR:Failed resetting logon hours for user ' + $u + ': ' + $errDetail)
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

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_ABBR = {
    "Monday": "M",
    "Tuesday": "T",
    "Wednesday": "W",
    "Thursday": "Th",
    "Friday": "F",
    "Saturday": "Sa",
    "Sunday": "Su",
    "Pondělí": "M",
    "Úterý": "T",
    "Středa": "W",
    "Čtvrtek": "Th",
    "Pátek": "F",
    "Sobota": "Sa",
    "Neděle": "Su",
}
DAY_INDEX = {
    "monday": 0,
    "m": 0,
    "pondělí": 0,
    "po": 0,
    "tuesday": 1,
    "t": 1,
    "úterý": 1,
    "út": 1,
    "wednesday": 2,
    "w": 2,
    "středa": 2,
    "st": 2,
    "thursday": 3,
    "th": 3,
    "čtvrtek": 3,
    "čt": 3,
    "friday": 4,
    "f": 4,
    "pátek": 4,
    "pá": 4,
    "saturday": 5,
    "sa": 5,
    "sobota": 5,
    "so": 5,
    "sunday": 6,
    "su": 6,
    "neděle": 6,
    "ne": 6,
}


def _format_day_indices(indices: list[int]) -> str:
    """Format sorted day indices 0..6 (Mon..Sun) into concise notation like M-F, Sa-Su, Daily."""
    if len(indices) == 7:
        return "Daily"
    ranges: list[list[int]] = []
    for idx in indices:
        if not ranges or idx != ranges[-1][-1] + 1:
            ranges.append([idx])
        else:
            ranges[-1].append(idx)
    parts: list[str] = []
    for r in ranges:
        if len(r) >= 3:
            parts.append(f"{DAY_ABBR[DAY_ORDER[r[0]]]}-{DAY_ABBR[DAY_ORDER[r[-1]]]}")
        elif len(r) == 2:
            if r == [5, 6]:
                parts.append("Sa-Su")
            else:
                parts.append(f"{DAY_ABBR[DAY_ORDER[r[0]]]},{DAY_ABBR[DAY_ORDER[r[1]]]}")
        else:
            parts.append(DAY_ABBR[DAY_ORDER[r[0]]])
    return ",".join(parts)


def format_condensed_logon_hours(raw_hours: str) -> str:
    """Condense daily logon hours into friendly groups (e.g. 'M-F: 07:00-18:00; Sa-Su: 08:00-18:00')."""
    if not raw_hours:
        return ""
    if raw_hours.lower() == "all":
        return "All"

    entries = [e.strip() for e in raw_hours.split(";") if e.strip()]
    if not entries:
        return raw_hours

    parsed: list[tuple[str, int, str]] = []
    for entry in entries:
        m = re.match(
            r"^([A-Za-z\u00C0-\u017F]+)\s+(\d{1,2}:\d{2})(?::\d{2})?\s*-\s*(\d{1,2}:\d{2})(?::\d{2})?$",
            entry,
        )
        if m:
            day_name = m.group(1)
            idx = DAY_INDEX.get(day_name.lower(), -1)
            start_t, end_t = m.group(2), m.group(3)
            time_str = f"{start_t}-{end_t}"
            parsed.append((day_name, idx, time_str))
        else:
            return raw_hours

    time_to_days: dict[str, list[tuple[int, str]]] = {}
    for day_name, idx, time_str in parsed:
        time_to_days.setdefault(time_str, []).append((idx, day_name))

    groups: list[tuple[int, str]] = []
    for time_str, day_list in time_to_days.items():
        if all(idx >= 0 for idx, _ in day_list):
            sorted_indices = sorted({idx for idx, _ in day_list})
            label = _format_day_indices(sorted_indices)
            sort_rank = sorted_indices[0]
        else:
            label = ",".join(d for _, d in day_list)
            sort_rank = 99
        groups.append((sort_rank, f"{label}: {time_str}"))

    groups.sort(key=lambda g: g[0])
    return "; ".join(g[1] for g in groups)


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

    # Normalize user mistakenly captured as action keyword
    if user.lower() in ("status", "off", "disable", "clear", "reset"):
        if not action:
            action = "off" if user.lower() in ("off", "disable", "clear", "reset") else "status"
        user = ""

    # Deduce action if not explicitly given in params
    if not action:
        if config and "action" in config:
            action = str(config["action"]).lower().strip()
        else:
            spec_name = str(params.get("_spec_name", "")).lower()
            spec_trig = str(params.get("_spec_trigger", "")).lower()
            if schedule or "set" in spec_name or "set" in spec_trig:
                action = "set"
            elif "off" in spec_name or "off" in spec_trig or "disable" in spec_name:
                action = "off"
            elif "status" in spec_name or "status" in spec_trig or host:
                action = "status"

    if action in ("disable", "clear", "reset"):
        action = "off"

    if not host:
        return ":no_entry_sign: Target host is required."

    # Validate action
    if action not in ("status", "set", "off"):
        return (
            f":warning: Invalid action `{action}`.\n"
            f"*Usage:*\n"
            f"• Query status: `/host {host} restrict status` (or `/host {host} restrict`)\n"
            f"• Set allowed schedule: `/host {host} restrict set <schedule>`\n"
            f"• Clear restriction: `/host {host} restrict off`\n\n"
            f"*Schedule Examples:*\n"
            f"• Weekdays 8am-8pm, Weekends 8am-9pm: `M-F,08:00-20:00;Sa-Su,08:00-21:00`\n"
            f"• Weekdays after school: `M-F,16:00-20:00`"
        )

    if action == "set":
        schedule = normalize_schedule(schedule)

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

    norm_notes: list[str] = []
    if is_windows and action == "set":
        schedule, norm_notes = normalize_windows_schedule(schedule)

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
                        condensed = format_condensed_logon_hours(u_hours)
                        status_items.append(f"• User *{u_name}*: 🔒 *Restricted* → `{condensed}`")
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
        users_str = ", ".join(f"*{u}*" for u in users_affected) or (f"*{user}*" if user else "user")
        notes_str = ("\n" + "\n".join(f"• _{n}_" for n in norm_notes)) if norm_notes else ""
        return (
            f":lock: *Logon hours restriction successfully applied on `{host}`:*\n"
            f"• Affected accounts: {users_str}\n"
            f"• Allowed schedule: `{schedule}`{notes_str}\n"
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
