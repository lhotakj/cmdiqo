# Cmdiqo Installation & Operator's Guide

A beginner-friendly, step-by-step walkthrough for deploying **cmdiqo-bot** and installing **cmdiqo-agent** as a background service on Windows and Linux.

---

## Architecture at a Glance

Before configuring anything, here is how the parts talk to each other:

```
┌────────────────────────────────┐
│           Slack Cloud          │
└──────────────┬─────────────────┘
               │  ▲  (Outbound WebSocket - Socket Mode)
               │  │  No open internet ports needed!
┌──────────────▼──┴──────────────┐
│     cmdiqo-bot (Docker)        │  ◄── Central Hub & Slack Bot
│     Listens on port :8765      │
└──────────────┬─────────────────┘
               │  ▲  (Internal LAN WebSockets)
               │  │  Agents dial OUT to the Hub!
    ┌──────────┴──┴──────────┐
    │                        │
┌───▼────────────────┐   ┌───▼────────────────┐
│   Linux Agent      │   │   Windows Agent    │
│  (Systemd Service) │   │ (Windows Service)  │
└────────────────────┘   └────────────────────┘
```

> [!NOTE]
> - **Behind NAT / No Port Forwarding:** Neither your server nor your LAN machines need public IP addresses, router port-forwarding, or incoming firewall rules to the internet.
> - **Internal Hub Port (`8765`):** The bot container listens on port `8765` on your **local home/office network**. Your Windows PCs and Linux servers only need to reach `ws://<server-lan-ip>:8765`.

---

## 1. How to Configure Slack (Click-by-Click)

You will need a Slack workspace where you have permission to install apps (or your own free personal workspace).

### Step 1.1: Create the App
1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
2. Select **From scratch**.
3. Set **App Name** to `cmdiqo-bot` and choose your Slack workspace.
4. Click **Create App**.

---

### Step 1.2: Enable Socket Mode & Get `SLACK_APP_TOKEN`
Socket Mode allows the bot to receive Slack events over a secure outbound WebSocket—no public URLs or reverse proxies required.

1. In the left sidebar, click **Socket Mode** (under *Settings*).
2. Toggle the switch to **Enable Socket Mode**.
3. A popup titled *Generate an app-level token* will appear:
   - **Token Name:** `socket-mode-token`
   - **Scope:** Ensure `connections:write` is checked.
4. Click **Generate**.
5. Copy the token starting with `xapp-...`. **Save this as your `SLACK_APP_TOKEN`**.

---

### Step 1.3: Set Permissions (OAuth Scopes)
1. In the left sidebar, click **OAuth & Permissions** (under *Features*).
2. Scroll down to **Scopes** → **Bot Token Scopes** and click **Add an OAuth Scope** for each of these:
   - `commands` — allows the bot to handle `/help`, `/lan`, `/host`, `/home`, and `/wol` slash commands.
   - `chat:write` — allows the bot to post replies and agent connection notifications in channels.
   - `channels:read` — allows auto-discovering public channels for agent notifications (`users.conversations` / `conversations.list`).
   - `groups:read` — allows auto-discovering private channels where the bot is invited.
   - `channels:join` — allows the bot to auto-join public channels to send notifications.
   - `channels:history` — allows the bot to read messages in public channels.
   - `groups:history` — (optional) allows reading messages in private channels.
   - `im:history` — allows the bot to read 1-on-1 direct messages.
   - `im:write` — allows the bot to send 1-on-1 direct messages.

> **Note**: After adding or updating scopes, scroll up and click **Reinstall to Workspace** to apply the new permissions to your Slack workspace.

---

