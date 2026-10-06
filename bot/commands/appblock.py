"""Application execution blocking command handler: manage game and app execution blocks on remote agents (Windows & Linux)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("cmdiqo-bot.commands.appblock")

DEFAULT_APPS: dict[str, dict[str, Any]] = {
    "roblox": {
        "name": "Roblox",
        "description": "Roblox Player, Studio, Bloxstrap & Linux wrappers",
        "windows": {
            "executables": [
                "RobloxPlayerBeta.exe",
                "RobloxPlayerLauncher.exe",
                "RobloxStudioBeta.exe",
                "Bloxstrap.exe",
                "Fishstrap.exe",
            ],
            "appx_packages": [
                "ROBLOXCORPORATION.ROBLOX",
            ],
        },
        "linux": {
            "flatpaks": [
                "org.sober.Sober",
                "org.vinegarhq.Vinegar",
            ],
            "process_names": [
                "sober",
                "vinegar",
                "grapejuice",
                "RobloxPlayerBeta.exe",
            ],
        },
    },
    "steam": {
        "name": "Steam",
        "description": "Steam Client & Launchers",
        "windows": {
            "executables": [
                "steam.exe",
                "steamservice.exe",
                "steamwebhelper.exe",
            ],
        },
        "linux": {
            "flatpaks": [
                "com.valvesoftware.Steam",
            ],
            "process_names": [
                "steam",
                "steamwebhelper",
            ],
        },
    },
    "epic": {
        "name": "Epic Games Launcher",
        "description": "Epic Games Store & Launcher",
        "windows": {
            "executables": [
                "EpicGamesLauncher.exe",
                "UnrealVersionSelector.exe",
            ],
        },
        "linux": {
            "flatpaks": [
                "com.heroicgameslauncher.hgl",
                "com.usebottles.bottles",
            ],
            "process_names": [
                "heroic",
                "bottles",
            ],
        },
    },
    "discord": {
        "name": "Discord",
        "description": "Discord Desktop Client",
        "windows": {
            "executables": [
                "Discord.exe",
                "Update.exe",
            ],
        },
        "linux": {
            "flatpaks": [
                "com.discordapp.Discord",
                "dev.vencord.Vesktop",
            ],
            "process_names": [
                "Discord",
                "vesktop",
            ],
        },
    },
    "minecraft": {
        "name": "Minecraft",
        "description": "Minecraft Java & Bedrock Launchers",
        "windows": {
            "executables": [
                "Minecraft.exe",
                "MinecraftLauncher.exe",
                "PrismLauncher.exe",
                "CurseForge.exe",
            ],
            "appx_packages": [
                "Microsoft.4297207D2250C",
            ],
        },
        "linux": {
            "flatpaks": [
                "com.mojang.Minecraft",
                "org.prismlauncher.PrismLauncher",
            ],
            "process_names": [
                "minecraft-launcher",
                "prismlauncher",
            ],
        },
    },
}


def build_appblock_script_windows(action: str, target_apps: dict[str, dict[str, Any]]) -> str:
    """Generate PowerShell script payload to manage app execution blocks on Windows via IFEO and AppX."""
    apps_json = json.dumps(target_apps)

    return f"""$ErrorActionPreference = "SilentlyContinue"
