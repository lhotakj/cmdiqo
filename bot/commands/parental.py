"""Cross-platform grouped parental domain blocking."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.hub import Hub

log = logging.getLogger("cmdiqo-bot.commands.parental")

PARENTAL_GROUPS: dict[str, list[str]] = {
    "youtube": ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtubekids.com", "www.youtubekids.com", "youtube-nocookie.com", "www.youtube-nocookie.com", "ytimg.com", "s.ytimg.com", "i.ytimg.com", "yt3.ggpht.com", "youtube.googleapis.com", "youtubei.googleapis.com"],
    "roblox": ["roblox.com", "www.roblox.com", "api.roblox.com", "web.roblox.com", "assetgame.roblox.com", "setup.roblox.com", "rbxcdn.com", "www.rbxcdn.com", "robloxlabs.com"],
    "minecraft": ["minecraft.net", "www.minecraft.net", "mojang.com", "www.mojang.com", "mojangapi.com", "minecraftservices.com", "api.minecraftservices.com", "sessionserver.mojang.com", "authserver.mojang.com", "textures.minecraft.net", "launchermeta.mojang.com", "launchercontent.mojang.com", "libraries.minecraft.net", "resources.download.minecraft.net", "dl.mojang.com", "piston-meta.mojang.com", "piston-data.mojang.com", "login.live.com", "login.microsoftonline.com", "xboxlive.com", "www.xboxlive.com", "user.auth.xboxlive.com", "xsts.auth.xboxlive.com", "profile.xboxlive.com", "realms.minecraft.net", "gamedata.minecraft.net", "events.data.microsoft.com", "vortex.data.microsoft.com", "playfabapi.com", "multiplayer.minecraft.net", "clientconfig.minecraft.net", "geo-prod.do.dsp.mp.microsoft.com", "packages.microsoft.com"],
    "discord": ["discord.com", "www.discord.com", "discord.gg", "discordapp.com", "www.discordapp.com", "discord.app", "discord.media", "discord.net", "discordcdn.com", "cdn.discordapp.com", "media.discordapp.net", "router.discord.gg", "gateway.discord.gg", "status.discord.com"],
    "tiktok": ["tiktok.com", "www.tiktok.com", "tiktokcdn.com", "tiktokv.com"],
    "twitch": ["twitch.tv", "www.twitch.tv", "ttvnw.net", "jtvnw.net"],
    "instagram": ["instagram.com", "www.instagram.com", "cdninstagram.com"],
    "reddit": ["reddit.com", "www.reddit.com", "redd.it"],
    "ai": ["character.ai", "beta.character.ai"],
    "web-games": ["now.gg", "www.now.gg", "poki.com", "crazygames.com", "coolmathgames.com"],
    "proxies": ["croxyproxy.com", "proxysite.com", "blockaway.net"],
}
PARENTAL_DOMAINS: list[str] = sorted({domain for domains in PARENTAL_GROUPS.values() for domain in domains})
ALL_DOMAINS = PARENTAL_DOMAINS
YOUTUBE_DOMAINS = PARENTAL_GROUPS["youtube"]
ROBLOX_DOMAINS = PARENTAL_GROUPS["roblox"]
MINECRAFT_DOMAINS = PARENTAL_GROUPS["minecraft"]
DISCORD_DOMAINS = PARENTAL_GROUPS["discord"]
TIKTOK_DOMAINS = PARENTAL_GROUPS["tiktok"]
TWITCH_DOMAINS = PARENTAL_GROUPS["twitch"]
INSTAGRAM_DOMAINS = PARENTAL_GROUPS["instagram"]
REDDIT_DOMAINS = PARENTAL_GROUPS["reddit"]
AI_DOMAINS = PARENTAL_GROUPS["ai"]
GAMING_BYPASS_DOMAINS = PARENTAL_GROUPS["web-games"]
PROXY_DOMAINS = PARENTAL_GROUPS["proxies"]


def _normalise_groups(config: dict[str, Any] | None) -> dict[str, list[str]]:
    """Return trusted configured groups, retaining legacy flat-domain configuration."""
    candidate = config.get("groups") if isinstance(config, dict) else None
    if isinstance(candidate, Mapping):
        groups = {
            str(name).strip().lower(): [str(domain).strip() for domain in domains if str(domain).strip()]
            for name, domains in candidate.items()
            if isinstance(domains, list) and str(name).strip()
        }
        if groups:
            return groups
    domains = config.get("domains") if isinstance(config, dict) else None
    if isinstance(domains, list) and domains:
        configured = [str(domain).strip() for domain in domains if str(domain).strip()]
        if set(configured) == set(PARENTAL_DOMAINS):
            return {name: list(group_domains) for name, group_domains in PARENTAL_GROUPS.items()}
        return {"all": configured}
    return {name: list(domains) for name, domains in PARENTAL_GROUPS.items()}


def _powershell_groups(groups: Mapping[str, list[str]]) -> str:
    rows = []
    for name, domains in groups.items():
        quoted = ", ".join(f"'{domain}'" for domain in domains)
        rows.append(f"'{name}' = @({quoted})")
    return "; ".join(rows)


def build_parental_script_windows(
    action: str, groups: Mapping[str, list[str]] | list[str] | None = None, group: str = "all"
) -> str:
    """Generate a PowerShell payload that changes only the requested group."""
    resolved = {"all": groups} if isinstance(groups, list) else (groups or PARENTAL_GROUPS)
    group_names = list(resolved)
    selected = group_names if group == "all" else [group]
    return f"""$ErrorActionPreference = 'Stop'