### Step 1.4: Create the Slash Commands
1. In the left sidebar, click **Slash Commands** (under *Features*).
2. Click **Create New Command** for each of the following:
   - **Command:** `/help`
     - **Short Description:** `Interactive manual and command reference`
     - **Usage Hint:** `[category|topic] (e.g. /help lan, /help host)`
   - **Command:** `/lan`
     - **Short Description:** `Manage local network and agents`
     - **Usage Hint:** `devices list | wake <mac> | ping <host> | agents list`
   - **Command:** `/host`
     - **Short Description:** `Run host and remote agent commands`
     - **Usage Hint:** `<hostname-or-ip> <action>`
   - **Command:** `/home` *(Alias)*
     - **Short Description:** `Shortcut to /host home`
     - **Usage Hint:** `docker ps | docker restart <service>`
   - **Command:** `/wol` *(Alias)*
     - **Short Description:** `Wake-on-LAN boot shortcut`
     - **Usage Hint:** `<mac-or-ip>`
   - **Optional Aliases:** `/blockapp`, `/appblock`
     - **Short Description:** `Shortcut for application execution blocker`
     - **Usage Hint:** `<host> <app|all> <on|off|status>`
   *(Leave Request URL blank for all commands — Socket Mode handles routing automatically)*

---

### Step 1.5: Enable Message Events
1. In the left sidebar, click **Event Subscriptions** (under *Features*).
2. Toggle **Enable Events** to **On**.
3. Expand **Subscribe to bot events** and click **Add Bot User Event**.
4. Add:
   - `message.channels` — allows the bot to reply to messages in public channels.
   - `message.im` — allows the bot to reply to 1-on-1 direct messages.
5. Click **Save Changes** at the bottom of the page.

---

### Step 1.6: Enable Messages Tab in App Home (Direct Messaging)

> [!IMPORTANT]
> If you skip this step, Slack will show: **"Sending messages to this app has been turned off"** when you click on the bot under Apps.

1. In the left sidebar, click **App Home** (under *Features*).
2. Scroll down to the **Show Tabs** section:
   - Ensure the **Messages Tab** toggle is turned **ON**.
   - Check the checkbox: **"Allow users to send Slash commands and messages from the messages tab"**.

---

### Step 1.7: Install (or Reinstall) App to Workspace & Get `SLACK_BOT_TOKEN`
1. In the left sidebar, click **Install App** (under *Settings*).
2. Click **Install to Workspace** (or **Reinstall to Workspace** if updating scopes) and then click **Allow**.
3. Under *OAuth Tokens for Your Workspace*, copy the **Bot User OAuth Token** starting with `xoxb-...`.
   **Save this as your `SLACK_BOT_TOKEN`**.

---

### Step 1.8: Find Your Slack User ID (For Security)
To ensure only **you** can execute commands, find your Slack User ID:
1. Open the Slack desktop app or browser.
2. Click your profile picture in the bottom-left / top-right corner → click **Profile**.
3. Click the **three dots (`...`)** button on your profile card.
4. Click **Copy member ID** (it looks like `U0123ABCDEF`).
5. Save this ID. Anyone whose ID is not on this list will be rejected by the bot.

---

### Step 1.9: Interact with the Bot
You can interact with the bot in two ways:
1. **Direct Message / App Home:**
   Click **cmdiqo-bot** under the **Apps** section in Slack and type `/help` or `/lan`.
2. **In any Channel:**
   Invite the bot to a channel (e.g. `#devops` or `#admin`):
   ```text
   /invite @cmdiqo-bot
   ```
   Then type slash commands like `/lan`, `/host`, or `/help`.

---

## 2. Where to Keep Secrets & Passwords

Never store tokens or passwords in Git repositories, Dockerfiles, or Slack chat. Here is where secrets must live on each system:

| Component | Secret File Location | File Permissions | What Goes Inside |
|---|---|---|---|
| **Bot Server** | `/path/to/Cmdiqo/.env` | `chmod 600 .env` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `AGENT_TOKEN`, `ALLOWED_USERS` |
| **Linux Agent** | `/etc/cmdiqo-agent/agent.env` | `chmod 600` (owned by `root:root`) | `HUB_URL`, `AGENT_TOKEN`, `AGENT_NAME` |
| **Windows Agent** | `C:\ProgramData\Cmdiqo\Agent\config.json` | Config & secrets (Full edit access for Admins & Users) | `hub_url`, `token`, `name` |