try {{
    $action = '{action}'.ToLower()
    $appsJson = @'
{apps_json}
'@
    $apps = $appsJson | ConvertFrom-Json

    $ifeoBase = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options"

    if ($action -eq "status") {{
        foreach ($prop in $apps.PSObject.Properties) {{
            $appKey = $prop.Name
            $appDef = $prop.Value
            $isBlocked = $false

            if ($appDef.windows -and $appDef.windows.executables) {{
                foreach ($exe in $appDef.windows.executables) {{
                    $keyPath = Join-Path $ifeoBase $exe
                    if (Test-Path $keyPath) {{
                        $dbg = (Get-ItemProperty -Path $keyPath -Name "Debugger" -ErrorAction SilentlyContinue).Debugger
                        if ($dbg) {{
                            $isBlocked = $true
                            break
                        }}
                    }}
                }}
            }}

            if (-not $isBlocked -and $appDef.windows -and $appDef.windows.appx_packages) {{
                foreach ($pkg in $appDef.windows.appx_packages) {{
                    $found = Get-AppxPackage -AllUsers "*$pkg*" -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($found -and $found.Status -and ($found.Status -like "*Blocked*" -or $found.Status -like "*Disabled*")) {{
                        $isBlocked = $true
                        break
                    }}
                }}
            }}

            if ($isBlocked) {{
                Write-Output "STATUS:$appKey:BLOCKED"
            }} else {{
                Write-Output "STATUS:$appKey:ALLOWED"
            }}
        }}
        exit 0
    }}

    foreach ($prop in $apps.PSObject.Properties) {{
        $appKey = $prop.Name
        $appDef = $prop.Value

        if ($action -eq "on") {{
            # 1. Set IFEO Debugger trap
            if ($appDef.windows -and $appDef.windows.executables) {{
                foreach ($exe in $appDef.windows.executables) {{
                    $keyPath = Join-Path $ifeoBase $exe
                    if (-not (Test-Path $keyPath)) {{
                        New-Item -Path $keyPath -Force | Out-Null
                    }}
                    Set-ItemProperty -Path $keyPath -Name "Debugger" -Value "systray.exe" -Force | Out-Null
                }}
            }}

            # 2. Block AppX packages if configured
            if ($appDef.windows -and $appDef.windows.appx_packages) {{
                foreach ($pkg in $appDef.windows.appx_packages) {{
                    Get-AppxPackage -AllUsers "*$pkg*" -ErrorAction SilentlyContinue | Set-AppxPackage -Status Blocked -ErrorAction SilentlyContinue | Out-Null
                }}
            }}

            # 3. Terminate running processes for blocked executables
            if ($appDef.windows -and $appDef.windows.executables) {{
                foreach ($exe in $appDef.windows.executables) {{
                    $procName = [System.IO.Path]::GetFileNameWithoutExtension($exe)
                    Stop-Process -Name $procName -Force -ErrorAction SilentlyContinue
                }}
            }}

            Write-Output "SUCCESS:on:$appKey"
        }} elseif ($action -eq "off") {{
            # 1. Remove IFEO Debugger trap
            if ($appDef.windows -and $appDef.windows.executables) {{
                foreach ($exe in $appDef.windows.executables) {{
                    $keyPath = Join-Path $ifeoBase $exe
                    if (Test-Path $keyPath) {{
                        Remove-Item -Path $keyPath -Recurse -Force -ErrorAction SilentlyContinue | Out-Null
                    }}
                }}
            }}

            # 2. Unblock AppX packages if configured
            if ($appDef.windows -and $appDef.windows.appx_packages) {{
                foreach ($pkg in $appDef.windows.appx_packages) {{
                    Get-AppxPackage -AllUsers "*$pkg*" -ErrorAction SilentlyContinue | Set-AppxPackage -Status Normal -ErrorAction SilentlyContinue | Out-Null
                }}
            }}

            Write-Output "SUCCESS:off:$appKey"
        }}
    }}
    exit 0
}} catch {{
    Write-Error $_
    exit 1
}}
"""


def build_appblock_script_linux(action: str, target_apps: dict[str, dict[str, Any]]) -> str:
    """Generate Bash script payload to manage app execution blocks on Linux via Flatpak overrides and process signals."""
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -e",
        f'ACTION="{action.lower()}"',
        "",
    ]

    if action == "status":
        for app_key, app_def in target_apps.items():
            linux_def = app_def.get("linux") or {}
            flatpaks = linux_def.get("flatpaks") or []
            flatpaks_str = " ".join(f'"{fp}"' for fp in flatpaks)

            lines.append(f"# App status for {app_key}")
            lines.append("IS_BLOCKED=0")
            lines.append(f"FLATPAKS=({flatpaks_str})")
            lines.append('for fp in "${FLATPAKS[@]}"; do')
            lines.append('  if [ -n "$fp" ]; then')
            lines.append('    if [ -f "/var/lib/flatpak/overrides/$fp" ]; then')
            lines.append('      if grep -q "nosocket=wayland\\|unshare=network\\|nosocket=x11" "/var/lib/flatpak/overrides/$fp" 2>/dev/null; then')
            lines.append('        IS_BLOCKED=1')
            lines.append('        break')
            lines.append('      fi')
            lines.append('    elif command -v flatpak >/dev/null 2>&1; then')
            lines.append('      if flatpak override --system --show "$fp" 2>/dev/null | grep -q "nosocket=wayland\\|unshare=network\\|nosocket=x11"; then')
            lines.append('        IS_BLOCKED=1')
            lines.append('        break')
            lines.append('      fi')
            lines.append('    fi')
            lines.append('  fi')
            lines.append('done')
            lines.append('if [ "$IS_BLOCKED" -eq 1 ]; then')
            lines.append(f'  echo "STATUS:{app_key}:BLOCKED"')
            lines.append('else')
            lines.append(f'  echo "STATUS:{app_key}:ALLOWED"')
            lines.append('fi')
            lines.append("")
        return "\n".join(lines)

    for app_key, app_def in target_apps.items():
        linux_def = app_def.get("linux") or {}
        flatpaks = linux_def.get("flatpaks") or []
        procs = linux_def.get("process_names") or []

        flatpaks_str = " ".join(f'"{fp}"' for fp in flatpaks)
        procs_str = " ".join(f'"{p}"' for p in procs)

        lines.append(f"# Process {action} for {app_key}")
        lines.append(f'FLATPAKS=({flatpaks_str})')
        lines.append(f'PROCS=({procs_str})')

        if action == "on":
            lines.append('if command -v flatpak >/dev/null 2>&1; then')
            lines.append('  for fp in "${FLATPAKS[@]}"; do')
            lines.append('    if [ -n "$fp" ]; then')
            lines.append('      flatpak override --system --nosocket=wayland --nosocket=x11 --nosocket=fallback-x11 --unshare=network "$fp" 2>/dev/null || true')
            lines.append('    fi')
            lines.append('  done')
            lines.append('fi')
            lines.append('for p in "${PROCS[@]}"; do')
            lines.append('  if [ -n "$p" ]; then')
            lines.append('    pkill -9 -f "$p" 2>/dev/null || true')
            lines.append('  fi')
            lines.append('done')
            lines.append(f'echo "SUCCESS:on:{app_key}"')
        elif action == "off":
            lines.append('if command -v flatpak >/dev/null 2>&1; then')
            lines.append('  for fp in "${FLATPAKS[@]}"; do')
            lines.append('    if [ -n "$fp" ]; then')
            lines.append('      flatpak override --system --reset "$fp" 2>/dev/null || true')
            lines.append('    fi')
            lines.append('  done')
            lines.append('fi')
            lines.append(f'echo "SUCCESS:off:{app_key}"')
        lines.append("")

    return "\n".join(lines)


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


async def manage_appblock(
    params: dict[str, Any],
    hub: Hub | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Handle /host {host} blockapp {app} {action} requests for Windows and Linux hosts."""
    host = str(params.get("host", "")).strip()
    app_query = str(params.get("app", "")).lower().strip()
    action = str(params.get("action", "")).lower().strip()

    if not host:
        return ":no_entry_sign: Target host is required."

    if not app_query:
        return (
            f":warning: Application identifier is required.\n"
            f"Usage: `/host {host} blockapp <app|all> <on|off|status>`"
        )

    # Normalize action aliases
    if action in ("enable", "block"):
        action = "on"
    elif action in ("disable", "unblock"):
        action = "off"

    if action not in ("on", "off", "status"):
        return (
            f":warning: Invalid action `{action}`.\n"
            f"Usage: `/host {host} blockapp {app_query} on` | `/host {host} blockapp {app_query} off` | `/host {host} blockapp {app_query} status`"
        )

    # Resolve apps dictionary from config (or params._config), falling back to DEFAULT_APPS
    apps_dict: dict[str, dict[str, Any]] = {}
    if config and isinstance(config, dict) and isinstance(config.get("apps"), dict):
        apps_dict = dict(config["apps"])
    elif isinstance(params.get("_config"), dict) and isinstance(params["_config"].get("apps"), dict):
        apps_dict = dict(params["_config"]["apps"])
    if not apps_dict:
        apps_dict = dict(DEFAULT_APPS)

    # Resolve targeted apps
    target_apps: dict[str, dict[str, Any]] = {}
    if app_query in ("all", "*"):
        target_apps = apps_dict
    elif app_query in apps_dict:
        target_apps = {app_query: apps_dict[app_query]}
    else:
        available = ", ".join(f"`{k}`" for k in sorted(apps_dict))
        return (
            f":warning: Unknown application `{app_query}`.\n"
            f"Available apps: {available} or `all`."
        )

    # 1. Determine execution target and platform
    is_local_target = host.lower() in ("local", "localhost", "container", "self")
    is_home_unregistered = host.lower() == "home" and (
        hub is None or "home" not in getattr(hub, "agents", {})
    )

    if is_local_target or is_home_unregistered:
        os_label = "Linux (local)"
        cmd = build_appblock_script_linux(action, target_apps=target_apps)
        raw_output = await _run_local_bash(cmd)
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
            cmd = build_appblock_script_windows(action, target_apps=target_apps)
            os_label = f"Windows (`{host}` IFEO/AppX)"
        else:
            kind = "shell"
            cmd = build_appblock_script_linux(action, target_apps=target_apps)
            os_label = f"Linux (`{host}` Flatpak/Process)"

        try:
            raw_output = await hub.exec(host, kind=kind, cmd=cmd, timeout=60)
        except Exception as exc:
            log.exception("Remote appblock command failed on agent '%s'", host)
            return f":x: Failed to execute appblock command on `{host}`: {exc}"

    output = (raw_output or "").strip()

    # 2. Format response based on action and output
    lines = [l.strip() for l in output.splitlines() if l.strip()]

    if action == "status":
        status_map: dict[str, str] = {}
        for line in lines:
            if line.startswith("STATUS:"):
                parts = line.split(":")
                if len(parts) >= 3:
                    status_map[parts[1]] = parts[2]

        rows: list[str] = []
        for k, app_data in target_apps.items():
            name = app_data.get("name", k.capitalize())
            st = status_map.get(k, "UNKNOWN")
            if st == "BLOCKED":
                rows.append(f"• **{name}** (`{k}`): :no_entry: `BLOCKED`")
            elif st == "ALLOWED":
                rows.append(f"• **{name}** (`{k}`): :white_check_mark: `ALLOWED`")
            else:
                rows.append(f"• **{name}** (`{k}`): :question: `{st}`")

        status_body = "\n".join(rows) if rows else output
        return (
            f":shield: *Application Execution Status on `{host}`:*\n"
            f"• **Target:** {os_label}\n"
            f"{status_body}"
        )

    if action == "on":
        blocked_names = [target_apps[k].get("name", k.capitalize()) for k in target_apps]
        names_str = ", ".join(blocked_names)
        return (
            f":white_check_mark: :no_entry: *Application execution BLOCKED on `{host}`!*\n"
            f"• **Target:** {os_label}\n"
            f"• **Blocked Apps:** {names_str}\n"
            f"• Interception rules and process termination applied."
        )

    if action == "off":
        unblocked_names = [target_apps[k].get("name", k.capitalize()) for k in target_apps]
        names_str = ", ".join(unblocked_names)
        return (
            f":white_check_mark: :unlock: *Application execution UNBLOCKED on `{host}`!*\n"
            f"• **Target:** {os_label}\n"
            f"• **Unblocked Apps:** {names_str}\n"
            f"• Clean execution state restored successfully."
        )

    return f":warning: Appblock completed with output from `{host}`:\n```{output}```"
