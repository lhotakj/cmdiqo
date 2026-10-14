"""Logon hours restriction command handler for Windows and Linux hosts.

Enforces, inspects, and resets allowed user login hours (e.g. M-F,07:00-18:00;Sa-Su,08:00-18:00).
On Windows:
  1. Configures user SAM account restrictions via `net user <user> /time:<schedule>`.
  2. Creates scheduled tasks for 5-minute toast warnings, 1-minute alert dialogues, and
     forced logoff (including wake-up execution via StartWhenAvailable).
  3. Purges all scheduled tasks and helper scripts upon restriction removal (`off` / reset).
On Linux:
  Configures PAM logon time controls via `/etc/security/time.conf`.
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


def parse_schedule_boundaries(schedule: str) -> list[dict[str, Any]]:
    """Parse schedule into discrete daily boundary definitions for Task Scheduler triggers."""
    if not schedule or schedule.lower() == "all":
        return []

    all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    boundaries: list[dict[str, Any]] = []
    # Split by semicolon
    sections = [s.strip() for s in schedule.split(";") if s.strip()]
    for section in sections:
        parts = [p.strip() for p in section.split(",") if p.strip()]
        if len(parts) < 2:
            continue
        day_spec = parts[0].lower()
        time_specs = parts[1:]

        # Resolve days
        target_days: list[str] = []
        if day_spec in ("all", "*"):
            target_days = list(all_days)
        elif "-" in day_spec:
            d_start, d_end = day_spec.split("-", 1)
            start_idx = DAY_INDEX.get(d_start, -1)
            end_idx = DAY_INDEX.get(d_end, -1)
            if start_idx >= 0 and end_idx >= 0:
                if start_idx <= end_idx:
                    target_days = all_days[start_idx : end_idx + 1]
                else:
                    target_days = all_days[start_idx:] + all_days[: end_idx + 1]
        else:
            if day_spec in DAY_INDEX:
                target_days = [all_days[DAY_INDEX[day_spec]]]

        if not target_days:
            target_days = list(all_days)

        for ts in time_specs:
            m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", ts)
            if not m:
                continue
            e_h, e_m = int(m.group(3)), int(m.group(4))
            total_min = e_h * 60 + e_m

            # T - 5 minutes
            m5 = (total_min - 5) % 1440
            t_minus_5 = f"{m5 // 60:02d}:{m5 % 60:02d}"

            # T - 1 minute
            m1 = (total_min - 1) % 1440
            t_minus_1 = f"{m1 // 60:02d}:{m1 % 60:02d}"

            end_time = f"{e_h:02d}:{e_m:02d}"

            boundaries.append({
                "days": target_days,
                "end_time": end_time,
                "warn5_time": t_minus_5,
                "warn1_time": t_minus_1,
            })

    return boundaries


def build_restrict_script_windows(action: str, username: str = "", schedule: str = "") -> str:
    """Generate self-contained PowerShell script payload for Windows logon hours restrictions.

    Enforces SAM logon limits, schedules 5-min toast notifications, 1-min alerts, and
    forced logoff at expiration time (with wake-up execution after sleep/hibernation).
    All generated PowerShell uses strictly single quotes (') to avoid command-line escaping breakage.
    """
    clean_user = username.replace("'", "''")
    clean_sched = schedule.replace("'", "''")

    boundaries = parse_schedule_boundaries(clean_sched) if action == "set" else []
    task_setup_lines: list[str] = []

    if boundaries:
        task_setup_lines.append("# Register enforcement and notification scheduled tasks")
        task_setup_lines.append("$scriptDir = 'C:\\ProgramData\\Cmdiqo\\Agent\\scripts'")
        task_setup_lines.append("if (-not (Test-Path $scriptDir)) { New-Item -ItemType Directory -Path $scriptDir -Force | Out-Null }")

        # 1. Write helper scripts using single-quoted here-strings
        task_setup_lines.append("$warn5Script = @'")
        task_setup_lines.append("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null")
        task_setup_lines.append("[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null")
        task_setup_lines.append("$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'")
        task_setup_lines.append("$xml = '<toast><visual><binding template=''ToastGeneric''><text>⏰ Logon Hours Reminder</text><text>Your allowed computer session expires in 5 minutes. Please save your open work.</text></binding></visual></toast>'")
        task_setup_lines.append("$toastXml = [Windows.Data.Xml.Dom.XmlDocument]::new()")
        task_setup_lines.append("$toastXml.LoadXml($xml)")
        task_setup_lines.append("$toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)")
        task_setup_lines.append("[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)")
        task_setup_lines.append("'@")
        task_setup_lines.append("Set-Content -Path ($scriptDir + '\\restrict-' + $targetUser + '-warn5m.ps1') -Value $warn5Script -Encoding UTF8 -Force")

        task_setup_lines.append("$warn1Script = @'")
        task_setup_lines.append("try {")
        task_setup_lines.append("    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null")
        task_setup_lines.append("    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null")
        task_setup_lines.append("    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'")
        task_setup_lines.append("    $xml = '<toast><visual><binding template=''ToastGeneric''><text>⚠️ Urgent: 1 Minute Remaining</text><text>Your session will be logged off in 1 minute due to logon hours restrictions. Save all work now!</text></binding></visual></toast>'")
        task_setup_lines.append("    $toastXml = [Windows.Data.Xml.Dom.XmlDocument]::new()")
        task_setup_lines.append("$toastXml.LoadXml($xml)")
        task_setup_lines.append("$toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)")
        task_setup_lines.append("    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)")
        task_setup_lines.append("} catch {}")
        task_setup_lines.append("try {")
        task_setup_lines.append("    Add-Type -AssemblyName System.Windows.Forms")
        task_setup_lines.append("    [System.Windows.Forms.MessageBox]::Show('Your allowed computer session will end in 1 minute. Please save all open work immediately!', 'Logon Hours Alert', 'OK', 'Warning') | Out-Null")
        task_setup_lines.append("} catch {}")
        task_setup_lines.append("'@")
        task_setup_lines.append("Set-Content -Path ($scriptDir + '\\restrict-' + $targetUser + '-warn1m.ps1') -Value $warn1Script -Encoding UTF8 -Force")

        task_setup_lines.append("$logoffScript = @'")
        task_setup_lines.append("$target = '__TARGET_USER__'.ToLower()")
        task_setup_lines.append("if (Get-Command quser.exe -ErrorAction SilentlyContinue) {")
        task_setup_lines.append("    $qLines = (& quser.exe 2>&1) | Select-Object -Skip 1")
        task_setup_lines.append("    foreach ($l in $qLines) {")
        task_setup_lines.append("        $trim = $l.Trim().TrimStart('>')")
        task_setup_lines.append("        $tokens = -split $trim")
        task_setup_lines.append("        if ($tokens.Count -ge 2 -and $tokens[0].ToLower() -eq $target) {")
        task_setup_lines.append("            for ($i = 1; $i -lt $tokens.Count; $i++) {")
        task_setup_lines.append("                if ($tokens[$i] -match '^\\d+$') {")
        task_setup_lines.append("                    & logoff.exe $tokens[$i]")
        task_setup_lines.append("                    break")
        task_setup_lines.append("                }")
        task_setup_lines.append("            }")
        task_setup_lines.append("        }")
        task_setup_lines.append("    }")
        task_setup_lines.append("}")
        task_setup_lines.append("'@")
        task_setup_lines.append("$logoffScript = $logoffScript.Replace('__TARGET_USER__', $targetUser)")
        task_setup_lines.append("Set-Content -Path ($scriptDir + '\\restrict-' + $targetUser + '-logoff.ps1') -Value $logoffScript -Encoding UTF8 -Force")

        # 2. Register scheduled tasks for each boundary
        for idx, b in enumerate(boundaries, 1):
            days_expr = "@(" + ", ".join(f"'{d}'" for d in b["days"]) + ")"
            end_t = b["end_time"]
            w5_t = b["warn5_time"]
            w1_t = b["warn1_time"]

            # Task 1: 5m Warning Toast
            task_setup_lines.append(f"$tNameWarn5 = 'Cmdiqo-Restrict-' + $targetUser + '-Warn5m-{idx}'")
            task_setup_lines.append(f"$trigWarn5 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {days_expr} -At '{w5_t}'")
            task_setup_lines.append("$actWarn5 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File ' + [char]39 + $scriptDir + '\\restrict-' + $targetUser + '-warn5m.ps1' + [char]39)")
            task_setup_lines.append("$princWarn = New-ScheduledTaskPrincipal -UserId $targetUser -LogonType Interactive")
            task_setup_lines.append("$settWarn = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries")
            task_setup_lines.append("Register-ScheduledTask -TaskName $tNameWarn5 -Trigger $trigWarn5 -Action $actWarn5 -Principal $princWarn -Settings $settWarn -Force | Out-Null")

            # Task 2: 1m Warning Toast & Alert
            task_setup_lines.append(f"$tNameWarn1 = 'Cmdiqo-Restrict-' + $targetUser + '-Warn1m-{idx}'")
            task_setup_lines.append(f"$trigWarn1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {days_expr} -At '{w1_t}'")
            task_setup_lines.append("$actWarn1 = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File ' + [char]39 + $scriptDir + '\\restrict-' + $targetUser + '-warn1m.ps1' + [char]39)")
            task_setup_lines.append("Register-ScheduledTask -TaskName $tNameWarn1 -Trigger $trigWarn1 -Action $actWarn1 -Principal $princWarn -Settings $settWarn -Force | Out-Null")

            # Task 3: Logoff (System execution, forced logoff outside allowed hours)
            task_setup_lines.append(f"$tNameLogoff = 'Cmdiqo-Restrict-' + $targetUser + '-Logoff-{idx}'")
            task_setup_lines.append(f"$trigLogoff = New-ScheduledTaskTrigger -Weekly -DaysOfWeek {days_expr} -At '{end_t}'")
            task_setup_lines.append("$actLogoff = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File ' + [char]39 + $scriptDir + '\\restrict-' + $targetUser + '-logoff.ps1' + [char]39)")
            task_setup_lines.append("$princSys = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest")
            task_setup_lines.append("$settLogoff = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries")
            task_setup_lines.append("Register-ScheduledTask -TaskName $tNameLogoff -Trigger $trigLogoff -Action $actLogoff -Principal $princSys -Settings $settLogoff -Force | Out-Null")

    task_setup_block = ("\n        " + "\n        ".join(task_setup_lines)) if task_setup_lines else ""

    return f"""$ErrorActionPreference = 'Stop'
try {{
    $action = '{action}'.ToLower()
    $targetUser = '{clean_user}'
    $scheduleStr = '{clean_sched}'

    if (-not $targetUser) {{
        Write-Output 'ERROR:Username is required for logon hours restrictions.'
        exit 1
    }}

    if ($action -eq 'status') {{
        $raw = & net.exe user $targetUser 2>&1
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
        $tasks = Get-ScheduledTask -TaskName ('Cmdiqo-Restrict-' + $targetUser + '-*') -ErrorAction SilentlyContinue
        $taskCount = if ($tasks) {{ $tasks.Count }} else {{ 0 }}
        Write-Output ('STATUS:' + $targetUser + ':' + $hoursStr + ':TASKS:' + $taskCount)
        exit 0
    }}

    if ($action -eq 'set') {{
        if (-not $scheduleStr) {{
            Write-Output 'ERROR:Schedule string is required for set action.'
            exit 1
        }}
        $out = & net.exe user $targetUser ('/time:' + $scheduleStr) 2>&1
        if ($LASTEXITCODE -ne 0) {{
            $errDetail = ($out | Out-String).Trim()
            Write-Output ('ERROR:Failed setting logon hours for user ' + $targetUser + ': ' + $errDetail)
            exit 1
        }}
        # Clear any prior scheduled tasks for this user to keep in sync
        Get-ScheduledTask -TaskName ('Cmdiqo-Restrict-' + $targetUser + '-*') -ErrorAction SilentlyContinue | ForEach-Object {{
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }}
{task_setup_block}
        Write-Output ('SUCCESS:set:' + $targetUser + ':' + $scheduleStr)
        exit 0
    }}

    if ($action -eq 'off') {{
        $out = & net.exe user $targetUser '/time:all' 2>&1
        if ($LASTEXITCODE -ne 0) {{
            $errDetail = ($out | Out-String).Trim()
            Write-Output ('ERROR:Failed resetting logon hours for user ' + $targetUser + ': ' + $errDetail)
            exit 1
        }}
        # Purge all scheduled tasks and helper scripts for this user
        Get-ScheduledTask -TaskName ('Cmdiqo-Restrict-' + $targetUser + '-*') -ErrorAction SilentlyContinue | ForEach-Object {{
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }}
        Remove-Item -Path ('C:\\ProgramData\\Cmdiqo\\Agent\\scripts\\restrict-' + $targetUser + '-*.ps1') -Force -ErrorAction SilentlyContinue
        Write-Output ('SUCCESS:off:' + $targetUser)
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
    echo "ERROR:Username is required for logon hours restrictions."
    exit 1
fi

if [ "$ACTION" = "status" ]; then
    if [ ! -f "$TIME_CONF" ] || ! grep -q "# cmdiqo-restrict:$TARGET_USER:start" "$TIME_CONF" 2>/dev/null; then
        echo "STATUS:$TARGET_USER:All:TASKS:0"
        exit 0
    fi
    ACTIVE=$(sed -n '/# cmdiqo-restrict:'$TARGET_USER':start/,/# cmdiqo-restrict:'$TARGET_USER':end/p' "$TIME_CONF" | grep -v '^#' | head -n 1)
    if [ -n "$ACTIVE" ]; then
        echo "STATUS:$TARGET_USER:$ACTIVE:TASKS:0"
    else
        echo "STATUS:$TARGET_USER:All:TASKS:0"
    fi
    exit 0
fi

if [ "$ACTION" = "set" ]; then
    if [ -z "$SCHEDULE" ]; then
        echo "ERROR:Schedule string is required for set action."
        exit 1
    fi
    # Clean previous block for this user
    if [ -f "$TIME_CONF" ]; then
        sed -i '/# cmdiqo-restrict:'$TARGET_USER':start/,/# cmdiqo-restrict:'$TARGET_USER':end/d' "$TIME_CONF" 2>/dev/null || true
    fi
    # Format PAM rule: login services; all ttys; user; schedule
    cat << EOF >> "$TIME_CONF"
# cmdiqo-restrict:$TARGET_USER:start
*;*;$TARGET_USER;$SCHEDULE
# cmdiqo-restrict:$TARGET_USER:end
EOF
    echo "SUCCESS:set:$TARGET_USER:$SCHEDULE"
    exit 0
fi

if [ "$ACTION" = "off" ]; then
    if [ -f "$TIME_CONF" ]; then
        sed -i '/# cmdiqo-restrict:'$TARGET_USER':start/,/# cmdiqo-restrict:'$TARGET_USER':end/d' "$TIME_CONF" 2>/dev/null || true
    fi
    echo "SUCCESS:off:$TARGET_USER"
    exit 0
fi

echo "ERROR:Invalid action: $ACTION"
exit 1
"""


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
    """Handle /host {host} restrict {user} {action} requests for Windows and Linux hosts."""
    host = str(params.get("host", "")).strip()
    action = str(params.get("action", "")).lower().strip()
    user = str(params.get("user", "")).strip()
    schedule = str(params.get("schedule", "")).strip()

    # 1. Host validation
    if not host:
        return ":no_entry_sign: Target host is required."

    # 2. Action normalization
    if action in ("disable", "clear", "reset"):
        action = "off"

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
            elif "status" in spec_name or "status" in spec_trig:
                action = "status"

    if action in ("disable", "clear", "reset"):
        action = "off"

    # 3. Mandatory User Validation
    if not user:
        return (
            ":warning: Username is required for logon hours restrictions.\n\n"
            "*Usage:*\n"
            f"• Set allowed schedule: `/host {host} restrict <user> set <schedule>`\n"
            f"• Query status: `/host {host} restrict <user> status` (or `/host {host} restrict <user>`)\n"
            f"• Clear restriction: `/host {host} restrict <user> off`\n\n"
            "*Schedule Examples:*\n"
            f"• Weekdays 7am-6pm, Weekends 8am-6pm: `/host {host} restrict jonas set M-F,07:00-18:00;Sa-Su,08:00-18:00`\n"
            f"• Weekdays after school: `/host {host} restrict jonas set M-F,16:00-20:00`\n"
            f"• Query user status: `/host {host} restrict jonas status`\n"
            f"• Remove limits: `/host {host} restrict jonas off`"
        )

    if not re.match(r"^[a-zA-Z0-9_\-\. ]+$", user):
        return f":warning: Invalid username `{user}`. Please use letters, numbers, hyphens, and underscores."

    # 4. Action Validation
    if action not in ("status", "set", "off"):
        return (
            f":warning: Invalid action `{action}`.\n\n"
            f"*Usage:*\n"
            f"• Set allowed schedule: `/host {host} restrict {user} set <schedule>`\n"
            f"• Query status: `/host {host} restrict {user} status` (or `/host {host} restrict {user}`)\n"
            f"• Clear restriction: `/host {host} restrict {user} off`\n\n"
            f"*Schedule Examples:*\n"
            f"• Weekdays 7am-6pm, Weekends 8am-6pm: `M-F,07:00-18:00;Sa-Su,08:00-18:00`\n"
            f"• Weekdays after school: `M-F,16:00-20:00`"
        )

    if action == "set":
        schedule = normalize_schedule(schedule)

    if action == "set" and not schedule:
        return (
            ":warning: Schedule string is required when setting logon restrictions.\n"
            f"Example: `/host {host} restrict {user} set M-F,07:00-18:00;Sa-Su,08:00-18:00`"
        )

    if action == "set" and not validate_schedule_syntax(schedule):
        return (
            f":warning: Invalid schedule format `{schedule}`.\n"
            "Only alphanumeric day names (M, T, W, Th, F, Sa, Su), numbers, and hyphens/colons/commas/semicolons are allowed.\n"
            f"Example: `/host {host} restrict {user} set M-F,07:00-18:00;Sa-Su,08:00-18:00`"
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
                    raw_val = parts[2]
                    tasks_info = ""
                    if ":TASKS:" in raw_val:
                        u_hours, task_str = raw_val.split(":TASKS:", 1)
                        task_cnt = int(task_str) if task_str.isdigit() else 0
                        if task_cnt > 0:
                            tasks_info = f"\n  _Active enforcement:_ 5-min toast warning, 1-min alert, and forced logoff ({task_cnt} scheduled tasks active)."
                    else:
                        u_hours = raw_val

                    if u_hours.lower() == "all":
                        status_items.append(f"• User *{u_name}*: 🔓 *Unrestricted* (24/7 unlimited access)")
                    else:
                        condensed = format_condensed_logon_hours(u_hours)
                        status_items.append(f"• User *{u_name}*: 🔒 *Restricted* → `{condensed}`{tasks_info}")
        if status_items:
            items_formatted = "\n".join(status_items)
            return (
                f":clock3: *Logon Hours Status on `{host}`:*\n"
                f"{items_formatted}\n\n"
                f"_To update schedule: `/host {host} restrict {user} set <schedule>`_\n"
                f"_To remove restriction: `/host {host} restrict {user} off`_"
            )
        return f":information_source: No user restriction status reported from `{host}`."

    if action == "set":
        notes_str = ("\n" + "\n".join(f"• _{n}_" for n in norm_notes)) if norm_notes else ""
        return (
            f":lock: *Logon hours restriction successfully applied on `{host}`:*\n"
            f"• Affected account: *{user}*\n"
            f"• Allowed schedule: `{schedule}`{notes_str}\n"
            f"• *Active enforcement:* 5-min toast reminder, 1-min toast & alert, and forced logoff at end of allowed time (including wake-up after sleep/hibernation).\n\n"
            f"_To remove restrictions later, run:_ `/host {host} restrict {user} off`"
        )

    if action == "off":
        return (
            f":unlock: *Logon hours restriction removed on `{host}`:*\n"
            f"• Affected account: *{user}*\n"
            f"• Restored to 24/7 unlimited access (`All`).\n"
            f"• All scheduled warning and logoff enforcement tasks removed."
        )

    return f":white_check_mark: Restriction command completed on `{host}`:\n```{raw_output}```"
