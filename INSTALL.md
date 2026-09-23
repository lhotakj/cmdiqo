# ShangoHub Installation & Operator's Guide

A beginner-friendly, step-by-step walkthrough for deploying **shango-bot** and installing **shango-agent** as a background service on Windows and Linux.

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
│     shango-bot (Docker)        │  ◄── Central Hub & Slack Bot
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
3. Set **App Name** to `shango-bot` and choose your Slack workspace.
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
   - `commands` — allows the bot to handle `/lan` and `/home` slash commands.
   - `chat:write` — allows the bot to post replies in channels and threads.
   - `channels:history` — allows the bot to read messages in public channels.
   - `groups:history` — (optional) allows reading messages in private channels.

---

### Step 1.4: Create the Slash Commands
1. In the left sidebar, click **Slash Commands** (under *Features*).
2. Click **Create New Command** and create the first command:
   - **Command:** `/lan`
   - **Short Description:** `Manage local network and agents`
   - **Usage Hint:** `devices list | agents list`
   - *(Leave Request URL blank — Socket Mode handles routing automatically)*
   - Click **Save**.
3. Click **Create New Command** again for the second command:
   - **Command:** `/home`
   - **Short Description:** `Run host and agent commands`
   - **Usage Hint:** `docker restart <service> | win updates <pc>`
   - Click **Save**.

---

### Step 1.5: Enable Message Events
1. In the left sidebar, click **Event Subscriptions** (under *Features*).
2. Toggle **Enable Events** to **On**.
3. Expand **Subscribe to bot events** and click **Add Bot User Event**.
4. Add:
   - `message.channels` — allows the bot to reply to plain text messages in channels.
5. Click **Save Changes** at the bottom of the page.

---

### Step 1.6: Install App to Workspace & Get `SLACK_BOT_TOKEN`
1. In the left sidebar, click **Install App** (or back to *OAuth & Permissions*).
2. Click **Install to Workspace** and then click **Allow**.
3. Under *OAuth Tokens for Your Workspace*, copy the **Bot User OAuth Token** starting with `xoxb-...`.
   **Save this as your `SLACK_BOT_TOKEN`**.

---

### Step 1.7: Find Your Slack User ID (For Security)
To ensure only **you** can execute commands, find your Slack User ID:
1. Open the Slack desktop app or browser.
2. Click your profile picture in the bottom-left / top-right corner → click **Profile**.
3. Click the **three dots (`...`)** button on your profile card.
4. Click **Copy member ID** (it looks like `U0123ABCDEF`).
5. Save this ID. Anyone whose ID is not on this list will be rejected by the bot.

---

### Step 1.8: Invite the Bot to a Channel
In any Slack channel where you want to interact with the bot (e.g., `#devops` or `#admin`):
```text
/invite @shango-bot
```

---

## 2. Where to Keep Secrets & Passwords

Never store tokens or passwords in Git repositories, Dockerfiles, or Slack chat. Here is where secrets must live on each system:

| Component | Secret File Location | File Permissions | What Goes Inside |
|---|---|---|---|
| **Bot Server** | `/path/to/ShangoHub/.env` | `chmod 600 .env` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `AGENT_TOKEN`, `ALLOWED_USERS` |
| **Linux Agent** | `/etc/shango-agent/agent.env` | `chmod 600` (owned by `root:root`) | `HUB_URL`, `AGENT_TOKEN`, `AGENT_NAME` |
| **Windows Agent** | `C:\ProgramData\ShangoHub\Agent\config.json` | Protected NTFS ACL (SYSTEM & Administrators only) | `hub_url`, `token`, `name` |

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

### Step 3.1: Clone Repository & Create `.env`
On your server:
```bash
git clone https://github.com/your-username/ShangoHub.git
cd ShangoHub
```

Create a `.env` file next to `docker-compose.yml`:
```bash
nano .env
```

Paste your tokens and configuration into `.env`:
```bash
# Slack Tokens (from Section 1)
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here

# Security: Comma-separated Slack member IDs allowed to run commands
ALLOWED_USERS=U0123ABCDEF

# Shared Secret for Agents (Must match on all agent machines)
AGENT_TOKEN=your-random-agent-token-here

# Hub Networking
HUB_PORT=8765
HUB_ENABLED=true

# Timeouts & Logging
SHELL_TIMEOUT=120
LOG_LEVEL=INFO
```

Lock down the file permissions so other system users cannot read it:
```bash
chmod 600 .env
```

---

