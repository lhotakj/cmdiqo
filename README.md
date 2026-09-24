# shango-bot (Shango)

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
Bot:   IP ADDRESS     MAC ADDRESS        VENDOR / INFO
       192.168.1.1    aa:bb:cc:dd:ee:ff  Technicolor ...
       192.168.1.20   11:22:33:44:55:66  Raspberry Pi Trading Ltd

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

## Repository layout

```
.
├── Dockerfile                      # all-in-one image (python + arp-scan + docker CLI)
├── docker-compose.yml              # ready-to-run compose file
├── requirements.txt
├── build.ps1                       # native Windows build wrapper
├── build.sh                        # native Linux/macOS build wrapper
├── bot/
│   ├── app.py                      # Slack app, dispatch, auth
│   ├── hub.py                      # WebSocket hub for remote agents
│   └── commands/                   # modular command handlers
├── config/
│   └── commands.yaml               # <-- YOUR commands live here
├── agent/
│   ├── agent.py                    # tiny Python agent for LAN hosts (Win/Linux)
│   ├── install-service.ps1         # Windows service installer
│   └── install-service.sh          # Linux systemd service installer
├── development/                    # build machinery, versioning, environment setup
│   ├── build.ps1                   # local PowerShell build script
│   ├── build.sh                    # local Bash build script
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
   - `chat:write` (to post replies)
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
```

Edit `config/commands.yaml` to define your commands and aliases (see **Adding your own commands** below). The defaults give you:

| You type in Slack                  | What happens                                             |
|------------------------------------|----------------------------------------------------------|
| `/lan devices list`                | ARP-scans the LAN, prints IP / MAC / vendor table        |
| `/lan wake 00:11:22:33:44:55`      | Sends Wake-on-LAN magic packet to boot a device          |
| `/lan ping 192.168.1.1`            | Pings a device or IP from the Hub container              |
| `/lan agents list`                 | Lists connected agents, operating systems, and IPs       |
| `/lan version`                     | Displays bot version                                     |
| `/host home uptime`                | System uptime and load averages                          |
| `/host home memory`                | Free and used RAM memory (`free -h`)                     |
| `/host nas disk`                   | Disk space usage (`df -h`)                               |
| `/host home top`                   | Top CPU-consuming processes                              |
| `/host home docker ps`             | Lists running docker containers                          |
| `/host home docker logs plex`      | Tails last 50 log lines of a docker container            |
| `/host home docker restart plex`   | Restarts docker compose project in `/docker/plex`        |
| `/host home docker stats`          | Snapshot of container CPU, RAM, and network usage        |
| `/host nas service status smbd`    | Checks systemd service status (`systemctl status`)       |
| `/host nas service restart nginx`  | Restarts systemd service (`systemctl restart`)           |
| `/host WIN-PC win updates`         | Queries installed Windows hotfixes on agent `WIN-PC`     |
| `/host WIN-PC win memory`          | RAM usage on Windows machine                             |
| `/host WIN-PC win services`        | Running Windows services table                           |
| `/host WIN-PC win reboot`          | Reboots Windows machine after 10-second warning          |
| `/host WIN-PC parental on`         | Blocks YouTube & Roblox in hosts, flushes DNS            |
| `/host WIN-PC parental off`        | Unblocks YouTube & Roblox in hosts, flushes DNS          |
| `/host WIN-PC parental status`     | Displays active parental blocking status                 |
| `/wol 00:11:22:33:44:55`           | *(alias)* auto-translates to `/lan wake ...`             |
| `/home docker ps`                  | *(alias)* auto-translates to `/host home docker ps`      |
| `/lan` or `/host` (no arguments)   | Built-in help showing commands and configured aliases    |

> The bot also understands the same commands typed **without** the leading
> slash as plain channel messages (e.g. `lan devices list` or `home docker ps`),
> as long as the bot is invited to the channel.

## 3. Running Locally (Pure Python, No Docker Required)