try {{
    $action = '{action}'
    $selected = @({', '.join(f"'{name}'" for name in selected)})
    $allGroups = @({', '.join(f"'{name}'" for name in group_names)})
    $domainsByGroup = @{{{_powershell_groups(resolved)}}}
    $hostsPath = Join-Path $env:SystemRoot 'System32\\drivers\\etc\\hosts'
    $content = if (Test-Path $hostsPath) {{ [IO.File]::ReadAllText($hostsPath) }} else {{ '' }}
    $legacy = [regex]::Match($content, '(?s)# parental:start(.*?)# parental:end\\r?\\n?')
    if ($legacy.Success) {{
        $active = @([regex]::Matches($legacy.Groups[1].Value, '(?m)^\\s*0\\.0\\.0\\.0\\s+')).Count
        $off = @([regex]::Matches($legacy.Groups[1].Value, '(?m)^\\s*#\\s*0\\.0\\.0\\.0\\s+')).Count
        if ($active -gt 0 -and $off -gt 0) {{ throw 'Cannot migrate a partially enabled legacy parental block.' }}
        $legacyAction = if ($active -gt 0) {{ 'on' }} else {{ 'off' }}
        $content = $content -replace '(?s)# parental:start.*?# parental:end\\r?\\n?', ''
        foreach ($legacyGroup in $allGroups) {{
            $lines = @('# parental:' + $legacyGroup + ':start')
            foreach ($domain in $domainsByGroup[$legacyGroup]) {{ $lines += $(if ($legacyAction -eq 'on') {{ '0.0.0.0 ' + $domain }} else {{ '# 0.0.0.0 ' + $domain }}) }}
            $lines += '# parental:' + $legacyGroup + ':end'
            $content = $content.TrimEnd() + [Environment]::NewLine + ($lines -join [Environment]::NewLine) + [Environment]::NewLine
        }}
    }}
    if ($action -eq 'status') {{
        foreach ($current in $selected) {{
            $match = [regex]::Match($content, '(?s)# parental:' + [regex]::Escape($current) + ':start(.*?)# parental:' + [regex]::Escape($current) + ':end')
            if (-not $match.Success) {{ Write-Output ('STATUS:' + $current + ':NOT_CONFIGURED:0'); continue }}
            $active = @([regex]::Matches($match.Groups[1].Value, '(?m)^\\s*0\\.0\\.0\\.0\\s+')).Count
            $off = @([regex]::Matches($match.Groups[1].Value, '(?m)^\\s*#\\s*0\\.0\\.0\\.0\\s+')).Count
            $state = if ($active -gt 0 -and $off -eq 0) {{ 'ON' }} elseif ($off -gt 0 -and $active -eq 0) {{ 'OFF' }} else {{ 'PARTIAL' }}
            Write-Output ('STATUS:' + $current + ':' + $state + ':' + ($active + $off))
        }}
        exit 0
    }}
    foreach ($current in $selected) {{
        $escaped = [regex]::Escape($current)
        $content = $content -replace ('(?s)# parental:' + $escaped + ':start.*?# parental:' + $escaped + ':end\\r?\\n?'), ''
        $lines = @('# parental:' + $current + ':start')
        foreach ($domain in $domainsByGroup[$current]) {{ $lines += $(if ($action -eq 'on') {{ '0.0.0.0 ' + $domain }} else {{ '# 0.0.0.0 ' + $domain }}) }}
        $lines += '# parental:' + $current + ':end'
        $content = $content.TrimEnd() + [Environment]::NewLine + ($lines -join [Environment]::NewLine) + [Environment]::NewLine
    }}
    [IO.File]::WriteAllText($hostsPath, $content, (New-Object System.Text.UTF8Encoding($false)))
    Clear-DnsClientCache -ErrorAction SilentlyContinue
    & ipconfig /flushdns | Out-Null
    Write-Output ('SUCCESS:' + $action + ':{group}')
}} catch {{ Write-Error $_; exit 1 }}
"""


build_parental_script = build_parental_script_windows


def build_parental_script_linux(
    action: str, groups: Mapping[str, list[str]] | list[str] | None = None, group: str = "all"
) -> str:
    """Generate a Bash payload that changes only the requested group."""
    resolved = {"all": groups} if isinstance(groups, list) else (groups or PARENTAL_GROUPS)
    selected = list(resolved) if group == "all" else [group]
    cases = "\n".join(
        f"    {name}) cat <<'EOF'\n" + "\n".join(domains) + "\nEOF\n      ;;"
        for name, domains in resolved.items()
    )
    return f"""set -e