### Generate a Strong `AGENT_TOKEN`
The `AGENT_TOKEN` is a shared secret between your bot and your agents. Generate a secure random string:

- **On Linux / macOS:**
  ```bash
  openssl rand -hex 24
  ```
- **On Windows (PowerShell):**
  ```powershell
  -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | ForEach-Object {[char]$_})
  ```

---

## 3. How to Run the Bot (Server Deployment)

### Prerequisites
- A Linux or Windows machine running **Docker** and **Docker Compose**.
- Recommended: Run with `network_mode: host` on Linux so `arp-scan` can inspect your physical LAN subnet.

---

### Option A: Standalone Docker Compose Deployment (Recommended)

You do not need to clone the entire development repository to run the bot. You can deploy it using the prebuilt official image `lhotakj/cmdiqo-bot:latest`:

1. Create a deployment directory on your server:
   ```bash
   mkdir -p ~/cmdiqo/config
   cd ~/cmdiqo
   ```

2. Create your `docker-compose.yml` file:
   ```yaml
   services:
     cmdiqo-bot:
       image: lhotakj/cmdiqo-bot:latest
       container_name: cmdiqo-bot
       restart: unless-stopped

       # Host networking required for arp-scan LAN device discovery:
       network_mode: host

       # Only used if switching to bridge networking:
       ports:
         - "${HUB_PORT:-8765}:8765"

       environment:
         SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN:?set SLACK_BOT_TOKEN in .env}
         SLACK_APP_TOKEN: ${SLACK_APP_TOKEN:?set SLACK_APP_TOKEN in .env}
         # Restrict to specific Slack user IDs (comma-separated):
         ALLOWED_USERS: ${ALLOWED_USERS:-}
         # Restrict to specific Slack channels:
         # ALLOWED_CHANNELS: "C0123CHANNEL"
         # Shared secret for remote LAN agents:
         AGENT_TOKEN: ${AGENT_TOKEN:-}
         HUB_PORT: "8765"
         HUB_ENABLED: "true"
         SHELL_TIMEOUT: "120"
         LOG_LEVEL: "INFO"

       volumes:
         # Manage host Docker daemon:
         - /var/run/docker.sock:/var/run/docker.sock
         # Host compose projects directory (optional):
         - /docker:/docker
         # Map custom commands config:
         - ./config:/config:ro
   ```

3. Create `.env` alongside your `docker-compose.yml`:
   ```bash
   nano .env
   ```
   ```bash
   # Slack Tokens (from Section 1)
   SLACK_BOT_TOKEN=xoxb-your-bot-token-here
   SLACK_APP_TOKEN=xapp-your-app-token-here

   # Security: Comma-separated Slack member IDs allowed to run commands
   ALLOWED_USERS=U0123ABCDEF

   # Shared Secret for Agents (Must match on all agent machines)
   AGENT_TOKEN=your-random-agent-token-here
   ```
   ```bash
   chmod 600 .env
   ```

4. *(Optional)* Provide your own `config/commands.yaml` inside `./config/commands.yaml`. If omitted, the container automatically uses its built-in defaults.

5. Start the bot:
   ```bash
   docker compose up -d
   ```

---

### Option B: From Repository Source

If you cloned the source repository:
```bash
git clone https://github.com/lhotakj/cmdiqo.git
cd cmdiqo
cp .env.example .env
nano .env   # configure tokens
chmod 600 .env
```

Start using the compose file in `docker/`:
```bash
docker compose -f docker/docker-compose.yml up -d
```
*(Or build locally from source)*:
```bash
docker compose -f docker/docker-compose.yml up -d --build
```

---

### Step 3.2: Verify Container Logs
Check the live container logs to verify that Socket Mode connected:
```bash
docker compose logs -f cmdiqo-bot
```