Shango provides a built-in local process supervisor ([`run.py`](run.py)):

```bash
# Start both Hub (bot) and local Agent concurrently:
python run.py

# Start the Hub / Slack Bot only:
python run.py hub

# Start the Remote Agent only:
python run.py agent

# Pass command-line arguments to the agent:
python run.py agent --hub-url ws://192.168.1.10:8765 --token my-secret --name PC-01
```

> 🛑 **Graceful Shutdown:** Pressing `Ctrl+C` safely shuts down all active services and terminates child process trees, ensuring no orphan processes are left running.

## 4. Run with Docker Compose

```bash
docker compose up -d --build
docker compose logs -f shango-bot
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
- `./config` is mounted read-only: edit commands without rebuilding the image,
  then restart the container (`docker compose restart`).

Plain `docker run` equivalent:

```bash
docker run -d --name shango-bot --restart unless-stopped \
  --network host \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_APP_TOKEN=xapp-... \
  -e ALLOWED_USERS=U0123ABCDEF \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /docker:/docker \
  -v "$PWD/config:/app/config:ro" \
  youruser/shango-bot:latest
```

## 4. Interactive Help & Adding Your Own Commands

### Interactive Nested Help (`/help`)

Shango features a built-in, dynamically generated command manual:
- `/help` — Overview of all command categories, aliases, and quick reference.
- `/help lan` — Detailed manual for LAN operations (ARP scanning, Wake-on-LAN, pinging).
- `/help host` — Detailed manual for host and remote agent operations.
- `/help docker` — Specific guidance for Docker container and compose management.
- `/help system` — Guidance for resource monitoring (uptime, memory, disk, top CPU).
- `/help windows` — Windows agent PowerShell command specifications.
- `/help home` or `/help wol` — Details on configured aliases and their targets.

### Adding Your Own Commands (`config/commands.yaml`)

Everything is data-driven via `config/commands.yaml`, supporting top-level `aliases:` and `commands:`. You can specify rich metadata (`category`, `details`, `examples`) which automatically feeds into the `/help` engine:

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
  - name: host-docker-logs
    category: docker
    trigger: "/host {host} docker logs {service}"
    description: "Tail last 50 log lines of a docker container"
    details: "Fetches recent standard output and error logs via 'docker logs --tail 50 {service}'."
    examples:
      - "/host home docker logs pihole"
      - "/host home docker logs plex"
    kind: shell
    target: "{host}"
    run: "docker logs --tail 50 {service}"
    timeout: 60
```
  kind: shell
  run: "wakeonlan {mac}"        # install wakeonlan in the image if needed
```

### Python command

Point `handler` at `module.function` inside the `bot` package:

```yaml
- name: lan-whois
  trigger: "/lan whois {ip}"
  description: "Reverse-DNS lookup of an IP"
  kind: python
  handler: "commands.network:reverse_dns"
```

`bot/commands/network.py`:

```python
import socket

def reverse_dns(params):
    ip = params["ip"]
    try:
        return f"{ip} -> {socket.gethostbyaddr(ip)[0]}"
    except Exception as e:
        return f"{ip} -> no PTR record ({e})"
```

Rebuild (or just restart if you mount the code) after adding Python handlers.

## 4. Hub & Agent Proxy Architecture (Remote Network Execution)

### How It Works

Shango uses an **asymmetric, reverse-connection WebSocket architecture** specifically designed for home servers and private networks behind NAT:

```mermaid
sequenceDiagram
    autonumber
    actor User as Slack User
    participant Slack as Slack Cloud API
    participant Bot as Shango Container (Bot + Hub)
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
| `-n`, `--name` | `AGENT_NAME` | `name` | Hostname | Unique name for this agent, used in `target:` commands |
| `-c`, `--config` | `AGENT_CONFIG` | — | `None` | Path to JSON config file (e.g. `/etc/shango-agent/agent.json`) |
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

### Building Standalone Binaries (Windows & Linux 64-bit)