### Step 3.2: Start the Bot Container
Start the container in the background:
```bash
docker compose up -d --build
```

Check the live logs to verify that Socket Mode connected:
```bash
docker compose logs -f shango-bot
```

You should see:
```text
INFO [shango-bot] Loaded 6 command(s) from /app/config/commands.yaml
INFO [shango-bot.hub] Hub listening on ws://0.0.0.0:8765 (agents connect outbound to this)
INFO [shango-bot] Starting Socket Mode handler (outbound WebSocket only - NAT friendly)
```

---

### Step 3.3: Test in Slack
In your Slack channel:
```text
/lan
```
The bot should reply with the list of available commands.

---

## 4. How to Install the Linux Service (`shango-agent`)

Install `shango-agent` on your Linux machines (e.g., Raspberry Pi, Ubuntu NAS, home server) so you can execute shell commands on them remotely from Slack.

### Step 4.1: Download or Build the Binary
You can either:
- **Download from GitHub Actions:** Go to your GitHub repo → **Actions** → click the latest **Build Agent Binaries** run → download `shango-agent-linux-x64` under *Artifacts*.
- **Or copy from repository:** Use `dist/shango-agent` (if built locally) or copy the `agent/` folder.

Place the binary or source on the target Linux machine:
```bash
# Example: transferring to your target Linux host
scp dist/shango-agent user@192.168.1.50:/tmp/
```

---

### Step 4.2: Run the Automated Installer
On the target Linux machine:
```bash
cd /tmp
chmod +x shango-agent
# Move to ShangoHub repository clone or run installer:
sudo ./agent/install-service.sh
```

The script will:
1. Place the binary at `/usr/local/bin/shango-agent`.
2. Create `/etc/shango-agent/agent.env`.
3. Install and enable the systemd service `/etc/systemd/system/shango-agent.service`.

---

### Step 4.3: Configure the Linux Agent Secret
Edit the agent's secret configuration file:
```bash
sudo nano /etc/shango-agent/agent.env
```

Fill in your server's LAN IP and the shared secret:
```bash
# IP of the machine running shango-bot Docker container
HUB_URL=ws://192.168.1.10:8765

# Must match AGENT_TOKEN in your bot server's .env
AGENT_TOKEN=your-random-agent-token-here

# Unique name for this agent (used in Slack commands like /home nas disk)
AGENT_NAME=nas
LOG_LEVEL=INFO
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

Restart the service to load the new config:
```bash
sudo systemctl restart shango-agent
```

---

### Step 4.4: Verify the Linux Agent
Check the service status:
```bash
sudo systemctl status shango-agent
```
It should say `active (running)`.

View live streaming connection logs:
```bash
sudo journalctl -u shango-agent -f
```
You should see:
```text
INFO [shango-agent] Connected to ws://192.168.1.10:8765 as 'nas'
```

---

## 5. How to Install the Windows Service (`shango-agent.exe`)

Install `shango-agent.exe` on Windows 10/11 or Windows Server so you can run PowerShell commands (`ps1`) or batch commands remotely from Slack.

### Step 5.1: Download the Windows Binary
1. Go to your GitHub repository in your web browser.
2. Click the **Actions** tab.
3. Click on the latest run named **Build Agent Binaries (Windows & Linux x64)**.
4. Scroll to the bottom to **Artifacts** and click **`shango-agent-windows-x64`**.
5. Extract the downloaded `.zip` file to get `shango-agent.exe` (and optionally `shango-agent.cer`).

---

### Step 5.2: Digital Signature, SmartScreen & Safety Verification

> [!TIP]
> **Why does Windows show "Windows protected your PC"?**
> When you download any newly compiled `.exe` from the web, Microsoft Defender SmartScreen flags it because it hasn't gathered download reputation on Microsoft servers yet.

To verify and ensure the `.exe` is authentic and safe:

1. **Check the Embedded Digital Signature**:
   - Right-click `shango-agent.exe` $\rightarrow$ **Properties** $\rightarrow$ **Digital Signatures** tab.
   - You will see the signature from `ShangoHub (GitHub Actions)` with a secure SHA256 digest and official timestamp.
2. **Check Publisher Metadata**:
   - In **Properties** $\rightarrow$ **Details**, you will see:
     - *File Description*: ShangoHub LAN Remote Agent
     - *Company*: ShangoHub
     - *Original Filename*: shango-agent.exe
3. **Verify Cryptographic GitHub Attestation (Sigstore Provenance)**:
   GitHub automatically generates a cryptographic attestation for the binary. You can verify it in PowerShell with the GitHub CLI:
   ```bash
   gh attestation verify shango-agent.exe --owner your-github-username
   ```
   GitHub CLI will confirm: `✓ Verification succeeded! Built by GitHub Actions on your repository`.
4. **Permanent SmartScreen Trust**:
   When you run `install-service.ps1` as Administrator (below), it **automatically imports the digital certificate into your Windows Trusted Publishers store**, permanently silencing SmartScreen warnings on that machine.

---

### Step 5.3: Run the PowerShell Installer as Administrator
1. Press the **Windows Key**, type **PowerShell**, right-click **Windows PowerShell**, and select **Run as Administrator**.
2. Navigate to the folder containing `install-service.ps1` and `shango-agent.exe`:
   ```powershell
   cd C:\Users\YourUser\Downloads\ShangoHub\agent
   ```
3. Run the installer script, supplying your server IP, token, and a name for this PC:
   ```powershell
   .\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "your-random-agent-token-here" -Name "MY-WINDOWS-PC"
   ```

#### What the installer does automatically:
- Copies `shango-agent.exe` into `C:\Program Files\ShangoHub\Agent\`.
- Writes secret configuration to `C:\ProgramData\ShangoHub\Agent\config.json` with restricted access permissions.
- Registers an automatic background service named `Shango-Agent` using Windows Task Scheduler (configured to run on computer boot as `NT AUTHORITY\SYSTEM` with highest privileges, surviving user logoffs).
- Starts the service immediately.

---

### Step 5.3: Verify the Windows Agent
In your Administrator PowerShell window, check that the service task is running:
```powershell
Get-ScheduledTask -TaskName "Shango-Agent"
```
The `State` should be `Running`.

#### Service Management Commands on Windows:
```powershell
# Stop the agent:
Stop-ScheduledTask -TaskName "Shango-Agent"