You should see:
```text
INFO [cmdiqo-bot] Loaded 22 command(s) from /config/commands.yaml
INFO [cmdiqo-bot] Loaded 2 alias(es) from /config/commands.yaml
INFO [cmdiqo-bot.hub] Hub listening on ws://0.0.0.0:8765 (agents connect outbound to this)
INFO [cmdiqo-bot] Starting Socket Mode handler (outbound WebSocket only - NAT friendly)
```

---

### Step 3.3: Test in Slack
In your Slack channel:
```text
/lan
```
The bot should reply with the list of available commands.

---

### Step 3.4: Local Command Testing Without Slack (`slacksim`)

If you want to verify your commands or debug agent connections before configuring Slack credentials or Socket Mode, run the built-in Slack simulator:

```bash
# Launch interactive Slack console emulator:
python run.py sim
# or:
python -m slacksim
# or:
python slacksim/slacksim.py
```

Type any slash command directly at the prompt:
```text
slack [#general] @U_ADMIN > /help
slack [#general] @U_ADMIN > /lan agents list
slack [#general] @U_ADMIN > /host AMD uptime
```
`slacksim` renders Slack mrkdwn, emojis, and code blocks in your terminal while hosting the WebSocket Hub on port `8765`, allowing connected agents to execute live commands without needing `SLACK_BOT_TOKEN` or `SLACK_APP_TOKEN`. Use **Up / Down arrows** for command history navigation, and press **Esc** to instantly clear/delete the current message draft.

---

## 4. How to Install the Linux Service (`cmdiqo-agent`)

Install `cmdiqo-agent` on your Linux machines (e.g., Raspberry Pi, Ubuntu NAS, home server) so you can execute shell commands on them remotely from Slack.

### Step 4.1: Download or Build the Linux Agent Package
Download `cmdiqo-agent-linux-x64.zip` from the GitHub Release or Actions
artifact, or build it locally with `./build.sh --agent`. Copy the ZIP to
the target Linux machine:
```bash
scp dist/cmdiqo-agent-linux-x64.zip user@192.168.1.50:/tmp/
```

---

### Step 4.2: Run the Automated Installer
On the target Linux machine:
```bash
cd /tmp
unzip cmdiqo-agent-linux-x64.zip -d cmdiqo-agent-linux-x64
cd cmdiqo-agent-linux-x64
sudo ./install.sh
```

The script will:
1. Place the binary at `/usr/local/bin/cmdiqo-agent`.
2. Create `/etc/cmdiqo-agent/agent.env`.
3. Install and enable the systemd service `/etc/systemd/system/cmdiqo-agent.service`.

> [!TIP]
> **Alternative: Native C++ Setup Application (`cmdiqo-agent-linux-x64-setup`)**:
> If you built or downloaded the native setup bundle, you can launch the GUI wizard or run headlessly:
> ```bash
> # Graphical setup wizard (requires X11 / Wayland desktop):
> ./cmdiqo-agent-linux-x64-setup
> 
> # Headless CLI installation:
> ./cmdiqo-agent-linux-x64-setup --cli install --hub-url "ws://192.168.1.10:8765" --token "your-agent-token" --name "nas"
> ```

---

### Step 4.3: Configure the Linux Agent Secret
Edit the agent's secret configuration file:
```bash
sudo nano /etc/cmdiqo-agent/agent.env
```