You can compile the agent into a single, standalone binary that has **zero Python runtime dependencies** on target machines. Full build documentation is available in [BUILD.md](BUILD.md).

#### 1. Build Windows 64-bit Executable (`shango-agent.exe`)

On any Windows machine:
```powershell
# Using the PowerShell build script (compiles, signs, and embeds version metadata):
.\build.ps1

# Or with python:
python development/build_agent.py
```
Output binary: `dist/shango-agent.exe` (digitally signed, with PE version resource).

#### 2. Build Linux 64-bit Binary (`shango-agent`)

**Option A — Cross-compile on Windows or macOS using Docker:**
```bash
docker run --rm -v "${PWD}:/src" -w /src python:3.12-slim bash -c \
  "pip install pyinstaller -r agent/requirements.txt && python development/build_agent.py"
```
Output binary: `dist/shango-agent`.

**Option B — Directly on a Linux host:**
```bash
./build.sh
# Or with python:
python development/build_agent.py
```
Output binary: `dist/shango-agent`.

#### 3. Automated CI Builds via GitHub Actions (Direct Download)
The repository includes [`.github/workflows/build-agent.yml`](.github/workflows/build-agent.yml) which automatically compiles and signs both binaries on every push to `main` or manual workflow dispatch:
1. Go to your GitHub repository and click the **Actions** tab.
2. Select the latest **Build Agent Binaries** workflow run.
3. Scroll down to the **Artifacts** section at the bottom of the summary page.
4. Click on either artifact to download:
   - `shango-agent-windows-x64` (contains `shango-agent-windows-x64.exe` digitally signed, and `shango-agent.cer`)
   - `shango-agent-linux-x64` (contains executable Linux x64 ELF binary)

> **No Release Required:** Binaries are stored directly as downloadable build artifacts (retained for 90 days), so you can download fresh builds anytime without tagging a release.

---

### Deploying the Agent as a Service

#### Linux: Deploy as a Systemd Service

1. **Automated Installation Script:**
   ```bash
   chmod +x agent/install-service.sh
   sudo ./agent/install-service.sh
   ```
   The installer automatically:
   - Installs the binary to `/usr/local/bin/shango-agent` (or prepares an isolated venv if no binary is present)
   - Creates configuration at `/etc/shango-agent/agent.env`
   - Installs and enables [`agent/shango-agent.service`](agent/shango-agent.service)
   - Starts the service immediately

2. **Managing the Linux Service:**
   ```bash
   # Check service status
   sudo systemctl status shango-agent

   # View live streaming logs
   sudo journalctl -u shango-agent -f

   # Restart or stop
   sudo systemctl restart shango-agent
   sudo systemctl stop shango-agent
   ```

#### Windows: Deploy as an Automatic Background Service

1. **Automated Installation Script (PowerShell):**
   Open PowerShell as **Administrator** and run:
   ```powershell
   .\agent\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "change-me-long-random-string" -Name "WIN-PC"
   ```
   The script automatically:
   - Installs `shango-agent.exe` into `C:\Program Files\Shango\Agent\`
   - Generates configuration at `C:\ProgramData\Shango\Agent\config.json` (accessible for easy editing)
   - Registers a native Windows Service (`ShangoAgent`) running under `NT AUTHORITY\SYSTEM` on boot
   - Starts the service immediately

2. **Managing the Windows Service:**
   ```powershell
   # Check service state
   Get-Service ShangoAgent

   # Start / Stop / Restart service
   Start-Service ShangoAgent
   Stop-Service ShangoAgent
   Restart-Service ShangoAgent

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
  # Linux agent (or local fallback)
  - name: host-disk
    trigger: "/host {host} disk"
    description: "Check storage on host"
    kind: shell
    target: "{host}"
    run: "df -h"

  # Windows agent, dynamically targeting the specified host
  - name: host-win-reboot
    trigger: "/host {host} win reboot"
    description: "Reboot a Windows PC"
    kind: ps1
    target: "{host}"
    run: "shutdown /r /t 10 /c 'Reboot requested from Slack'"
