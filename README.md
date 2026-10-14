# cmdiqo-bot (Cmdiqo)

Control your home network from Slack — **works behind NAT with zero open ports**.

> 📖 **Helpful Guides:**
> - **[OVERVIEW.md](OVERVIEW.md)**: Architectural overview, execution flowcharts (Mermaid), communication protocol, and logging observability.
> - **[INSTALL.md](INSTALL.md)**: Click-by-click Slack setup, secrets management, Windows/Linux background service installation, and health checks.
> - **[BUILD.md](BUILD.md)**: Beginner tutorial on GitHub Actions, GitHub Secrets, binary builds, and digital code-signing.

A small Python bot (built on [Slack Bolt](https://slack.dev/bolt-python/) with **Socket Mode**)
that turns Slack messages into commands on your local network. Think of it as a
Python, Docker-ready replacement for Hubot, built specifically for LAN management.

Example session in Slack:

You:   /lan devices list
Bot:   IP ADDRESS     MAC ADDRESS        HOST NAME
       192.168.1.1    aa:bb:cc:dd:ee:ff  router.home
       192.168.1.20   11:22:33:44:55:66  rpi.home

You:   /host home docker restart plex
Bot:   Container plex  Restarted

# Or using the configured /home alias:
You:   /home docker restart plex
Bot:   Container plex  Restarted
```

## Why Socket Mode?

With classic Slack apps you must expose an HTTPS endpoint to the internet —
impossible behind NAT without tunnels or reverse proxies. **Socket Mode** opens a
single outbound WebSocket from your container to Slack. Nothing to forward,
nothing to expose, TLS handled for you. Ideal for a home server.

## Features

- 🔌 **Socket Mode** — no public URL, no port forwarding, NAT-friendly
- 🧩 **YAML-driven commands** — add your own commands without touching bot code
- 🐚 Two command backends:
  - `shell` — run any CLI command (with captured `{placeholders}`, `cwd`, timeout)
  - `python` — call your own Python function (return a string, done)
- 📟 Built-in `arp-scan` based **LAN device discovery** with `ip neigh` fallback
- 🐳 Docker + Compose included; ready for Docker Hub via GitHub Actions
- 🔒 Allow-lists for Slack **users** and **channels**
- 🧵 Replies in threads, long output auto-split into multiple messages
- ⚡ **Super-Lightweight Native C++20 Agents & GUI/CLI Installers**:
  - **Linux Agent (x86_64 & ARM64/aarch64)**: only **~1.1 MB RAM** working set
  - **Windows Agent (x86_64)**: only **~3.3 MB RAM** working set
  - **Windows Installer GUI**: only **~5.2 MB RAM**
  - Instant cold boot (<10 ms), zero Python runtime dependency on target nodes
- 🌐 **Integrated Real-time WebGUI & Remote Control Console** — optional single-page dashboard (`WEB_PORT=8080`) styled with Obsidian Cyberpunk design:
  - **Live Connected Agents Table**: Sortable columns (`Host Name`, `Arch`, `OS`, `Uptime`), `localStorage` sort preference persistence, 32x32 WebP architecture badges (`WIN64`, `Linux64`, `ARM64`, `ARMv6`), and standardized zero-padded uptime with client-side 1Hz local refresh.
  - **Interactive Host Actions Menu**: One-click execution for System Metrics (Uptime, Memory, Disk, Top), Docker (Containers, Stats, Logs, Restart), Power (Reboot, Shutdown), Services (Status, Restart), and Parental / Access Guards.
  - **Live Terminal Modal**: In-browser command output with full Slack formatting, emoji rendering, italics/code styles, and automated failure detection with red `FAILED` badges.
  - **Server-Sent Events (SSE)**: Last 50 events live-streamed with reverse-order toggle and zero polling overhead.
- 🛡️ **Parental Controls & Access Guards**:
  - **Logon Hours Restriction (`/host <host> restrict`)**: Enforce Windows SAM allowed login hours and Linux PAM `time.conf` rules. Supports compound schedules (`M-F,07:00-18:00;Sa-Su,08:00-18:00`) with automated multi-line daily parsing and condensed status display.
  - **Parental Domain Shield (`/host <host> parental`)**: Instant DNS/hosts-file blocking for entertainment and gaming domains (`on`, `off`, `status`).
  - **Application Execution Blocking (`/host <host> blockapp`)**: Block or restore games and apps (Roblox, Minecraft, Steam, Epic, Discord, Battle.net, etc.) via Windows IFEO / AppX and Linux execution overrides.

## Repository layout

```
.
├── docker/
│   ├── Dockerfile                  # all-in-one image (python + arp-scan + docker CLI)
│   ├── docker-compose.yml          # ready-to-run compose file
│   └── docker-compose.example.yml  # standalone deployment compose template
├── requirements.txt
├── build.ps1                       # Windows agent, native setup, and bot build script
├── build.sh                        # Linux agent, native setup, and bot build script
├── agent.c/                        # native C++20 standalone agent (Windows & Linux)
├── installer.c/                    # native C++ setup application (GUI & CLI installer)
├── bot/
│   ├── app.py                      # Slack app, dispatch, auth
│   ├── hub.py                      # WebSocket hub for remote agents
│   └── commands/                   # modular command handlers
├── config/
│   └── commands.yaml               # <-- YOUR commands live here
├── agent/                          # legacy Python agent reference implementation
│   ├── agent.py                    # tiny Python agent for LAN hosts (Win/Linux)
│   ├── install-service.ps1         # Windows service installer
│   └── install-service.sh          # Linux systemd service installer
├── development/                    # build machinery, versioning, environment setup
│   ├── build_agent.py              # PyInstaller & Authenticode signer
│   ├── update_version.py           # semantic & dev hex timestamp versioning
│   ├── setup-env.ps1               # Windows venv setup
│   └── setup-direnv.sh             # Linux/macOS direnv setup
├── .github/workflows/              # CI/CD: tests, binary builds, docker publish
└── LICENSE
```

## 1. Create the Slack app (5 minutes)

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
   Name it (e.g. `lan-bot`) and pick your workspace.
2. **Enable Socket Mode**: *Settings → Socket Mode → Enable*.
   It will ask you to create an app-level token with the `connections:write` scope.
   Copy the `xapp-...` token — this is your `SLACK_APP_TOKEN`.
3. **OAuth scopes**: *OAuth & Permissions → Bot Token Scopes*, add:
   - `commands` (for slash commands)
   - `chat:write` (to post replies and agent connection notifications)
   - `channels:read` (to auto-discover public channels for agent notifications)
   - `groups:read` (to auto-discover private channels where the bot is invited)
   - `channels:join` (to auto-join public channels for notifications)
   - `channels:history` (to read plain messages in channels)
4. **Slash commands**: *Slash Commands → Create Command* (create `/help`, `/lan`, `/host`, and optional aliases `/home`, `/wol`):
   | Command | Description               | Usage hint                         |
   |---------|---------------------------|------------------------------------|
   | `/help` | Dynamic command manual    | `[category] (e.g. lan, host, docker)`|
   | `/lan`  | Manage LAN operations     | `devices list \| wake <mac> \| ping`|
   | `/host` | Host-agnostic execution   | `<hostname-or-ip> <action>`        |
   | `/home` | *(Alias)* Home server ops | `docker restart ... \| memory`     |
   | `/wol`  | *(Alias)* Wake-on-LAN boot| `<mac-or-ip>`                      |

   (With Socket Mode enabled no Request URL is needed — events arrive over the socket.)
5. **Bot events**: *Event Subscriptions → Subscribe to bot events* → add `message.channels`.
6. **Install**: *OAuth & Permissions → Install to Workspace* → allow.
   Copy the `xoxb-...` **Bot User OAuth Token** — this is your `SLACK_BOT_TOKEN`.
7. Invite the bot to a channel: `/invite @lan-bot`.

## 2. Configure

Create a `.env` file next to `docker-compose.yml`:

```bash
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token

# OPTIONAL but strongly recommended: comma-separated Slack user IDs.
# Anyone NOT listed will be rejected. Find IDs via right-click on a user
# in Slack → "Copy member ID" (enable Developer Mode in Slack settings).
# ALLOWED_USERS=U0123ABCDEF,U0456GHIJKL

# OPTIONAL: restrict to specific channels (channel IDs start with C)
# ALLOWED_CHANNELS=C0123CHANNELID

# OPTIONAL: default timeout for shell commands in seconds
# SHELL_TIMEOUT=120

# OPTIONAL: WebSocket Hub port override (default is 8765)
# export HUB_PORT=8765

# OPTIONAL: start real-time WebGUI dashboard on specified port (e.g. 8080)
# export WEB_PORT=8080
```

Edit `config/commands.yaml` to define your commands and aliases (see **Adding your own commands** below). The defaults give you:

| You type in Slack                  | Target / Platform       | What happens                                             |
|------------------------------------|-------------------------|----------------------------------------------------------|
| `/lan devices list`                | Local container         | ARP-scans the LAN, prints IP / MAC / vendor table        |
| `/lan wake <target>`               | Local container         | Sends Wake-on-LAN magic packet to boot a device (MAC/IP) |
| `/lan ping <host>`                 | Local container         | Pings a device or IP from the Hub container              |
| `/lan agents list`                 | Hub registry            | Lists connected agents, operating systems, and IPs       |
| `/lan version`                     | Local container         | Displays bot version                                     |
| `/host <host> users`               | Remote host (All OS)    | Lists all local OS users (active ✔️ or locked 🚫)         |
| `/host <host> user <user> status`  | Remote host (All OS)    | Shows user profile info, status, and active session time |
| `/host <host> user <user> disable` | Remote host (All OS)    | Locks / disables user account login                      |
| `/host <host> user <user> enable`  | Remote host (All OS)    | Unlocks / enables user account login                     |
| `/host <host> uptime`              | Remote host (Linux/Win) | System uptime & boot time                                |
| `/host <host> memory`              | Remote host (Linux/Win) | Free and used physical RAM memory                        |
| `/host <host> disk`                | Remote host (Linux/Win) | Filesystem and drive usage breakdown                     |
| `/host <host> top`                 | Remote host (Linux/Win) | Top CPU-consuming processes                              |
| `/host <host> reboot`              | Remote host (Linux/Win) | Safely reboots remote machine                            |
| `/host <host> shutdown`            | Remote host (Linux/Win) | Safely shuts down remote machine                         |
| `/host <host> services`            | Remote host (Linux/Win) | Lists active running system services                     |
| `/host <host> service status <svc>`| Remote host (Linux/Win) | Queries service status (systemd / Windows SCM)           |
| `/host <host> service restart <svc>`| Remote host (Linux/Win)| Restarts system service (systemd / Windows SCM)          |
| `/host <host> docker ps`           | Remote host (Linux)     | Lists running docker containers                          |
| `/host <host> docker logs <svc>`   | Remote host (Linux)     | Tails last 50 log lines of a docker container            |
| `/host <host> docker restart <svc>`| Remote host (Linux)     | Restarts docker compose project in `/docker/<svc>`       |
| `/host <host> docker stats`        | Remote host (Linux)     | Snapshot of container CPU, RAM, and network usage        |
| `/host <host> win updates`         | Remote host (Windows)   | Queries 10 most recent installed Windows hotfixes/updates|
| `/host <host> parental on`         | Remote host (Linux/Win) | Blocks configured domains in hosts file & flushes DNS    |
| `/host <host> parental off`        | Remote host (Linux/Win) | Unblocks configured domains in hosts file & flushes DNS  |
| `/host <host> parental status`     | Remote host (Linux/Win) | Displays active parental blocking status & domain count  |
| `/host <host> blockapp [app|all] [on|off|status]` | Remote host (Linux/Win) | Blocks/unblocks apps (Roblox, Minecraft, TLauncher...) via IFEO & Flatpak (`appblock` alias; defaults to status) |
| `/host <host> restrict <user> status` | Remote host (Linux/Win) | Displays active logon hours and scheduled tasks for user |
| `/host <host> restrict <user> set <schedule>` | Remote host (Linux/Win) | Enforces logon hours with 5m toast, 1m alert dialog & forced logoff (`/restrict` shortcut) |
| `/host <host> restrict <user> off` | Remote host (Linux/Win) | Clears logon restrictions, purges enforcement tasks, and restores 24/7 unlimited access |
| `/restrict <host> <user> [status|set|off]` | *(alias)* | Convenience shortcut for `/host <host> restrict <user> ...` |
| `/wol <target>`                    | *(alias)*               | Auto-translates to `/lan wake <target>`                  |
| `/home <command>`                  | *(alias)*               | Auto-translates to `/host home <command>`                |
| `/help [topic]`                    | Bot App                 | Interactive manual with nested topics (`lan`, `docker`, `parental`, `host restriction`...)|

> The bot also understands the same commands typed **without** the leading
> slash as plain channel messages (e.g. `lan devices list` or `home docker ps`),
> as long as the bot is invited to the channel.

## 3. Running Locally (Pure Python, No Docker Required)

Cmdiqo provides a built-in local process supervisor ([`run.py`](run.py)):

```bash
# Start both Hub (bot) and local Agent concurrently:
python run.py

# Start the Hub / Slack Bot only:
python run.py hub

# Start the Remote Agent only:
python run.py agent

# Start the interactive Slack emulator (test commands & live agents without Slack tokens!):
python run.py sim
# or directly:
python -m slacksim
# or:
python slacksim/slacksim.py

# Execute a single command non-interactively in simulator:
python run.py sim -c "/host AMD uptime"

# Pass command-line arguments to the agent:
python run.py agent --hub-url ws://192.168.1.10:8765 --token my-secret --name PC-01
```

> 🛑 **Graceful Shutdown:** Pressing `Ctrl+C` safely shuts down all active services and terminates child process trees, ensuring no orphan processes are left running.

### Interactive Slack Console Simulator (`slacksim`)

`slacksim` is a terminal-based emulator that behaves like the Slack chat environment. It is designed for fast local debugging, command verification, and testing remote agents **without needing Slack credentials, public URLs, or Socket Mode**.

- **Full Command & Response Simulation**: Emulates both the instant acknowledgment (`⏳ Running ...`) and final result output (`✅ ...`) with preformatted blocks in light gray.
- **ANSI mrkdwn Rendering**: Automatically formats `*bold*`, `_italics_`, `` `inline code` ``, ```` ```pre/code blocks``` ````, bullet lists, and converts Slack emoji shortcodes (`:white_check_mark:` $\rightarrow$ ✅, `:hourglass_flowing_sand:` $\rightarrow$ ⏳, `:computer:` $\rightarrow$ 💻).
- **Direct Hub Connection & Embedded Hub Integration**: Automatically connects directly to a running Hub server (e.g. launched via `./run.py` or `python run.py hub`) to interact with live connected agents. When running standalone, seamlessly starts an embedded WebSocket Hub server on port `8765`.
- **Interactive Line Editing & Persistent History**:
  - Automatically loads and immediately logs every executed command to `~/.cmdiqo-history` (creating it if missing).
  - `↑` (Up Arrow) — Recall previous command from history.
  - `↓` (Down Arrow) — Navigate forward through history.
  - `Esc` — Instantly clear/delete the current draft message buffer.
  - Left / Right arrows, Home, End, Backspace, and Delete for seamless terminal editing.
- **Dynamic Security Testing**:
  - `/as <user_id>` — Switch simulated Slack user on the fly to test `ALLOWED_USERS` access restrictions.
  - `/in <channel_id>` — Switch simulated channel to test `ALLOWED_CHANNELS`.
  - `/whoami` — Inspect active simulator context and connected agents.
  - `/clear` — Clear terminal screen.

```text
slack [#general] @U0123ABCDEF > /host AMD uptime
  [18:55:01] 🤖 cmdiqo-bot: ⏳ Running `/host AMD uptime` on `AMD` ...
  [18:55:02] 🤖 cmdiqo-bot: ✅ `host-uptime`
  ┌────────────────────────────────────────────────────────────┐
  │ Uptime: 0d 7h 18m 55s (Booted: 2026-09-24 10:06:01)        │
  └────────────────────────────────────────────────────────────┘
```

## 4. Run with Docker Compose

```bash
# In the cloned repository:
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f cmdiqo-bot

# Or standalone using docker/docker-compose.example.yml:
docker compose up -d
docker compose logs -f cmdiqo-bot
```

Notes on the compose file:

- `network_mode: host` — **required** for `arp-scan` to see your real LAN
  (in bridge mode it would only see the Docker network). If you truly cannot
  use host networking, remove it and add:
  ```yaml
  cap_add: [NET_RAW, NET_ADMIN]
  ```
  but the device list will then be limited to what the kernel neighbour table knows.
- `/var/run/docker.sock` is mounted so `/host home docker ...` (or `/home docker ...`) commands control the
  **host** Docker daemon (this is equivalent to root on the host — see Security).
- `/docker` is mounted at the same path so `cwd: /docker/{service}` works. If
  your compose projects live elsewhere, adjust both the mount and `cwd`.
- `./config` is mounted read-only to `/config`: edit commands without rebuilding the image,
  then restart the container (`docker compose restart`).

Plain `docker run` equivalent:

```bash
docker run -d --name cmdiqo-bot --restart unless-stopped \
  --network host \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_APP_TOKEN=xapp-... \
  -e ALLOWED_USERS=U0123ABCDEF \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /docker:/docker \
  -v "$PWD/config:/config:ro" \
  lhotakj/cmdiqo-bot:latest
```

## 4. Interactive Help & Adding Your Own Commands

### Interactive Nested Help (`/help`)

Cmdiqo features a built-in, dynamically generated command manual:
- `/help` — Overview of all command categories, aliases, and quick reference.
- `/help lan` — Detailed manual for LAN operations (ARP scanning, Wake-on-LAN, pinging).
- `/help host` — Detailed manual for host and remote agent operations.
- `/help docker` — Specific guidance for Docker container and compose management.
- `/help system` — Guidance for resource monitoring (uptime, memory, disk, top CPU).
- `/help windows` — Windows agent PowerShell command specifications.
- `/help parental` — Guidance for domain distraction blocking in hosts file.
- `/help blockapp` or `/help appblock` — Guidance for executable process blocking via IFEO & Flatpak.
- `/help restrict` — Guidance for user logon hours restriction, schedule syntax (M-F,08:00-20:00), and resetting to 24/7 unlimited (/restrict off).
- `/help home` or `/help wol` — Details on configured aliases and their targets.
- Appending `help` to any category prefix (e.g. `/lan help`, `/host help`, `/docker help`, `/parental help`, `/blockapp help`, `/restrict help`) directly routes to that topic's detailed breakdown.

### Adding Your Own Commands (`config/commands.yaml`)

Everything is data-driven via `config/commands.yaml`, supporting top-level `aliases:` and `commands:`. Commands are designed to be **OS-agnostic**, using a unified `command:` element that specifies what to execute across platforms:

#### Command Structure & OS Agnosticism

Each command entry contains:
- `name`: Unique identifier for logging and reply headers.
- `trigger`: Slack trigger pattern. Any trigger containing `{host}` automatically targets that host agent; triggers without `{host}` (like `/lan ...`) run locally in the bot container. **No redundant `target: "{host}"` is needed.**
- `description` & `details`: Summary and detailed operational notes shown in Slack `/help`.
- `examples`: Copy-pasteable invocation examples for documentation and `/help`.
- `command` *(mandatory)*: Mapping of execution commands supporting up to three kinds:
  - `python`: Function path inside the `bot` package (e.g. `commands.users:manage_users`). **Executes on any OS.**
  - `shell`: Bash command string. **Executes on Linux agents (or local container).**
  - `ps1`: PowerShell command string. **Executes on Windows agents.**
  - **Dual `shell` + `ps1`**: For cross-platform host commands, define both! Cmdiqo inspects the target agent's reported OS platform and automatically dispatches `shell` to Linux agents and `ps1` to Windows agents.
- `config` *(optional)*: Arbitrary configuration object (dictionary, list, or primitive) accessible to command handlers or parameterized in execution strings via `{config.<key>}` (for example, `config.domains: [list]`).
- `timeout` *(optional)*: Execution timeout in seconds (default: 120s).

```yaml
aliases:
  - name: home
    input: "/home"
    output: "/host home"
    description: "Shortcut alias targeting host 'home'"
    details: "Translates any command starting with /home to /host home."
    examples:
      - "/home docker ps"
      - "/home uptime"

commands:
  # Linux-only command (only shell defined)
  - name: host-docker-logs
    trigger: "/host {host} docker logs {service}"
    description: "Tail last 50 log lines of a docker container"
    details: "Fetches recent standard output and error logs via 'docker logs --tail 50 {service}'."
    examples:
      - "/host home docker logs pihole"
      - "/host home docker logs plex"
    command:
      shell: "docker logs --tail 50 {service}"
    timeout: 60

  # Cross-platform OS-agnostic command (both shell and ps1 defined)
  - name: host-uptime
    trigger: "/host {host} uptime"
    description: "Show system uptime and load average"
    details: "Displays system uptime. Runs 'uptime' on Linux or queries Win32_OperatingSystem via PowerShell on Windows."
    examples:
      - "/host home uptime"
      - "/host AMD uptime"
    command:
      shell: "uptime"
      ps1: "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime; $u = (Get-Date) - $b; 'Uptime: {0}d {1}h {2}m {3}s (Booted: {4:yyyy-MM-dd HH:mm:ss})' -f [int]$u.TotalDays, $u.Hours, $u.Minutes, $u.Seconds, $b"

  # Command with general config (e.g. custom domain list for parental control)
  - name: host-parental
    trigger: "/host {host} parental {action}"
    description: "Manage parental domain blocking on Windows or Linux (on | off | status)"
    details: "Configures system-level hosts file blocking on target Windows or Linux agent. Redirects configured domains to 0.0.0.0 and flushes DNS cache."
    examples:
      - "/host AMD parental on"
      - "/host nas parental on"
    command:
      python: "commands.parental:manage_parental"
    config:
      domains:
        - youtube.com
        - roblox.com
        - minecraft.net
        - discord.com

  # Logon hours restriction (Windows SAM net user /time and Linux PAM time.conf)
  - name: host-restrict-set
    trigger: "/host {host} restrict set {schedule}"
    description: "Enforce allowed logon hours on {host} (e.g. M-F,08:00-20:00;Sa-Su,09:00-21:00)"
    details: "Restricts allowed logon hours on remote host. Use '/host {host} restrict status' to inspect, and '/host {host} restrict off' to restore 24/7 unlimited access."
    examples:
      - "/host PC-KIDS restrict set M-F,08:00-20:00;Sa-Su,09:00-21:00"
      - "/host PC-KIDS restrict status"
      - "/host PC-KIDS restrict off"
    command:
      python: "commands.restrict:manage_restrict"
```

### Python Command Handlers

Python commands can execute on any OS. Point `python:` at `module.function` inside the `bot` package:

```yaml
- name: lan-whois
  trigger: "/lan whois {ip}"
  description: "Reverse-DNS lookup of an IP"
  command:
    python: "commands.network:reverse_dns"
  config:
    default_timeout: 5
```

`bot/commands/network.py`:

```python
import socket

def reverse_dns(params, config=None):
    ip = params["ip"]
    timeout = config.get("default_timeout", 5) if config else 5
    try:
        socket.setdefaulttimeout(timeout)
        return f"{ip} -> {socket.gethostbyaddr(ip)[0]}"
    except Exception as e:
        return f"{ip} -> no PTR record ({e})"
```

Rebuild (or just restart if you mount the code) after adding Python handlers.

## 4. Hub & Agent Proxy Architecture (Remote Network Execution)

### How It Works

Cmdiqo uses an **asymmetric, reverse-connection WebSocket architecture** specifically designed for home servers and private networks behind NAT:

```mermaid
sequenceDiagram
    autonumber
    actor User as Slack User
    participant Slack as Slack Cloud API
    participant Bot as Cmdiqo Container (Bot + Hub)
    participant Agent as LAN Agent (Win / Linux)
    participant Host as Target Host OS

    Note over Bot: Runs behind NAT (Docker host mode)<br/>Outbound Socket Mode connection to Slack
    Agent->>Bot: 1. Outbound WS connect ws://hub:8765<br/>(Bearer Token Authentication)
    Note over Bot,Agent: Keepalive Ping/Pong (20s interval)

    User->>Slack: 2. Type "/host PC-OLO win updates" (or alias)
    Slack->>Bot: 3. Dispatch slash command event (Socket Mode)
    Note over Bot: Apply aliases, validate user allow-list,<br/>sanitize parameters, lookup target 'PC-OLO'
    Bot->>Agent: 4. Send exec request (JSON over WebSocket):<br/>{id, kind: "ps1", cmd: "Get-CimInstance..."}
    Agent->>Host: 5. Spawn subprocess (powershell.exe / bash)
    Host-->>Agent: 6. Output stream + exit code
    Agent-->>Bot: 7. Send result (JSON over WebSocket):<br/>{id, ok: true, code: 0, output: "..."}
    Bot-->>Slack: 8. Post formatted output in thread
    Slack-->>User: 9. Message delivered in Slack
```

#### Why This Design?
- **Zero Inbound Open Ports:** The agents connect **outbound** to the hub, and the hub connects **outbound** to Slack via Socket Mode. Neither the server nor your workstations need public IP addresses, port forwarding, dynamic DNS, or firewall pinholes.
- **Timing-Safe Authentication:** The hub validates incoming agent connections using constant-time comparison (`hmac.compare_digest`), preventing side-channel timing attacks (SonarQube S5804 / CWE-208).
- **Command Injection Prevention:** Placeholder parameters (like `{service}` or `{pc}`) are strictly checked against safe identifier character sets (`[a-zA-Z0-9_\-\.]+`), blocking shell injection characters (`;`, `&`, `|`, backticks).
- **Fast-Fail on Disconnection:** If an agent drops offline mid-execution, the hub immediately fails the pending request and notifies Slack, rather than hanging for 150 seconds.

---

### Configuring the Agent

The agent supports 3 configuration methods with the following precedence:
1. **Command-Line Arguments** (highest priority)
2. **Environment Variables**
3. **JSON Configuration File**

#### Configuration Options Reference

| Command-Line Flag | Environment Variable | JSON Key | Default | Description |
|---|---|---|---|---|
| `-u`, `--hub-url` | `HUB_URL` | `hub_url` | *(required)* | WebSocket URL of the hub (e.g. `ws://192.168.1.10:8765`) |
| `-t`, `--token` | `AGENT_TOKEN` | `token` | *(required)* | Shared secret matching the hub's `AGENT_TOKEN` |
| `-n`, `--name` | `AGENT_NAME` | `name` | Hostname | Unique name for this agent, matched by `/host {host} ...` commands |
| `-c`, `--config` | `AGENT_CONFIG` | — | `None` | Path to JSON config file (e.g. `/etc/cmdiqo-agent/agent.json`) |
| `-d`, `--reconnect-delay` | `RECONNECT_DELAY` | `reconnect_delay` | `5.0` | Initial reconnect delay in seconds (uses exponential backoff) |
| `--log-level` | `LOG_LEVEL` | — | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

#### Example JSON Config (`agent/agent.json.example`)
```json
{
  "hub_url": "ws://192.168.1.10:8765",
  "token": "change-me-long-random-string",
  "name": "nas",
  "reconnect_delay": 5.0
}
```

---

### Resource Consumption & Performance Benchmarks

The native ISO C++20 agent daemon and installer wizards are engineered for minimal host footprint:

| Component | Target OS | Binary Size | RAM Memory Footprint | Cold Boot Time |
| :--- | :--- | :---: | :---: | :---: |
| **cmdiqo-agent** | Linux (Ubuntu/Debian/RHEL x64) | ~1.4 MB | **~1.1 MB RAM** | < 10 ms |
| **cmdiqo-agent.exe** | Windows 10/11/Server x64 | ~1.8 MB | **~3.3 MB RAM** | < 10 ms |
| **Windows Installer GUI** | Windows 10/11/Server x64 | ~2.5 MB | **~5.2 MB RAM** | < 25 ms |

Because the background agent daemon consumes only **1.1 MB RAM on Linux** and **3.3 MB RAM on Windows**, it remains completely unnoticeable on desktop workstations and runs reliably even on the smallest resource-constrained VPS instances and Raspberry Pis.

---

### Building Standalone Binaries (Windows, Linux 64-bit & ARM64)

You can compile the agent into a single, standalone binary that has **zero Python runtime dependencies** on target machines. Full build documentation is available in [BUILD.md](BUILD.md).

#### 1. Build Windows 64-bit Agent Package

On any Windows machine:
```powershell
# Compiles the PE agent and Windows service ZIP:
.\build.ps1 --clean --platform win64 --target agent

# Compiles the agent ZIP and paired native Windows setup bundle:
.\build.ps1 --installer
```
Outputs: `dist/cmdiqo-agent.exe` and `dist/cmdiqo-agent-windows-x64.zip`.

#### 2. Build Linux Agent Package (x86_64, ARM64, and ARMv6)

On Linux (or WSL):
```bash
# Build Linux x86_64 agent and ZIP package:
./build.sh --clean --platform linux64 --target agent

# Build Linux ARM64 (aarch64) agent and ZIP package (Raspberry Pi 3/4/5, Zero 2 W, SBCs, AWS Graviton):
./build.sh --clean --platform linux-arm64 --target agent

# Build Linux ARMv6 (armv6l) agent and ZIP package (Raspberry Pi Zero / Zero W, Pi 1):
./build.sh --clean --platform linux-armv6 --target agent

# Build Linux agent and paired native setup (GUI on x64, CLI on ARM64 and ARMv6):
./build.sh --installer --platform linux64
./build.sh --installer --platform linux-arm64
./build.sh --installer --platform linux-armv6

# Build all components across all platforms (linux64, linux-arm64, linux-armv6, win64, bot):
./build.sh --build-all
```
Outputs:
- **Linux x86_64**: `dist/cmdiqo-agent`, `dist/cmdiqo-agent-linux-x64.zip`, and `dist/cmdiqo-agent-linux-x64-setup`
- **Linux ARM64**: `dist/cmdiqo-agent-linux-arm64`, `dist/cmdiqo-agent-linux-arm64.zip`, and `dist/cmdiqo-agent-linux-arm64-setup`
- **Linux ARMv6**: `dist/cmdiqo-agent-linux-armv6`, `dist/cmdiqo-agent-linux-armv6.zip`, and `dist/cmdiqo-agent-linux-armv6-setup`
- **Windows x64**: `dist/cmdiqo-agent.exe`, `dist/cmdiqo-agent-windows-x64.zip`, and `dist/cmdiqo-agent-windows-x64-setup.exe`

Each ZIP includes the binary and platform service scripts: `install.sh`/`uninstall.sh` on Linux and `install.ps1`/`uninstall.ps1` on Windows.

`--installer` additionally writes the native setup executable to `dist/` beside
the matching ZIP (GUI installer for Windows & Linux x64, self-contained CLI installer for ARM64 and ARMv6).

The Docker installer routes in `build.sh` use the unified
`lhotakj/cmdiqo-installer-builder:all` image: native GCC builds the Linux x64 GTK3 setup,
`aarch64-linux-gnu-gcc` cross-compiles the Linux ARM64 setup, `armv6-linux-musleabihf-gcc` (targeting `-marm -march=armv6zk -mfloat-abi=hard -mfpu=vfp` with static musl libc) cross-compiles the Linux ARMv6 setup, and MinGW-w64 cross-compiles the Windows wxMSW setup.

#### 3. Automated CI Builds via GitHub Actions (Direct Download & Release)
The repository includes [`.github/workflows/release.yml`](.github/workflows/release.yml). Select **Actions → Build & Release → Run workflow** or push with `/release` on `main`:
1. Go to your GitHub repository and click the **Actions** tab.
2. Select the latest **Build & Release** workflow run.
3. Download artifacts from the **Artifacts** section at the bottom of the summary page, or download directly from the tagged GitHub Release.
4. Includes Linux (x86_64 and ARM64) and Windows native agents, service ZIP packages, and GUI/CLI installers.

Commit `/release` (or `/release-agent`, `/release-bot`, with optional version like `/release 0.10.0`) on `main` to build all artifacts via `build.sh` and publish them to a GitHub Release and Docker Hub. CI verifies every ZIP and binary before upload.

---

### Deploying the Agent as a Service

#### Linux: Deploy as a Systemd Service

1. **Automated Installation Script:**
   ```bash
   chmod +x agent/install-service.sh
   sudo ./agent/install-service.sh
   ```
   The installer automatically:
   - Installs the binary to `/usr/local/bin/cmdiqo-agent` (or prepares an isolated venv if no binary is present)
   - Creates configuration at `/etc/cmdiqo-agent/agent.env`
   - Installs and enables [`agent/cmdiqo-agent.service`](agent/cmdiqo-agent.service)
   - Starts the service immediately

2. **Managing the Linux Service:**
   ```bash
   # Check service status
   sudo systemctl status cmdiqo-agent

   # View live streaming logs
   sudo journalctl -u cmdiqo-agent -f

   # Restart or stop
   sudo systemctl restart cmdiqo-agent
   sudo systemctl stop cmdiqo-agent
   ```

#### Windows: Deploy as an Automatic Background Service

1. **Automated Installation Script (PowerShell):**
   Open PowerShell as **Administrator** and run:
   ```powershell
   .\agent\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "change-me-long-random-string" -Name "WIN-PC"
   ```
   The script automatically:
   - Installs `cmdiqo-agent.exe` into `C:\Program Files\Cmdiqo\Agent\`
   - Generates configuration at `C:\ProgramData\Cmdiqo\Agent\config.json` (accessible for easy editing)
   - Registers a native Windows Service (`CmdiqoAgent`) running under `NT AUTHORITY\SYSTEM` on boot
   - Starts the service immediately

2. **Managing the Windows Service:**
   ```powershell
   # Check service state
   Get-Service CmdiqoAgent

   # Start / Stop / Restart service
   Start-Service CmdiqoAgent
   Stop-Service CmdiqoAgent
   Restart-Service CmdiqoAgent

   # Uninstall service completely
   .\agent\uninstall-service.ps1
   ```

---

### Remote Command Examples (`config/commands.yaml`)

```yaml
aliases:
  - name: home
    input: "/home"
    output: "/host home"

commands:
  # OS-agnostic host storage check (runs df -h on Linux, Get-CimInstance on Windows)
  - name: host-disk
    trigger: "/host {host} disk"
    description: "Check storage on host"
    command:
      shell: "df -h"
      ps1: 'Get-CimInstance Win32_LogicalDisk -Filter ''DriveType=3'' | Select-Object DeviceID, VolumeName, @{Name=''Total_GB'';Expression={[math]::Round($_.Size/1GB,2)}}, @{Name=''Free_GB'';Expression={[math]::Round($_.FreeSpace/1GB,2)}}, @{Name=''Used_GB'';Expression={[math]::Round(($_.Size - $_.FreeSpace)/1GB,2)}} | Format-Table -AutoSize'

  # Windows-only updates inspection
  - name: host-win-updates
    trigger: "/host {host} win updates"
    description: "Installed Windows hotfixes on host"
    command:
      ps1: "Get-CimInstance Win32_QuickFixEngineering | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID, InstalledOn | Format-Table -AutoSize"
    timeout: 180

  # Cross-platform host reboot
  - name: host-reboot
    trigger: "/host {host} reboot"
    description: "Reboot host after warning"
    command:
      shell: "shutdown -r +1 'Reboot requested from Slack'"
      ps1: "shutdown /r /t 10 /c 'Reboot requested from Slack'"
    timeout: 60
```

Typing `/host PC-OLO reboot` in Slack safely reboots the machine whether it runs Windows or Linux.
Typing `/host PC-OLO win updates` executes the PowerShell query on the Windows agent and returns the output to Slack.
Typing `/home docker ps` translates via alias to `/host home docker ps` and queries Docker containers on host `home`.

### Built-in System Commands
- `/lan agents list` (or `/host agents list`) in Slack at any time to see every connected agent, its operating system, version, connection uptime, and LAN IP address.
- `/lan version` (or `/host version`) to display the running version of `cmdiqo-bot`.

### Parental Controls & Access Guards

Cmdiqo includes native access management and parental guard commands operable directly from Slack or the WebGUI:

1. **Logon Hours Restriction (`/host <host> restrict <user>`)**:
   Enforce allowed login times for users on Windows (via SAM `net user <user> /time:...` with automated 1-hour normalization and Task Scheduler toast/alert/logoff enforcement) and Linux (via PAM `/etc/security/time.conf`).
   ```bash
   # Set weekly schedule for user (M-F 07:00-18:00 and Sa-Su 08:00-18:00)
   /host PC-KIDS restrict alex set Sa-Su,08:00-18:00;M-F,07:00-18:00

   # Inspect current restriction status and active enforcement tasks
   /host PC-KIDS restrict alex status
   # Output: • User alex: 🔒 Restricted → M-F: 07:00-18:00; Sa-Su: 08:00-18:00

   # Remove restriction, purge scheduled enforcement tasks, and restore 24/7 access
   /host PC-KIDS restrict alex off
   ```

2. **Parental Domain Shield (`/host <host> parental`)**:
   Instantly block or unblock gaming, streaming, and social video platforms via local `/etc/hosts` / `C:\Windows\System32\drivers\etc\hosts` DNS sinkholing:
   ```bash
   /host PC-KIDS parental on       # Enable domain blocking
   /host PC-KIDS parental off      # Disable domain blocking
   /host PC-KIDS parental status   # Check current shield state
   ```

3. **Application Execution Blocking (`/host <host> blockapp`)**:
   Prevent execution of games and communication apps (Roblox, Minecraft, Steam, Epic Games, Discord, Battle.net, GeForce NOW, Riot, TikTok, TLauncher, or `all`) using Windows Image File Execution Options (IFEO) / AppX blocks and Linux execution overrides:
   ```bash
   /host PC-KIDS blockapp roblox on      # Block Roblox execution
   /host PC-KIDS blockapp roblox off     # Restore Roblox access
   /host PC-KIDS blockapp all on         # Block all tracked gaming/social apps
   /host PC-KIDS blockapp all status     # Query block state of all apps
   ```

---

## 5. WebGUI Dashboard & Remote Actions

Cmdiqo includes an optional, zero-install, real-time web interface built into the bot container.

### Enabling the WebGUI
Set `WEB_PORT` in your `.env` or container environment (e.g. `WEB_PORT=8080`). The dashboard becomes available immediately at `http://<bot-ip>:8080`.

### Key Features
- **Live Agent Fleet Table**:
  - Displays all connected agents with 32×32 WebP architecture badges (`WIN64`, `Linux64`, `ARM64`, `ARMv6`), host name, version, IP address, operating system, and synchronized live 1Hz uptimes.
  - Interactive multi-column sorting (`Arch`, `Host Name`, `Operating System`, `Uptime`) with automatic `localStorage` persistence across page reloads.
- **Interactive Host Actions Menu**:
  - Click the terminal action icon (`>_`) in any host row to open a categorized context menu:
    - **System Metrics**: Uptime, Memory (`free -m`), Storage (`disk`), Top CPU (`top`).
    - **Docker Control**: Containers (`docker ps`), Resource Stats (`docker stats`), Tail Logs, Restart Service.
    - **Services & Windows**: System Services list, Query Service Status, Restart Service, Windows Hotfix Updates.
    - **Parental Controls**: Domain Blocking (Status, Enable, Disable), Logon Hours Restriction (Status, Set Schedule, Disable), Application Blocking (Status, Block, Unblock).
    - **Power**: Remote Reboot, Remote Shutdown.
- **In-Browser Terminal Modal**:
  - Dispatches commands directly to the target agent through the WebSocket Hub.
  - Converts Slack-formatted responses (emojis, `*bold*`, `_italics_`, `` `code` `` blocks, and bullet points) into clean HTML in real time.
  - Automatically detects execution errors, highlighting failures with a red `FAILED` status badge and logging them at `ERROR` level in the event stream.
- **Server-Sent Events (SSE) Live Event Stream**:
  - Streams host connections, disconnections, command executions, and hub alerts in real time with zero polling overhead.
  - 50-event ring buffer with a reverse-order toggle (newest first / oldest first).

---

## 6. Publishing to Docker Hub with GitHub Actions

The unified workflow [`.github/workflows/release.yml`](.github/workflows/release.yml) utilizes `build.sh` to build
multi-arch images (`linux/amd64` + `linux/arm64`) and pushes to Docker Hub whenever a commit on
`main` includes `/release` (or `/release-bot`), or upon manual **workflow_dispatch**.

1. Create a Docker Hub access token: <https://app.docker.com/settings/security>
   → **New Access Token**.
2. In your GitHub repo: *Settings → Secrets and variables → Actions → New
   repository secret*, add:
   - `DOCKERHUB_USERNAME` — your Docker Hub username
   - `DOCKERHUB_TOKEN` — the access token
3. Push with `/release` on `main`. The multi-arch image appears as
   `<your-user>/cmdiqo-bot:latest`, tagged with the release version.
   Push a tag like `v1.0.0` to also get `:1.0.0` and `:1.0`.
4. Set `DOCKERHUB_USER` in `.env` (or hardcode your user in
   `docker-compose.yml`) so `docker compose pull` fetches your image instead of
   building locally.

### Manual push (without CI)

```bash
# Direct Docker build:
docker build -t youruser/cmdiqo-bot:latest -f docker/Dockerfile .
docker push youruser/cmdiqo-bot:latest

# Or using repository build scripts:
./build.sh --bot          # Linux / macOS
.\build.ps1 --target bot  # Windows
```

## Environment variable reference

| Variable           | Required | Default                | Purpose                                   |
|--------------------|----------|------------------------|-------------------------------------------|
| `SLACK_BOT_TOKEN`  | yes      | —                      | `xoxb-...` bot token                      |
| `SLACK_APP_TOKEN`  | yes      | —                      | `xapp-...` Socket Mode token              |
| `ALLOWED_USERS`    | no       | *(everyone)*           | Comma-separated Slack user IDs allow-list |
| `ALLOWED_CHANNELS` | no       | *(every channel)*      | Comma-separated channel IDs allow-list    |
| `SHELL_TIMEOUT`    | no       | `120`                  | Default shell command timeout (seconds)   |
| `COMMANDS_CONFIG`  | no       | `/config/commands.yaml` | Path to commands YAML                |
| `AGENT_TOKEN`      | no (recommended) | *(empty)*      | Shared secret for hub agent connections         |
| `HUB_ENABLED`      | no       | `true`                 | Enable/disable the agent hub                          |
| `HUB_HOST`         | no       | `0.0.0.0`              | Hub bind address                                      |
| `HUB_PORT`         | no       | `8765`                 | WebSocket Hub port (agents connect here, e.g. `export HUB_PORT=8765`) |
| `WEB_PORT`         | no       | *(disabled)*           | Optional: Port to host integrated real-time WebGUI (e.g. `export WEB_PORT=8080`) |
| `LOG_LEVEL`        | no       | `INFO`                 | Logging level                             |

## Security notes

- `ALLOWED_USERS` is **off by default**. Do not run it without an allow-list in
  a shared workspace — anyone who can reach the bot can run the commands you
  configured.
- Mounting `/var/run/docker.sock` gives the container **root-level control over
  the host**. Only mount it if you need the `/host home docker ...` or `/home docker ...` commands.
- Shell commands run with `argv` splitting (`shlex`), never through a shell
  with string interpolation of user input, so placeholders cannot inject extra
  commands. Still, treat every configured command as a capability you hand to
  whoever passes your allow-list.
- Keep `config/commands.yaml` free of secrets; pass those via env/`.env` and
  reference files (e.g. mount a `.env` for `docker compose` projects).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Slash command shows "No command found" | Create `/lan`, `/host`, and `/home` under *Slash Commands* and **reinstall** the app to the workspace. |
| Bot online but doesn't reply to channel messages | Invite it (`/invite @lan-bot`); check `channels:history` scope; check `ALLOWED_CHANNELS`. |
| `/lan devices list` returns nothing | Use `network_mode: host` (arp-scan needs raw sockets on the real interface). |
| `docker: command not found` / socket errors | Mount `/var/run/docker.sock`; the image ships docker CLI + compose plugin. |
| `cwd` errors for `/host home docker ...` / `/home ...` | Mount the host directory (e.g. `/docker:/docker`) so the `cwd` path exists. |
| Changes to `commands.yaml` ignored | Restart the container: `docker compose restart`. |
| `/lan agents list` shows no agents | Check `AGENT_TOKEN` matches on both sides; check `HUB_URL` host/port and that the hub port is reachable on the LAN (`Test-NetConnection 192.168.1.10 -Port 8765`). |
| `agent 'x' is not connected` | Agent not running / failed auth / wrong `AGENT_NAME` (the `{host}` parameter in the command must match the agent's name). |
| `ps1` on Windows: "executable not found" | PowerShell is in PATH on Windows by default; if using PowerShell Core on Linux install `pwsh`. |

## Development, Testing & Code Quality

Cmdiqo is developed following SonarQube Clean Code standards and strict automated testing requirements.

### Running Tests Locally

```bash
# Install development and testing dependencies
pip install -r requirements-dev.txt

# Run linter
ruff check bot agent development tests

# Run full test suite with coverage
pytest -v --cov=bot --cov=agent --cov-report=term-missing --cov-report=xml:coverage.xml
```

For complete instructions on building standalone binaries, local dev environments, and GitHub Actions CI, see **[BUILD.md](BUILD.md)**.

### SonarQube & Agentic Skills

- **Antigravity Skill:** [`.agents/skills/cmdiqo-quality/SKILL.md`](.agents/skills/cmdiqo-quality/SKILL.md) and [`SKILL.md`](SKILL.md) provide guidelines for SonarQube Quality Gate compliance, constant-time token comparison (`hmac`), safe command parameter sanitization, and WebSocket keepalives.
- **SonarQube Configuration:** [`sonar-project.properties`](sonar-project.properties) configures source analysis, coverage reporting (`coverage.xml`), and exclusion rules.
- **CI / CD Pipeline:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) executes linting, test coverage verification, and SonarQube quality checks on every push and pull request.

## License

MIT — see `LICENSE`.