Fill in your server's LAN IP and the shared secret:
```bash
# IP of the machine running cmdiqo-bot Docker container
HUB_URL=ws://192.168.1.10:8765

# Must match AGENT_TOKEN in your bot server's .env
AGENT_TOKEN=your-random-agent-token-here

# Unique name for this agent (used in Slack commands like /host nas disk)
AGENT_NAME=nas
LOG_LEVEL=INFO
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

Restart the service to load the new config:
```bash
sudo systemctl restart cmdiqo-agent
```

---

### Step 4.4: Verify the Linux Agent
Check the service status:
```bash
sudo systemctl status cmdiqo-agent
```
It should say `active (running)`.

View live streaming connection logs:
```bash
sudo journalctl -u cmdiqo-agent -f
```
You should see:
```text
INFO [cmdiqo-agent] Connected to ws://192.168.1.10:8765 as 'nas'
```

> [!NOTE]
> **Super-Lightweight Resource Footprint:** The background `cmdiqo-agent` systemd daemon consumes only **~1.1 MB RAM**, making it completely negligible on servers, NAS appliances, and Raspberry Pis.

---

## 5. How to Install the Windows Service (`cmdiqo-agent.exe`)

Install `cmdiqo-agent.exe` on Windows 10/11 or Windows Server so you can run PowerShell commands (`ps1`) or batch commands remotely from Slack.

### Step 5.1: Download the Windows Binary
1. Go to your GitHub repository in your web browser.
2. Click the **Actions** tab.
3. Click on the latest run named **Build Agent Binaries (Windows & Linux x64)**.
4. Scroll to the bottom to **Artifacts** and click **`cmdiqo-agent-windows-x64`**.
5. Extract the downloaded `.zip` file to get `cmdiqo-agent.exe` (and optionally `cmdiqo-agent.cer`).

---

### Step 5.2: Digital Signature, SmartScreen & Safety Verification

> [!TIP]
> **Why does Windows show "Windows protected your PC"?**
> When you download any newly compiled `.exe` from the web, Microsoft Defender SmartScreen flags it because it hasn't gathered download reputation on Microsoft servers yet.

To verify and ensure the `.exe` is authentic and safe:

1. **Check the Embedded Digital Signature**:
   - Right-click `cmdiqo-agent.exe` $\rightarrow$ **Properties** $\rightarrow$ **Digital Signatures** tab.
   - You will see the signature from `Cmdiqo (GitHub Actions)` with a secure SHA256 digest and official timestamp.
2. **Check Publisher Metadata**:
   - In **Properties** $\rightarrow$ **Details**, you will see:
     - *File Description*: Cmdiqo LAN Remote Agent
     - *Company*: Cmdiqo
     - *Original Filename*: cmdiqo-agent.exe
3. **Permanent SmartScreen Trust**:
   When you run `install-service.ps1` as Administrator (below), it **automatically imports the digital certificate into your Windows Trusted Publishers store**, permanently silencing SmartScreen warnings on that machine.

---

### Step 5.3: Install the Windows Service as Administrator
1. Press the **Windows Key**, type **PowerShell**, right-click **Windows PowerShell**, and select **Run as Administrator**.
2. Extract `cmdiqo-agent-windows-x64.zip` to a local directory and navigate
   to it:
   ```powershell
   Expand-Archive "$HOME\Downloads\cmdiqo-agent-windows-x64.zip" "$HOME\Downloads\cmdiqo-agent"
   Set-Location "$HOME\Downloads\cmdiqo-agent"
   ```
3. Run the installer script, supplying your server IP, token, and a name for this PC:
   ```powershell
   .\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "your-random-agent-token-here" -Name "MY-WINDOWS-PC"
   ```

> [!TIP]
> The ZIP contains `cmdiqo-agent.exe` beside `install-service.ps1`, so the installer
> detects it automatically. You can also specify
> `-BinaryPath "C:\path\to\cmdiqo-agent.exe"` if needed.
>
> **Alternative: Native Windows Setup Application (`cmdiqo-agent-windows-x64-setup.exe`)**:
> You can also launch the native graphical setup wizard directly (self-contained executable with embedded agent payload, taking only **~5.2 MB RAM**):
> - Double-click `cmdiqo-agent-windows-x64-setup.exe`.
> - Configure your Hub URL, Agent Token, and Machine Name.
> - Click **Install Service** (runs with standard Windows UAC elevation).

#### What the installer sets up automatically:
- **Real Windows Service (`CmdiqoAgent`)**: Registered directly in Windows Service Control Manager (SCM). Visible in `services.msc`.
- **Native C++ Execution Engine**: High-performance native service dispatcher invoked via `cmdiqo-agent.exe --service` (super-lightweight background memory footprint of only **~3.3 MB RAM**, Win32 Job Objects for instant process tree isolation).
- **Accessible Config in `C:\ProgramData\Cmdiqo\Agent\config.json`**: Grants Full Control to Administrators and the current user (plus Modify to Users) so you can freely view and edit settings in Notepad, VS Code, or any shell without UAC access issues.
- **Automatic Self-Healing**: Configures SCM failure recovery to automatically restart the service if it ever terminates.
- **Log Redirection**: Redirects stdout/stderr to `C:\ProgramData\Cmdiqo\Agent\logs\service.log`.
- **Strips Mark-of-the-Web & Trusts Certificate**: Removes download flags and imports the Authenticode certificate into machine stores.

---

### Step 5.4: Verify & Manage the Windows Service
In your Administrator PowerShell window, check that the Windows Service is running:
```powershell
Get-Service -Name "CmdiqoAgent"
```
The `Status` should be `Running`:
```text
Status   Name               DisplayName
------   ----               -----------
Running  CmdiqoAgent        Cmdiqo LAN Remote Agent
```

#### Native Windows Service Management Commands:
```powershell
# Check service status:
Get-Service CmdiqoAgent