# Start the agent:
Start-ScheduledTask -TaskName "Shango-Agent"

# Re-configure (edit the config file):
notepad "C:\ProgramData\ShangoHub\Agent\config.json"
# (Then restart the service)

# Uninstall the service completely:
Unregister-ScheduledTask -TaskName "Shango-Agent" -Confirm:$false
```

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

3. **Test Remote Command Execution:**
   - On Linux: `/home nas disk` (runs `df -h` on the NAS and returns storage usage).
   - On Windows: `/home win updates MY-WINDOWS-PC` (queries Windows hotfixes).

---

### 6.2 Check Server-Side Health (Docker)

On your bot server:
```bash
# 1. Check if the container is running and healthy
docker compose ps

# 2. Inspect live container logs
docker compose logs -f shango-bot

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

## 7. Troubleshooting Guide (Common Pitfalls)

| Symptom / Error in Slack | Cause | Fix |
|---|---|---|
| `:no_entry_sign: You are not authorized to use this bot.` | Your Slack Member ID is not listed in `ALLOWED_USERS`. | Follow **Step 1.7** to copy your member ID (e.g. `U0123ABCDEF`). Add it to `.env` as `ALLOWED_USERS=U0123ABCDEF` and run `docker compose restart`. |
| `dispatch_failed` or `No command found` | Slash commands `/lan` or `/home` were not created or the app wasn't reinstalled. | In [api.slack.com/apps](https://api.slack.com/apps), verify both commands exist under **Slash Commands**, then go to **Install App** and click **Reinstall to Workspace**. |
| `agent 'nas' is not connected. Connected agents: (none)` | Agent is offline, has the wrong IP, or token mismatch. | 1. Run `/lan agents list` to see what agents are online.<br/>2. Check `HUB_URL` in `/etc/shango-agent/agent.env` or `config.json`.<br/>3. Verify `AGENT_TOKEN` matches **exactly** on server and agent. |
| Agent connects but disconnects every 30 seconds. | Stateful NAT / firewall dropping idle connection. | ShangoHub includes automated 20-second ping/pong keepalives. Ensure your firewall allows outbound WebSockets on port 8765. |
| `/lan devices list` returns `No devices discovered` | Container running in bridge mode instead of host mode. | In `docker-compose.yml`, verify `network_mode: host` is set. Raw ARP packets require host network access. |
| Windows agent command fails with execution policy error. | PowerShell script execution disabled. | `shango-agent` automatically calls PowerShell with `-NoProfile -NonInteractive -Command`, bypassing execution policy restrictions. |