HOSTS_FILE='/etc/hosts'
ACTION='{action}'
SELECTED='{ ' '.join(selected) }'
ALL_GROUPS='{ ' '.join(resolved) }'
domains_for_group() {{
  case "$1" in
{cases}
  esac
}}
replace_group() {{
  current="$1"; state="$2"
  sed -i "\\|# parental:$current:start|,|# parental:$current:end|d" "$HOSTS_FILE"
  {{ echo "# parental:$current:start"; if [ "$state" = 'on' ]; then domains_for_group "$current" | sed 's/^/0.0.0.0 /'; else domains_for_group "$current" | sed 's/^/# 0.0.0.0 /'; fi; echo "# parental:$current:end"; }} >> "$HOSTS_FILE"
}}
if grep -q '^# parental:start' "$HOSTS_FILE"; then
  active=$(sed -n '/# parental:start/,/# parental:end/p' "$HOSTS_FILE" | grep -Ec '^[[:space:]]*0\\.0\\.0\\.0[[:space:]]+' || true)
  off=$(sed -n '/# parental:start/,/# parental:end/p' "$HOSTS_FILE" | grep -Ec '^[[:space:]]*#[[:space:]]*0\\.0\\.0\\.0[[:space:]]+' || true)
  if [ "$active" -gt 0 ] && [ "$off" -gt 0 ]; then echo 'ERROR: Cannot migrate a partially enabled legacy parental block.' >&2; exit 1; fi
  legacy_action=off; [ "$active" -gt 0 ] && legacy_action=on
  sed -i '/# parental:start/,/# parental:end/d' "$HOSTS_FILE"
  for current in $ALL_GROUPS; do replace_group "$current" "$legacy_action"; done