# Restart service:
Restart-Service CmdiqoAgent

# Stop service:
Stop-Service CmdiqoAgent

# Start service:
Start-Service CmdiqoAgent

# View live service logs:
Get-Content "C:\ProgramData\Cmdiqo\Agent\logs\service.log" -Tail 20 -Wait

# Re-configure (edit config file):
notepad "C:\ProgramData\Cmdiqo\Agent\config.json"
Restart-Service CmdiqoAgent
```

---

### Step 5.5: Uninstall the Windows Service

To cleanly stop, unregister, and remove the Windows Service:

```powershell
# Run in Administrator PowerShell from the extracted agent package:
Set-Location "$HOME\Downloads\cmdiqo-agent"

# Standard uninstall (stops service, removes SCM registration & binaries, preserves config for upgrades):
.\uninstall.ps1

# Complete purge (removes service, binaries, AND deletes config/secrets in C:\ProgramData\Cmdiqo\Agent):
.\uninstall.ps1 -PurgeConfig
```

*(You can also run `.\install.ps1 -Uninstall` for the same result).*

---

## 6. How to Debug & Check Bot Health

### 6.1 Check Health from Slack (The Quickest Way)
Once the bot and agents are running, test everything directly inside Slack:

1. **Verify Connected Agents:**
   Type:
   ```text
   /lan agents list
   ```
   **Expected output:** A table listing every online machine, its operating system, and connect time:
   ```text
   AGENT          PLATFORM        CONNECTED SINCE      ADDRESS
   MY-WINDOWS-PC  Windows/11      2026-09-23 21:05:12  192.168.1.15:58392
   nas            Linux/6.1.0     2026-09-23 21:02:44  192.168.1.50:41208
   ```
   *(If an agent is missing from this table, it is not connected to the hub).*

2. **Test LAN Scanner:**
   Type:
   ```text
   /lan devices list
   ```
   The bot performs an ARP scan and prints all discovered MAC addresses and vendor names on your subnet.

3. **Test Wake-on-LAN (Boot Over LAN):**
   Type:
   ```text
   /lan wake 00:11:22:33:44:55
   # or with alias:
   /wol 00:11:22:33:44:55
   ```
   The bot broadcasts a magic UDP packet on port 9 to boot the target machine.

4. **Test Remote Command Execution:**
   - **Cross-Platform Commands (work on Windows & Linux):**
     - System uptime & boot time: `/host MY-WINDOWS-PC uptime` or `/host nas uptime`
     - Physical memory: `/host MY-WINDOWS-PC memory` or `/host nas memory`
     - Disk & drive usage: `/host MY-WINDOWS-PC disk` or `/host nas disk`
     - Top CPU-consuming processes: `/host MY-WINDOWS-PC top` or `/host nas top`
     - Remote machine reboot: `/host MY-WINDOWS-PC reboot` or `/host nas reboot`
     - Active system services: `/host MY-WINDOWS-PC services` or `/host nas services`
     - Service status & restart: `/host MY-WINDOWS-PC service status Spooler` or `/host nas service restart nginx`
     - Parental Controls (YouTube, Roblox, Minecraft & Discord):
       - `/host MY-WINDOWS-PC parental on` (blocks YouTube, Roblox, Minecraft, and Discord in hosts file & flushes DNS).
       - `/host MY-WINDOWS-PC parental off` (unblocks domains & flushes DNS).
       - `/host MY-WINDOWS-PC parental status` (checks current block status).
     - Application Execution Blocker (Roblox, Steam, Epic Games, Discord, Minecraft, or all):
       - `/host MY-WINDOWS-PC blockapp roblox on` (or `/host MY-WINDOWS-PC appblock roblox on` — blocks executables & terminates running processes via IFEO on Windows / Flatpak overrides on Linux).
       - `/host MY-WINDOWS-PC blockapp steam off` (unblocks application execution).
       - `/host MY-WINDOWS-PC blockapp all status` (checks current execution interception status across all configured apps).
   - **Windows-Specific Hotfixes:**
     - `/host MY-WINDOWS-PC win updates` (queries installed Windows quality updates).
   - **Docker Operations (Linux Host):**
     - `/host home docker ps` (or shortcut alias `/home docker ps`).
     - `/host home docker restart plex` or `/host home docker logs plex`.

---

### 6.2 Check Server-Side Health (Docker)

On your bot server:
```bash
# 1. Check if the container is running and healthy
docker compose ps

