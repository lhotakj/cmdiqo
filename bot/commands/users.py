"""User management command handlers: list users, check status, lock/unlock, and manage power user (sudo/Administrators) privileges on remote agents (Windows & Linux)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("cmdiqo-bot.commands.users")

SAFE_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
POWERUSER_ACTION_ALIASES: dict[str, str] = {
    "enable": "enable",
    "add": "enable",
    "disable": "disable",
    "remove": "disable",
    "status": "status",
}


def build_user_script_windows(action: str, username: str = "") -> str:
    """Generate PowerShell script payload for Windows user management."""
    clean_user = username.replace("'", "''")
    return f"""$ErrorActionPreference = 'Stop'
function Refresh-CmdiqoLogonUI {{
    param([bool]$EnsureUserSwitch = $false)
    try {{
        $logonProcs = Get-Process -Name 'LogonUI' -ErrorAction SilentlyContinue
        if ($logonProcs) {{
            if ($EnsureUserSwitch) {{
                try {{
                    $usPath = 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Authentication\\LogonUI\\UserSwitch'
                    if (-not (Test-Path $usPath)) {{ New-Item -Path $usPath -Force | Out-Null }}
                    Set-ItemProperty -Path $usPath -Name 'Enabled' -Value 1 -Type DWord -ErrorAction SilentlyContinue
                }} catch {{}}
            }}
            $logonProcs | Stop-Process -Force -ErrorAction Stop
            Write-Output 'LOGONUI_REFRESHED'
        }}
    }} catch {{}}
}}
try {{
    $action = '{action}'.ToLower()
    $targetUser = '{clean_user}'

    if ($action -eq 'users') {{
        $users = Get-LocalUser | Sort-Object Name
        $lines = @()
        foreach ($u in $users) {{
            $icon = if ($u.Enabled) {{ '✔️' }} else {{ '🚫' }}
            $statusStr = if ($u.Enabled) {{ 'active' }} else {{ 'locked' }}
            $lines += ($icon + ' ' + $u.Name + ' (' + $statusStr + ')')
        }}
        $nl = [Environment]::NewLine
        Write-Output ('USERS_LIST:' + $nl + ($lines -join $nl))
        exit 0
    }}

    if ($action -eq 'status') {{
        $u = Get-LocalUser -Name $targetUser -ErrorAction SilentlyContinue
        if (-not $u) {{
            Write-Output ('ERROR:User ''' + $targetUser + ''' not found.')
            exit 1
        }}
        $fullName = if ($u.FullName) {{ $u.FullName }} else {{ 'N/A' }}
        $pattern = '*\\\\' + $targetUser
        $homeDir = (Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object LocalPath -like $pattern).LocalPath
        if (-not $homeDir) {{ $homeDir = (Join-Path $env:SystemDrive ('Users\\\\' + $targetUser)) }}
        $statusStr = if ($u.Enabled) {{ 'active' }} else {{ 'locked' }}
        $icon = if ($u.Enabled) {{ '✔️' }} else {{ '🚫' }}

        $sessTime = 'Not logged in'
        try {{
            $sessions = Get-CimInstance Win32_LogonSession -ErrorAction SilentlyContinue | Where-Object LogonType -in (2,10)
            foreach ($s in $sessions) {{
                $sessId = $s.LogonId
                $sessPattern = '*' + $sessId + '*'
                $loggedOn = Get-CimInstance Win32_LoggedOnUser -ErrorAction SilentlyContinue | Where-Object {{ $_.Dependent.Antecedent -like $sessPattern }}
                $userPattern = '*Name=' + [char]34 + $targetUser + [char]34 + '*'
                if ($loggedOn -and $loggedOn.Antecedent -like $userPattern) {{
                    $diff = (Get-Date) - $s.StartTime
                    $hours = [int]$diff.TotalHours
                    $mins = $diff.Minutes
                    $secs = $diff.Seconds
                    $sessTime = ('' + $hours + 'h ' + $mins + 'm ' + $secs + 's')
                    break
                }}
            }}
        }} catch {{}}

        Write-Output ('USER_STATUS|' + $u.Name + '|' + $fullName + '|' + $homeDir + '|' + $statusStr + '|' + $icon + '|' + $sessTime)
        exit 0
    }}

    if ($action -eq 'disable') {{
        Disable-LocalUser -Name $targetUser
        Refresh-CmdiqoLogonUI -EnsureUserSwitch $false
        Write-Output ('SUCCESS:disabled:' + $targetUser)
        exit 0
    }}

    if ($action -eq 'enable') {{
        Enable-LocalUser -Name $targetUser
        try {{ Unlock-LocalUser -Name $targetUser -ErrorAction SilentlyContinue }} catch {{}}
        Refresh-CmdiqoLogonUI -EnsureUserSwitch $true
        Write-Output ('SUCCESS:enabled:' + $targetUser)
        exit 0
    }}

    Write-Error ('Invalid action: ' + $action)
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


async def _execute_user_platform_script(
    host: str,
    win_script: str,
    linux_script: str,
    hub: Hub | None,
    command_label: str = "user",
) -> tuple[str | None, str]:
    """Execute a platform-appropriate script locally or on a connected remote agent."""
    is_local_target = host.lower() in ("local", "localhost", "container", "self")
    is_home_unregistered = host.lower() == "home" and (
        hub is None or "home" not in getattr(hub, "agents", {})
    )

    if is_local_target or is_home_unregistered:
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                linux_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            raw_output = (stdout or b"").decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not raw_output:
                raw_output = (stderr or b"").decode("utf-8", errors="replace").strip()
            return None, raw_output
        except OSError as exc:
            return f":x: Failed to execute {command_label} command locally: {exc}", ""

    if hub is None:
        return ":no_entry_sign: Hub is disabled. Cannot communicate with remote agents.", ""

    if host not in hub.agents:
        connected = ", ".join(sorted(hub.agents)) or "(none)"
        return f":no_entry_sign: Agent `{host}` is not connected. Connected agents: {connected}", ""

    agent_info = hub.agents[host]
    platform_str = str(agent_info.get("platform", "")).lower()
    is_windows = "windows" in platform_str or "win" in platform_str

    kind = "ps1" if is_windows else "shell"
    cmd = win_script if is_windows else linux_script

    try:
        raw_output = await hub.exec(host, kind=kind, cmd=cmd, timeout=60)
    except Exception as exc:
        log.exception("Remote %s command failed on agent '%s'", command_label, host)
        return f":x: Failed to execute {command_label} command on `{host}`: {exc}", ""

    return None, (raw_output or "").strip()


async def manage_users(
    params: dict[str, Any],
    hub: Hub | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Handle /host {host} users and /host {host} user {user} [status|disable|enable] requests."""
    host = str(params.get("host", "")).strip()
    username = str(params.get("user", "")).strip()

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

    err_msg, output = await _execute_user_platform_script(
        host,
        build_user_script_windows(action, username),
        build_user_script_linux(action, username),
        hub,
        command_label="user",
    )
    if err_msg:
        return err_msg

    if output.startswith("ERROR:"):
        return f":x: {output[6:].strip()}"

    # Format responses
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

    refreshed_note = (
        "\n• _Lock/sign-in screen (`LogonUI.exe`) refreshed automatically._"
        if "LOGONUI_REFRESHED" in output
        else ""
    )

    if "SUCCESS:disabled:" in output:
        return f"🚫 *User `{username}` on `{host}` disabled (locked).*{refreshed_note}"

    if "SUCCESS:enabled:" in output:
        return f"✔️ *User `{username}` on `{host}` enabled (active).*{refreshed_note}"

    return f":x: Failed to {action} user `{username}` on `{host}`:\n```{output}```"


def build_poweruser_script_windows(action: str, username: str) -> str:
    """Generate PowerShell script payload to grant, revoke, or query local Administrators membership."""
    clean_user = username.replace("'", "''")
    return f"""$ErrorActionPreference = 'Stop'
try {{
    $action = '{action}'.ToLower()
    $targetUser = '{clean_user}'

    if (-not $targetUser) {{
        Write-Output 'ERROR:Username is required.'
        exit 1
    }}

    $u = Get-LocalUser -Name $targetUser -ErrorAction SilentlyContinue
    if (-not $u) {{
        Write-Output ('ERROR:User ' + [char]39 + $targetUser + [char]39 + ' not found.')
        exit 1
    }}

    $adminGroup = Get-LocalGroup -SID 'S-1-5-32-544' -ErrorAction SilentlyContinue
    if (-not $adminGroup) {{
        Write-Output 'ERROR:Local Administrators group (S-1-5-32-544) not found.'
        exit 1
    }}
    $groupName = $adminGroup.Name

    $isMember = $false
    try {{
        $members = Get-LocalGroupMember -Group $groupName -ErrorAction Stop
        foreach ($m in $members) {{
            if ($m.SID -eq $u.SID) {{
                $isMember = $true
                break
            }}
        }}
    }} catch {{
        try {{
            $adsiGroup = [ADSI]('WinNT://./' + $groupName + ',group')
            $adsiUser = 'WinNT://./' + $u.Name + ',user'
            $isMember = [bool]$adsiGroup.IsMember($adsiUser)
        }} catch {{
            Write-Output ('ERROR:Failed inspecting Administrators group membership: ' + $_.Exception.Message)
            exit 1
        }}
    }}

    if ($action -eq 'status') {{
        $state = if ($isMember) {{ 'enabled' }} else {{ 'disabled' }}
        Write-Output ('POWERUSER_STATUS:' + $state + ':' + $u.Name + ':' + $groupName)
        exit 0
    }}

    if ($action -eq 'enable') {{
        if ($isMember) {{
            Write-Output ('SUCCESS:already_enabled:' + $u.Name + ':' + $groupName)
            exit 0
        }}
        Add-LocalGroupMember -Group $groupName -Member $u.Name
        Write-Output ('SUCCESS:enabled:' + $u.Name + ':' + $groupName)
        exit 0
    }}

    if ($action -eq 'disable') {{
        if (-not $isMember) {{
            Write-Output ('SUCCESS:already_disabled:' + $u.Name + ':' + $groupName)
            exit 0
        }}
        Remove-LocalGroupMember -Group $groupName -Member $u.Name
        Write-Output ('SUCCESS:disabled:' + $u.Name + ':' + $groupName)
        exit 0
    }}

    Write-Output ('ERROR:Invalid action: ' + $action)
    exit 1
}} catch {{
    Write-Output ('ERROR:' + $_.Exception.Message)
    exit 1
}}
"""


def build_poweruser_script_linux(
    action: str,
    username: str,
    sudoers_file: str = "/etc/sudoers",
) -> str:
    """Generate Bash script payload for surgical /etc/sudoers edits validated by visudo."""
    return f"""set -e
ACTION="{action}"
TARGET_USER="{username}"
SUDOERS_FILE="{sudoers_file}"

if [ -z "$TARGET_USER" ]; then
    echo "ERROR:Username is required."
    exit 1
fi

if ! getent passwd "$TARGET_USER" >/dev/null 2>&1 && ! id "$TARGET_USER" >/dev/null 2>&1; then
    echo "ERROR:User '$TARGET_USER' not found."
    exit 1
fi

if [ ! -f "$SUDOERS_FILE" ]; then
    echo "ERROR:$SUDOERS_FILE not found."
    exit 1
fi

if [ ! -r "$SUDOERS_FILE" ]; then
    echo "ERROR:Cannot read $SUDOERS_FILE (permission denied)."
    exit 1
fi

has_sudoers_entry() {{
    awk -v u="$TARGET_USER" '
        /^[ \t]*#/ || /^[ \t]*$/ {{ next }}
        $1 == u && NF >= 2 {{ found = 1; exit }}
        END {{ exit(found ? 0 : 1) }}
    ' "$SUDOERS_FILE"
}}

report_extra_privs() {{
    groups_str=$(id -nG "$TARGET_USER" 2>/dev/null || true)
    priv_groups=""
    for g in $groups_str; do
        case "$g" in
            sudo|wheel|admin)
                if [ -z "$priv_groups" ]; then
                    priv_groups="$g"
                else
                    priv_groups="$priv_groups,$g"
                fi
                ;;
        esac
    done
    if [ -n "$priv_groups" ]; then
        echo "EXTRA_GROUPS:$priv_groups"
    fi
    if [ -d /etc/sudoers.d ]; then
        dropins=""
        for f in /etc/sudoers.d/*; do
            [ -f "$f" ] && [ -r "$f" ] || continue
            base=$(basename "$f")
            case "$base" in
                .*|*~|README) continue ;;
            esac
            if awk -v u="$TARGET_USER" '/^[ \t]*#/ || /^[ \t]*$/ {{ next }} $1 == u && NF >= 2 {{ found = 1; exit }} END {{ exit(found ? 0 : 1) }}' "$f" 2>/dev/null; then
                if [ -z "$dropins" ]; then
                    dropins="$f"
                else
                    dropins="$dropins,$f"
                fi
            fi
        done
        if [ -n "$dropins" ]; then
            echo "EXTRA_SUDOERS_D:$dropins"
        fi
    fi
}}

if [ "$ACTION" = "status" ]; then
    if has_sudoers_entry; then
        echo "POWERUSER_STATUS:enabled:$TARGET_USER:$SUDOERS_FILE"
    else
        echo "POWERUSER_STATUS:disabled:$TARGET_USER:$SUDOERS_FILE"
    fi
    report_extra_privs
    exit 0
fi

if [ "$ACTION" = "enable" ]; then
    if has_sudoers_entry; then
        echo "SUCCESS:already_enabled:$TARGET_USER:$SUDOERS_FILE"
        exit 0
    fi
    SUDOERS_DIR=$(dirname "$SUDOERS_FILE")
    TMP_SUDOERS=$(mktemp "${{SUDOERS_DIR}}/.sudoers.cmdiqo.XXXXXX")
    trap 'rm -f "$TMP_SUDOERS"' EXIT
    cp "$SUDOERS_FILE" "$TMP_SUDOERS"
    if [ -s "$TMP_SUDOERS" ] && [ -n "$(tail -c 1 "$TMP_SUDOERS" 2>/dev/null || true)" ]; then
        printf '\\n' >> "$TMP_SUDOERS"
    fi
    printf '%s ALL=(ALL:ALL) ALL\\n' "$TARGET_USER" >> "$TMP_SUDOERS"
    chown 0:0 "$TMP_SUDOERS" 2>/dev/null || true
    chmod 0440 "$TMP_SUDOERS"
    if command -v visudo >/dev/null 2>&1; then
        if ! err_msg=$(visudo -cf "$TMP_SUDOERS" 2>&1); then
            echo "ERROR:visudo validation failed; $SUDOERS_FILE left untouched: $err_msg"
            exit 1
        fi
    fi
    mv -f "$TMP_SUDOERS" "$SUDOERS_FILE"
    trap - EXIT
    echo "SUCCESS:enabled:$TARGET_USER:$SUDOERS_FILE"
    exit 0
fi

if [ "$ACTION" = "disable" ]; then
    if [ "$TARGET_USER" = "root" ]; then
        echo "ERROR:Refusing to remove sudo privileges from 'root'."
        exit 1
    fi
    if ! has_sudoers_entry; then
        echo "SUCCESS:already_disabled:$TARGET_USER:$SUDOERS_FILE"
        report_extra_privs
        exit 0
    fi
    SUDOERS_DIR=$(dirname "$SUDOERS_FILE")
    TMP_SUDOERS=$(mktemp "${{SUDOERS_DIR}}/.sudoers.cmdiqo.XXXXXX")
    trap 'rm -f "$TMP_SUDOERS"' EXIT
    awk -v u="$TARGET_USER" '
        /^[ \t]*#/ || /^[ \t]*$/ {{ print; next }}
        $1 == u && NF >= 2 {{ next }}
        {{ print }}
    ' "$SUDOERS_FILE" > "$TMP_SUDOERS"
    chown 0:0 "$TMP_SUDOERS" 2>/dev/null || true
    chmod 0440 "$TMP_SUDOERS"
    if command -v visudo >/dev/null 2>&1; then
        if ! err_msg=$(visudo -cf "$TMP_SUDOERS" 2>&1); then
            echo "ERROR:visudo validation failed; $SUDOERS_FILE left untouched: $err_msg"
            exit 1
        fi
    fi
    mv -f "$TMP_SUDOERS" "$SUDOERS_FILE"
    trap - EXIT
    echo "SUCCESS:disabled:$TARGET_USER:$SUDOERS_FILE"
    report_extra_privs
    exit 0
fi

echo "ERROR:Invalid action: $ACTION"
exit 1
"""


def _format_poweruser_extras(lines: list[str]) -> str:
    """Format optional Linux group/drop-in sudo privilege notes."""
    notes: list[str] = []
    for line in lines:
        if line.startswith("EXTRA_GROUPS:"):
            groups = line[13:].strip()
            if groups:
                notes.append(f"• _Also member of privileged group(s):_ `{groups}`")
        elif line.startswith("EXTRA_SUDOERS_D:"):
            dropins = line[16:].strip()
            if dropins:
                notes.append(f"• _Also configured in drop-in file(s):_ `{dropins}`")
    return ("\n" + "\n".join(notes)) if notes else ""


async def manage_poweruser(
    params: dict[str, Any],
    hub: Hub | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Handle /host {host} (poweruser|sudo|administrator) {user} [enable|disable|add|remove|status]."""
    del config  # Unused; kept for standard command handler signature
    host = str(params.get("host", "")).strip()
    username = str(params.get("user", "")).strip()
    raw_action = str(params.get("action", "")).lower().strip()

    if not host:
        return ":no_entry_sign: Target host is required."

    if not raw_action:
        action = "status"
    elif raw_action in POWERUSER_ACTION_ALIASES:
        action = POWERUSER_ACTION_ALIASES[raw_action]
    else:
        return (
            f":warning: Invalid privilege action `{raw_action}`.\n"
            f"Supported actions: `enable`, `disable`, `add`, `remove`, `status`\n"
            f"Supported commands:\n"
            f"• `/host {host} poweruser <user> enable|disable|add|remove|status`\n"
            f"• `/host {host} sudo <user> enable|disable|add|remove|status`\n"
            f"• `/host {host} administrator <user> enable|disable|add|remove|status`"
        )

    if not username:
        return f":no_entry_sign: Username is required for `/host {host} poweruser <user> {action}`."

    if not SAFE_USERNAME_RE.match(username):
        return f":warning: Invalid username `{username}`. Use letters, numbers, hyphens, underscores, and dots."

    err_msg, output = await _execute_user_platform_script(
        host,
        build_poweruser_script_windows(action, username),
        build_poweruser_script_linux(action, username),
        hub,
        command_label="poweruser",
    )
    if err_msg:
        return err_msg

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("ERROR:"):
            return f":x: {line[6:].strip()}"

    extras = _format_poweruser_extras(lines)

    for line in lines:
        if line.startswith("POWERUSER_STATUS:"):
            parts = line.split(":", 3)
            if len(parts) >= 4:
                _, state, u_name, target = parts
                if state == "enabled":
                    return (
                        f"🛡️ *Power user privileges for `{u_name}` on `{host}`:* ✔️ *ENABLED* (`{target}`){extras}"
                    )
                return (
                    f"🛡️ *Power user privileges for `{u_name}` on `{host}`:* 🚫 *DISABLED* (not in `{target}`){extras}"
                )

        if line.startswith("SUCCESS:already_enabled:"):
            parts = line.split(":", 3)
            u_name = parts[2] if len(parts) > 2 else username
            target = parts[3] if len(parts) > 3 else "sudoers/Administrators"
            return f"✔️ *Power user privileges already enabled for `{u_name}` on `{host}` (`{target}`).*"

        if line.startswith("SUCCESS:enabled:"):
            parts = line.split(":", 3)
            u_name = parts[2] if len(parts) > 2 else username
            target = parts[3] if len(parts) > 3 else "sudoers/Administrators"
            return f"✔️ *Power user privileges enabled for `{u_name}` on `{host}` (`{target}`).*"

        if line.startswith("SUCCESS:already_disabled:"):
            parts = line.split(":", 3)
            u_name = parts[2] if len(parts) > 2 else username
            target = parts[3] if len(parts) > 3 else "sudoers/Administrators"
            return f"🔓 *Power user privileges already disabled for `{u_name}` on `{host}` (`{target}`).*{extras}"

        if line.startswith("SUCCESS:disabled:"):
            parts = line.split(":", 3)
            u_name = parts[2] if len(parts) > 2 else username
            target = parts[3] if len(parts) > 3 else "sudoers/Administrators"
            return f"🔓 *Power user privileges disabled for `{u_name}` on `{host}` (`{target}`).*{extras}"

    return f":x: Failed to {action} power user privileges for `{username}` on `{host}`:\n```{output}```"
