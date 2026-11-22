# Cmdiqo on Android: Architecture, Implementation Plan & Root Guide

This document defines the architectural specification, implementation plan, and operational guide for deploying the **Cmdiqo Agent** onto rooted Android mobile devices. It is organized so engineering teams can implement and verify Android support in a subsequent phase without ambiguity.

---

## Table of Contents

1. [Executive Summary & Motivation](#1-executive-summary--motivation)
2. [Technical Feasibility & System Foundations](#2-technical-feasibility--system-foundations)
   - [Kernel & Root Privilege Model](#kernel--root-privilege-model)
   - [Bionic libc vs. glibc](#bionic-libc-vs-glibc)
   - [Userland Shell & Toybox Environment](#userland-shell--toybox-environment)
   - [Power Management & Android Doze Mode](#power-management--android-doze-mode)
   - [Dynamic Partitions & Magisk Systemless Hosts](#dynamic-partitions--magisk-systemless-hosts)
   - [Carrier NAT (CGNAT) & Outbound Architecture](#carrier-nat-cgnat--outbound-architecture)
3. [Android Root Command Registry (`commands.yaml`)](#3-android-root-command-registry-commandsyaml)
4. [Phase 1: Agent Core Enhancements for Android](#4-phase-1-agent-core-enhancements-for-android)
   - [Shell Discovery Adaptation (Python Agent)](#shell-discovery-adaptation-python-agent)
   - [Native C++ Agent NDK Toolchain & Fallback](#native-c-agent-ndk-toolchain--fallback)
5. [Phase 2: Deployment Target A — Termux Userland (Rapid Setup)](#5-phase-2-deployment-target-a--termux-userland-rapid-setup)
6. [Phase 3: Deployment Target B — Magisk Module (Headless System Daemon)](#6-phase-3-deployment-target-b--magisk-module-headless-system-daemon)
7. [Comprehensive Guide: How to Root a Xiaomi Phone (MIUI & HyperOS)](#7-comprehensive-guide-how-to-root-a-xiaomi-phone-miui--hyperos)
   - [Prerequisites & Account Requirements](#step-1-prerequisites--account-requirements)
   - [Unlocking the Bootloader](#step-2-unlocking-the-bootloader)
   - [Obtaining & Patching Stock Boot Image](#step-3-obtaining--patching-stock-boot-image)
   - [Flashing via Fastboot](#step-4-flashing-via-fastboot)
   - [Post-Root Configuration & Integrity Verification](#step-5-post-root-configuration--integrity-verification)
8. [Testing Matrix & Operational Safeguards](#8-testing-matrix--operational-safeguards)

---

## 1. Executive Summary & Motivation

Deploying the Cmdiqo Agent onto a rooted Android device transforms an ordinary mobile smartphone into a powerful, battery-backed edge management node. 

### Why Deploy Cmdiqo to Android?
- **Built-in Uninterruptible Power Supply (UPS):** A smartphone battery keeps the agent running during power outages.
- **Dual Network Redundancy:** Continuous connectivity switching between Wi-Fi and 4G/5G mobile cellular networks.
- **Deep Mobile Fleet Management:** Remote execution of root-level controls, including:
  - Instant screen locking and timeout overrides.
  - Silent application freezing (`pm suspend`) and unfreezing for parental control.
  - DNS-level content filtering via Magisk Systemless Hosts.
  - Radio toggles (`svc wifi`, `svc data`) and automated curfew cut-offs.
  - Hardware status telemetry (battery temperature, charging health, storage, memory).

---

## 2. Technical Feasibility & System Foundations

### Kernel & Root Privilege Model
- Android is built on the Linux kernel (typically 4.14, 5.4, 5.10, 5.15, or 6.1+).
- Desktop Linux uses `sudo` and `/etc/sudoers`. Android does not have `sudo` by default; root execution is mediated via the **`su`** binary (`/system/bin/su`, `/data/adb/ksu/bin/su`, or Magisk su).
- **SELinux:** Android enforces SELinux in `enforcing` mode by default. Unprivileged applications cannot execute root operations. When Cmdiqo runs via Magisk's `su` daemon (or as a Magisk `late_start` service), it runs in the permissive root domain (`u:r:magisk:s0` or `u:r:su:s0`), bypassing SELinux policy denials without needing to disable SELinux system-wide (`setenforce 0` is not required).

### Bionic libc vs. glibc
- Android does **not** use the GNU C Library (`glibc`) or `musl`; it uses Google's **Bionic libc**.
- Standard desktop Linux binaries (`x86_64` or `aarch64`) dynamically linked against `glibc` will fail on stock Android with missing linker errors (`/lib/ld-linux-aarch64.so.1: No such file or directory`).
- **Solutions:**
  1. *Python Agent (`agent/agent.py`):* Runs natively inside **Termux**, which provides a complete Bionic-compiled Python 3.12+ runtime and `websockets` package.
  2. *C++ Agent (`agent.c`):* Cross-compiled using the **Android NDK** (`aarch64-linux-android`) or statically linked against `musl` libc to produce a self-contained zero-dependency native binary.

### Userland Shell & Toybox Environment
- Stock Android uses **Toybox** (or mksh) at `/system/bin/sh`. It does **not** include GNU coreutils or `/bin/bash` in its base filesystem.
- Commands like `systemctl`, `journalctl`, `apt`, `yum`, and `netsh` do not exist. Instead, Android provides specialized CLI tools:
  - `pm` (Package Manager): install, uninstall, enable, disable, suspend apps.
  - `am` (Activity Manager): start activities, force-stop applications, broadcast intents.
  - `svc` (Service Control): control power, Wi-Fi, mobile data, and USB states.
  - `dumpsys`: dump system service states (battery, connectivity, telephony).
  - `input`: simulate user inputs, key events (e.g. keycode 26 for power button).

### Power Management & Android Doze Mode
- Android aggressively saves battery when the screen turns off through **Doze Mode** and **App Standby**, which suspends background execution and cuts off TCP socket activity.
- **Countermeasures:**
  1. *Termux:* Acquire a partial wake-lock via `termux-wake-lock` and whitelist Termux from battery optimization (`dumpsys deviceidle whitelist +com.termux`).
  2. *Magisk Daemon:* A background daemon launched from `/data/adb/modules/cmdiqo/service.sh` runs as an init-level root process outside the Android ART runtime. To maintain Wi-Fi responsiveness during deep sleep, acquire a kernel wake-lock:
     ```bash
     echo "cmdiqo_agent" > /sys/power/wake_lock
     ```

### Dynamic Partitions & Magisk Systemless Hosts
- Modern Android (Android 10+) mounts `/system` as read-only via dynamic partitions (`erofs` or `ext4`). You cannot directly edit `/system/etc/hosts`.
- Magisk provides **Systemless Hosts**: Magisk bind-mounts `/data/adb/modules/hosts/system/etc/hosts` over `/system/etc/hosts`. Writing to this path updates host routing instantly across the entire operating system without modifying disk partitions.

### Carrier NAT (CGNAT) & Outbound Architecture
- Mobile cellular connections (4G/5G) are placed behind Carrier-Grade NAT (CGNAT) with private, non-routable IPs.
- Cmdiqo's architecture is 100% outbound: the agent opens an outbound WebSocket connection (`wss://`) to the central Cmdiqo Bot/Hub. It requires **no public IP**, **no DDNS**, and **no port forwarding**.

---

## 3. Android Root Command Registry (`commands.yaml`)

Add the following command definitions to `commands.yaml` for Android hosts:

```yaml
# ==============================================================================
# Android Root Commands (for rooted mobile nodes)
# ==============================================================================
commands:
  # Telemetry & Status
  - id: android_battery
    name: "Battery & Thermal Health"
    description: "Read battery level, voltage, temperature, and charging status"
    kind: shell
    cmd: "dumpsys battery | grep -E 'level|status|health|temperature|voltage|powered'"

  - id: android_storage
    name: "Storage Usage"
    description: "Inspect free space on user data partition"
    kind: shell
    cmd: "df -h /data"

  - id: android_memory
    name: "Memory Info"
    description: "Check available RAM and swap/zRAM statistics"
    kind: shell
    cmd: "cat /proc/meminfo | grep -E 'MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree'"

  # Screen & Display Controls
  - id: android_lock_screen
    name: "Lock Screen"
    description: "Simulate power keypress to turn off display and lock device"
    kind: shell
    cmd: "input keyevent 26"

  - id: android_wake_screen
    name: "Wake Display"
    description: "Wake the screen"
    kind: shell
    cmd: "input keyevent 224"

  # Application Control & Parental Restrictions
  - id: android_suspend_pkg
    name: "Suspend Application"
    description: "Freezes app and grays out icon (pass target package name)"
    kind: shell
    cmd: "pm suspend ${pkg}"

  - id: android_unsuspend_pkg
    name: "Unsuspend Application"
    description: "Restores normal app usage"
    kind: shell
    cmd: "pm unsuspend ${pkg}"

  - id: android_force_stop
    name: "Force Stop App"
    description: "Immediately kills all background processes of an application"
    kind: shell
    cmd: "am force-stop ${pkg}"

  # Network & Parental Domain Blocking (Magisk Systemless Hosts)
  - id: android_block_domain
    name: "Block Domain (Systemless Hosts)"
    description: "Redirect domain to 0.0.0.0 via Magisk Systemless Hosts"
    kind: shell
    cmd: "echo '0.0.0.0 ${domain}' >> /data/adb/modules/hosts/system/etc/hosts"

  - id: android_toggle_wifi_off
    name: "Disable Wi-Fi"
    description: "Turns off Wi-Fi radio immediately"
    kind: shell
    cmd: "svc wifi disable"

  - id: android_toggle_wifi_on
    name: "Enable Wi-Fi"
    description: "Turns on Wi-Fi radio"
    kind: shell
    cmd: "svc wifi enable"

  - id: android_toggle_data_off
    name: "Disable Mobile Data"
    description: "Disables cellular data connection"
    kind: shell
    cmd: "svc data disable"

  - id: android_toggle_data_on
    name: "Enable Mobile Data"
    description: "Enables cellular data connection"
    kind: shell
    cmd: "svc data enable"

  # Power Management
  - id: android_reboot
    name: "Reboot Device"
    description: "Performs instant software reboot"
    kind: shell
    cmd: "reboot"

  - id: android_poweroff
    name: "Shut Down Phone"
    description: "Powers off device completely"
    kind: shell
    cmd: "reboot -p"
```

---

## 4. Phase 1: Agent Core Enhancements for Android

To enable native execution across both stock Android and Termux, two minor core updates should be committed in the agent implementation phase.

### Shell Discovery Adaptation (Python Agent)

In `agent/agent.py`, update `build_argv()` to gracefully fall back from `/bin/bash` to Android's Toybox `/system/bin/sh` or system `sh`:

```python
import shutil

def build_argv(kind: str, cmd: str) -> list[str]:
    """Map command kind to [executable, ...args, command-line]."""
    if kind == "ps1":
        exe = "powershell.exe" if IS_WINDOWS else "pwsh"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", cmd]
    if IS_WINDOWS:
        return ["cmd.exe", "/c", cmd]
    
    # Android & POSIX fallback:
    # 1. Prefer bash if available (Termux or BusyBox installed)
    # 2. Fallback to /system/bin/sh on Android
    # 3. Fallback to standard sh
    if shutil.which("bash"):
        return ["bash", "-c", cmd]
    if os.path.exists("/system/bin/sh"):
        return ["/system/bin/sh", "-c", cmd]
    return ["sh", "-c", cmd]
```

### Native C++ Agent NDK Toolchain & Fallback

In `agent.c/src/process_linux.cpp`:

```cpp
std::vector<std::string> build_argv(std::string_view kind, std::string_view cmd) {
    if (kind == "ps1") {
        return {"pwsh", "-NoProfile", "-NonInteractive", "-Command", std::string(cmd)};
    }
#if defined(__ANDROID__)
    return {"/system/bin/sh", "-c", std::string(cmd)};
#else
    return {"/bin/bash", "-c", std::string(cmd)};
#endif
}
```

Cross-compiling with Android NDK:
```bash
cmake -B out/build/android-arm64 \
  -DCMAKE_TOOLCHAIN_FILE=${ANDROID_NDK_HOME}/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-30 \
  -DCMAKE_BUILD_TYPE=Release
cmake --build out/build/android-arm64 --target cmdiqo-agent
```

---

## 5. Phase 2: Deployment Target A — Termux Userland (Rapid Setup)

Target A runs the standard Python agent inside Termux. This is the fastest method to verify functionality.

### 1. Prerequisites
- Download and install **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) or GitHub Releases.
- Download and install **Termux:Boot** from [F-Droid](https://f-droid.org/packages/com.termux.boot/).

### 2. Environment Setup
Launch Termux and execute:
```bash
# Update repositories and install core packages
pkg update && pkg install -y python python-pip tsu git

# Install required Python dependencies
pip install websockets

# Create agent workspace
mkdir -p ~/cmdiqo-agent
cd ~/cmdiqo-agent
```

### 3. Agent Configuration
Create `~/cmdiqo-agent/agent.env`:
```ini
HUB_URL=wss://your-hub-domain.com:8765
AGENT_TOKEN=your_secure_agent_token_here
AGENT_NAME=android-node-01
RECONNECT_DELAY=5.0
AGENT_CONNECTION_NOTIFY=true
```

### 4. Background Execution & Power Optimization
Prevent Android from killing Termux when the screen turns off:
```bash
# Acquire Termux wakelock
termux-wake-lock

# In Android System Settings:
# Settings > Apps > Termux > Battery > Select "Unrestricted"
```

### 5. Boot Persistence via Termux:Boot
Create `~/.termux/boot/cmdiqo-startup.sh`:
```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd /data/data/com.termux/files/home/cmdiqo-agent

# Run agent with root privilege using tsu
sudo python3 agent.py --config agent.env > agent.log 2>&1 &
```
Make the startup script executable:
```bash
chmod +x ~/.termux/boot/cmdiqo-startup.sh
```

---

## 6. Phase 3: Deployment Target B — Magisk Module (Headless System Daemon)

Target B packages the native C++ binary as a Magisk Module. It runs before user unlock, has zero UI footprint, consumes `< 3 MB` RAM, and survives app kills.

### 1. Module File Structure
```
cmdiqo-module/
├── module.prop
├── service.sh
└── system/
    └── bin/
        └── cmdiqo-agent
```

### 2. File Specifications

#### `module.prop`
```ini
id=cmdiqo-agent
name=Cmdiqo LAN/Cloud Remote Agent
version=v0.30.0
versionCode=3000
author=Jaroslav Lhotak
description=High-performance background agent connecting Android to the Cmdiqo management network.
```

#### `service.sh` (Late-start init daemon)
```bash
#!/system/bin/sh
MODDIR=${0%/*}

# Wait until Android has fully completed booting
while [ "$(getprop sys.boot_completed)" != "1" ]; do
    sleep 3
done

# Wait for network interface to come online
sleep 5

# Set environment variables for the agent
export HUB_URL="wss://hub.yourdomain.com:8765"
export AGENT_TOKEN="your_secure_agent_token_here"
export AGENT_NAME="android-headless-edge"
export RECONNECT_DELAY="5.0"

# Optional: hold kernel wake lock if 24/7 instant responsiveness is required
# echo "cmdiqo_agent" > /sys/power/wake_lock

# Launch the agent as root daemon
chmod 755 "${MODDIR}/system/bin/cmdiqo-agent"
"${MODDIR}/system/bin/cmdiqo-agent" > /data/local/tmp/cmdiqo-agent.log 2>&1 &
```

### 3. Packaging & Flashing
```bash
cd cmdiqo-module
zip -r ../cmdiqo-agent-magisk.zip .
```
- Open **Magisk App** > **Modules** > **Install from storage** > select `cmdiqo-agent-magisk.zip`.
- Reboot the device.

---

## 7. Comprehensive Guide: How to Root a Xiaomi Phone (MIUI & HyperOS)

This guide covers unlocking the bootloader and installing Magisk root on modern Xiaomi, Redmi, and POCO devices running **MIUI 13/14** or **Xiaomi HyperOS**.

> [!WARNING]
> Unlocking the bootloader **completely erases all data** on the device (factory reset). Back up all essential photos, contacts, and authenticators before proceeding.

---

### Step 1: Prerequisites & Account Requirements

1. **Active Xiaomi Account:**
   - Account must be signed in on the phone and bound to an active phone number.
   - For **Xiaomi HyperOS**: Xiaomi requires an account registered for at least 30 days and authorization through the global **Xiaomi Community App** (Profile > Unlock bootloader > Apply for unlocking).
   - For **MIUI**: No app application required; binding is done directly in Developer Options.
2. **SIM Card & Mobile Data:**
   - A physical or eSIM must be active in the phone. Mobile data (not Wi-Fi) is mandatory to bind the device to Xiaomi servers.
3. **Computer & Cable:**
   - Windows PC with ADB & Fastboot drivers installed.
   - High-quality USB data cable.
4. **Enable Developer Options on Device:**
   - Open **Settings** > **About Phone**.
   - Tap **OS Version** or **MIUI Version** repeatedly 7 times until you see `"You are now a developer!"`.
   - Go to **Settings** > **Additional Settings** > **Developer Options**.
   - Toggle **OEM Unlocking** to ON.
   - Toggle **USB Debugging** to ON.

---

### Step 2: Unlocking the Bootloader

1. **Bind Device to Mi Account:**
   - Turn off Wi-Fi; enable Mobile Data.
   - In **Developer Options**, tap **Mi Unlock Status**.
   - Tap **"Add account and device"**.
   - Confirm success message: `"Added successfully. Mi Account is associated with this device now."`
2. **Waiting Period (Mandatory Security Countdown):**
   - Xiaomi enforces a mandatory **168-hour (7 days)** security waiting period.
   - *Important:* Do not log out of the Xiaomi account, do not factory reset the phone, and do not change SIM cards during this period, or the timer resets.
3. **Unlock via Mi Unlock Tool (PC):**
   - Download the official **Mi Unlock Tool** from Xiaomi (`miflash_unlock`).
   - Power off the phone. Boot into **Fastboot Mode**:
     - Hold **Volume Down + Power** simultaneously until the Fastboot logo appears.
   - Connect phone to the PC via USB.
   - Open `miflash_unlock.exe`, sign in with the exact same Xiaomi account.
   - Click **Unlock**. The tool verifies the countdown. Once 168 hours have passed, it will unlock the bootloader and format userdata.
   - The phone reboots into the initial setup wizard with an unlocked padlock icon at startup.

---

### Step 3: Obtaining & Patching Stock Boot Image

Modern Android devices use one of two boot partitions depending on Android version:
- **GKI Devices (Launched with Android 13 or 14+):** Uses **`init_boot.img`** (e.g. Xiaomi 13, 14 series, Redmi Note 13 series).
- **Non-GKI Devices (Launched with Android 12 or earlier):** Uses **`boot.img`**.

1. **Find Exact Firmware Build:**
   - Check **Settings** > **About Phone** (e.g., `1.0.8.0.UNAMIXM` or `14.0.6.0.TLQEUXM`).
2. **Download Matching Fastboot ROM:**
   - Download the official Fastboot ROM (`.tgz` archive) matching your exact region and version.
   - Extract the `.tgz` archive to find the `images/` directory.
   - Locate `init_boot.img` (or `boot.img` if `init_boot.img` does not exist).
3. **Patch the Image with Magisk:**
   - Copy `init_boot.img` (or `boot.img`) to phone storage (e.g., `Download` folder).
   - Download and install the latest official **Magisk APK** from GitHub (`topjohnwu/Magisk`).
   - Open Magisk app > Tap **Install** (top card) > **Select and Patch a File**.
   - Select your copied `init_boot.img` (or `boot.img`).
   - Tap **LET'S GO**. Magisk writes `magisk_patched_[random_strings].img` to your `Download` folder.
   - Copy this patched `.img` file back to your PC's ADB folder.

---

### Step 4: Flashing via Fastboot

1. Reboot phone back into **Fastboot Mode** (`Volume Down + Power`).
2. Open terminal/PowerShell in your PC's ADB folder and verify device detection:
   ```bash
   fastboot devices
   ```
3. Flash the patched image:
   - **For devices with `init_boot.img` (Android 13/14+):**
     ```bash
     fastboot flash init_boot magisk_patched.img
     ```
   - **For devices with standard `boot.img`:**
     ```bash
     fastboot flash boot magisk_patched.img
     ```
4. Reboot the phone:
   ```bash
   fastboot reboot
   ```

---

### Step 5: Post-Root Configuration & Integrity Verification

1. **Finalize Magisk Setup:**
   - Open the Magisk app. If prompted with `"Requires Additional Setup"`, tap **OK** and allow the device to automatically reboot.
2. **Enable Systemless Hosts (Crucial for Cmdiqo):**
   - Open Magisk **Settings** (gear icon top right).
   - Scroll down to the *Module* section and tap **Systemless Hosts**.
   - Magisk automatically creates the module in `/data/adb/modules/hosts`.
3. **Preserve Banking & Play Integrity:**
   - In Magisk settings, enable **Zygisk**.
   - Enable **Enforce DenyList** and configure banking / Google Wallet apps under **Configure DenyList**.
   - Download and flash the **PlayIntegrityFix** Magisk module (by chiteroman) to maintain `MEETS_BASIC_INTEGRITY` and `MEETS_DEVICE_INTEGRITY`.
4. **Verify Superuser Access:**
   - Connect via ADB:
     ```bash
     adb shell
     su
     ```
   - Grant the root permission prompt on the phone screen. The shell prompt turns from `$` to `#`. Root installation is complete!

---

## 8. Testing Matrix & Operational Safeguards

When executing the Android implementation in a future phase, verify against this operational checklist:

| Verification Item | Command / Action | Expected Result | Pass/Fail |
| :--- | :--- | :--- | :--- |
| **Root Execution** | `su -c id` | Returns `uid=0(root) gid=0(root)` | [ ] |
| **WebSocket Connectivity** | Agent launch | Connects to central Cmdiqo Hub with TLS (`wss://`) | [ ] |
| **Battery Telemetry** | `dumpsys battery` via bot | Hub displays percentage, temperature, status | [ ] |
| **Screen Lock Action** | `input keyevent 26` via bot | Mobile screen locks immediately | [ ] |
| **App Freeze** | `pm suspend <pkg>` via bot | Target app icon grays out, cannot launch | [ ] |
| **App Unfreeze** | `pm unsuspend <pkg>` via bot | Target app restored to full operational state | [ ] |
| **Hosts Block** | Check `/system/etc/hosts` | Systemless hosts resolves blocked domain to `0.0.0.0` | [ ] |
| **Doze Survival** | Leave phone locked for 2 hrs | Agent responds to incoming Hub command within 5s | [ ] |
| **Network Roaming** | Toggle Wi-Fi to 4G Cellular | WebSocket reconnects cleanly within `reconnect_delay` | [ ] |
| **Reboot Survival** | `reboot` via bot | Agent restarts automatically upon boot completion | [ ] |