# 2. Inspect live container logs
docker compose logs -f cmdiqo-bot

# 3. Check if the WebSocket hub port is listening on the host:
ss -tulpn | grep 8765
```

---

### 6.3 Test Network Connectivity from an Agent PC
If an agent fails to connect, verify that the agent PC can reach the bot container on port 8765:

- **From Windows (PowerShell):**
  ```powershell
  Test-NetConnection -ComputerName 192.168.1.10 -Port 8765
  ```
  Look for: `TcpTestSucceeded : True`.
- **From Linux:**
  ```bash
  nc -zv 192.168.1.10 8765
  # or
  curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" ws://192.168.1.10:8765
  ```

---

## 7. Common Issues and Troubleshooting

| Symptom / Error in Slack | Cause | Fix |
|---|---|---|
| `:no_entry_sign: You are not authorized to use this bot.` | Your Slack Member ID is not listed in `ALLOWED_USERS`. | Follow **Step 1.7** to copy your member ID (e.g. `U0123ABCDEF`). Add it to `.env` as `ALLOWED_USERS=U0123ABCDEF` and run `docker compose restart`. |
| `dispatch_failed` or `No command found` | Slash commands `/lan`, `/host`, or `/home` were not created or the app wasn't reinstalled. | In [api.slack.com/apps](https://api.slack.com/apps), verify both commands exist under **Slash Commands**, then go to **Install App** and click **Reinstall to Workspace**. |
| `agent 'nas' is not connected. Connected agents: (none)` | Agent is offline, has the wrong IP, or token mismatch. | 1. Run `/lan agents list` to see what agents are online.<br/>2. Check `HUB_URL` in `/etc/cmdiqo-agent/agent.env` or `config.json`.<br/>3. Verify `AGENT_TOKEN` matches **exactly** on server and agent. |
| Agent connects but disconnects every 30 seconds. | Stateful NAT / firewall dropping idle connection. | Cmdiqo includes automated 20-second ping/pong keepalives. Ensure your firewall allows outbound WebSockets on port 8765. |
| `/lan devices list` returns `No devices discovered` | Container running in bridge mode instead of host mode. | In `docker-compose.yml`, verify `network_mode: host` is set. Raw ARP packets require host network access. |
| Windows agent command fails with execution policy error. | PowerShell script execution disabled. | `cmdiqo-agent` automatically calls PowerShell with `-NoProfile -NonInteractive -Command`, bypassing execution policy restrictions. |