```

Typing `/host PC-OLO win reboot` in Slack runs that PowerShell command on the agent named `PC-OLO` and streams the output back into the Slack thread.
Or typing `/home docker ps` translates via the configured alias to `/host home docker ps`.

### Built-in System Commands
- `/lan agents list` (or `/host agents list`) in Slack at any time to see every connected agent, its operating system, version, connection uptime, and LAN IP address.
- `/lan version` (or `/host version`) to display the running version of `shango-bot`.

## 5. Publishing to Docker Hub with GitHub Actions

The included workflow `.github/workflows/docker-publish.yml` builds a
multi-arch image (`linux/amd64` + `linux/arm64`) and pushes it on every push
to `main` and every `v*` tag.

1. Create a Docker Hub access token: <https://app.docker.com/settings/security>
   → **New Access Token**.
2. In your GitHub repo: *Settings → Secrets and variables → Actions → New
   repository secret*, add:
   - `DOCKERHUB_USERNAME` — your Docker Hub username
   - `DOCKERHUB_TOKEN` — the access token
3. Push to `main`. The image appears as
   `<your-user>/shango-bot:latest`.
   Push a tag like `v1.0.0` to also get `:1.0.0` and `:1.0`.
4. Set `DOCKERHUB_USER` in `.env` (or hardcode your user in
   `docker-compose.yml`) so `docker compose pull` fetches your image instead of
   building locally.

### Manual push (without CI)

```bash
docker build -t youruser/shango-bot:latest .
docker push youruser/shango-bot:latest
```

## Environment variable reference

| Variable           | Required | Default                | Purpose                                   |
|--------------------|----------|------------------------|-------------------------------------------|
| `SLACK_BOT_TOKEN`  | yes      | —                      | `xoxb-...` bot token                      |
| `SLACK_APP_TOKEN`  | yes      | —                      | `xapp-...` Socket Mode token              |
| `ALLOWED_USERS`    | no       | *(everyone)*           | Comma-separated Slack user IDs allow-list |
| `ALLOWED_CHANNELS` | no       | *(every channel)*      | Comma-separated channel IDs allow-list    |
| `SHELL_TIMEOUT`    | no       | `120`                  | Default shell command timeout (seconds)   |
| `COMMANDS_CONFIG`  | no       | `/app/config/commands.yaml` | Path to commands YAML                |
| `AGENT_TOKEN`      | no (recommended) | *(empty)*      | Shared secret for hub agent connections         |
| `HUB_ENABLED`      | no       | `true`                 | Enable/disable the agent hub                          |
| `HUB_HOST`         | no       | `0.0.0.0`              | Hub bind address                                      |
| `HUB_PORT`         | no       | `8765`                 | Hub port (agents connect here)                        |
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
| `agent 'x' is not connected` | Agent not running / failed auth / wrong `AGENT_NAME` (`target:` must equal the agent's name). |
| `ps1` on Windows: "executable not found" | PowerShell is in PATH on Windows by default; if using PowerShell Core on Linux install `pwsh`. |

## Development, Testing & Code Quality

Shango is developed following SonarQube Clean Code standards and strict automated testing requirements.

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

- **Antigravity Skill:** [`.agents/skills/shango-quality/SKILL.md`](.agents/skills/shango-quality/SKILL.md) and [`SKILL.md`](SKILL.md) provide guidelines for SonarQube Quality Gate compliance, constant-time token comparison (`hmac`), safe command parameter sanitization, and WebSocket keepalives.
- **SonarQube Configuration:** [`sonar-project.properties`](sonar-project.properties) configures source analysis, coverage reporting (`coverage.xml`), and exclusion rules.
- **CI / CD Pipeline:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) executes linting, test coverage verification, and SonarQube quality checks on every push and pull request.

## License

MIT — see `LICENSE`.