fi
if [ "$ACTION" = 'status' ]; then
  for current in $SELECTED; do
    block=$(sed -n "/# parental:$current:start/,/# parental:$current:end/p" "$HOSTS_FILE")
    if [ -z "$block" ]; then echo "STATUS:$current:NOT_CONFIGURED:0"; continue; fi
    active=$(printf '%s\\n' "$block" | grep -Ec '^[[:space:]]*0\\.0\\.0\\.0[[:space:]]+' || true)
    off=$(printf '%s\\n' "$block" | grep -Ec '^[[:space:]]*#[[:space:]]*0\\.0\\.0\\.0[[:space:]]+' || true)
    state=PARTIAL; [ "$active" -gt 0 ] && [ "$off" -eq 0 ] && state=ON; [ "$off" -gt 0 ] && [ "$active" -eq 0 ] && state=OFF
    echo "STATUS:$current:$state:$((active + off))"
  done
  exit 0
fi
for current in $SELECTED; do replace_group "$current" "$ACTION"; done
systemd-resolve --flush-caches 2>/dev/null || resolvectl flush-caches 2>/dev/null || true
echo 'SUCCESS:{action}:{group}'
"""


async def _run_local_bash(script: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec("bash", "-c", script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        output = (stdout or b"").decode("utf-8", errors="replace").strip()
        error = (stderr or b"").decode("utf-8", errors="replace").strip()
        return output if proc.returncode == 0 else f"ERROR: {error or output or f'exit code {proc.returncode}'}"
    except FileNotFoundError:
        return "ERROR: bash not found on local host"
    except (OSError, RuntimeError) as exc:
        return f"ERROR: {exc}"


async def manage_parental(params: dict[str, Any], hub: Hub | None = None, config: dict[str, Any] | None = None) -> str:
    """Manage a selected parental domain group on a local or remote host."""
    host = str(params.get("host", "")).strip()
    action = str(params.get("action", "")).lower().strip() or "status"
    group = str(params.get("group", "all")).lower().strip() or "all"
    if not host:
        return ":no_entry_sign: Target host is required."
    if action in ("enable", "block"):
        action = "on"
    elif action in ("disable", "unblock"):
        action = "off"
    if action not in ("on", "off", "status"):
        return f":warning: Invalid action `{action}`. Usage: `/host {host} parental <group|all> <on|off|status>`"
    groups = _normalise_groups(config or (params.get("_config") if isinstance(params.get("_config"), dict) else None))
    if group != "all" and group not in groups:
        return f":warning: Unknown parental group `{group}`. Available groups: {', '.join(['all', *groups])}."
    is_local = host.lower() in ("local", "localhost", "container", "self") or (host.lower() == "home" and (hub is None or "home" not in getattr(hub, "agents", {})))
    if is_local:
        raw_output = await _run_local_bash(build_parental_script_linux(action, groups, group))
        target = "Linux (local /etc/hosts)"
    else:
        if hub is None:
            return ":no_entry_sign: Hub is disabled. Cannot communicate with remote agents."
        if host not in hub.agents:
            connected = ", ".join(sorted(hub.agents)) or "(none)"
            return f":no_entry_sign: Agent `{host}` is not connected. Connected agents: {connected}"
        is_windows = "win" in str(hub.agents[host].get("platform", "")).lower()
        kind = "ps1" if is_windows else "shell"
        script = build_parental_script_windows(action, groups, group) if is_windows else build_parental_script_linux(action, groups, group)
        target = f"{'Windows' if is_windows else 'Linux'} (`{host}` {'hosts' if is_windows else '/etc/hosts'})"
        try:
            raw_output = await hub.exec(host, kind=kind, cmd=script, timeout=60)
        except (ConnectionError, OSError, RuntimeError, TimeoutError) as exc:
            log.exception("Remote parental command failed on agent '%s'", host)
            return f":x: Failed to execute parental command on `{host}`: {exc}"
    output = (raw_output or "").strip()
    label = "all groups" if group == "all" else f"`{group}`"
    if action == "status":
        return f":shield: *Parental Controls on `{host}`:*\n• **Target:** {target}\n```{output or 'No parental groups configured.'}```"
    if f"SUCCESS:{action}:{group}" in output:
        verb = "ENABLED" if action == "on" else "DISABLED"
        return f":white_check_mark: *Parental controls {verb} on `{host}` for {label}.*\n• **Target:** {target}\n• **DNS:** Resolver cache flushed successfully."
    return f":warning: Parental control completed with output from `{host}`:\n```{output}```"
