# Cmdiqo Build & Development Guide

This guide covers:
1. **Local Environment Setup**: Automated environment activation via `.direnv` or PowerShell.
2. **Local Build System**: Unified native build scripts (`build.ps1` / `build.sh`) located at the repository root for standalone binaries and Docker images.
3. **Dynamic Versioning System**: Semantic versioning with GitHub Actions CI run numbers and hexadecimal Linux timestamp dev versions.
4. **Windows Authenticode Digital Signing & Trust**: How executables are signed, timestamped, and trusted across machines.
5. **GitHub Actions CI/CD & Secrets Guide**: Step-by-step tutorial for hosted builds, secrets configuration, and artifact downloads.

---

## 1. Local Environment Setup

Cmdiqo provides automated virtual environment setup across Linux, macOS, and Windows.

### Option A: Linux / macOS / WSL / Git-Bash (`direnv`)
Whenever you `cd` into the repository, `direnv` automatically:
- Creates a Python 3.12+ virtual environment (`.venv`) if not present.
- Activates the virtual environment.
- Installs or updates dependencies (`requirements.txt`, `requirements-dev.txt`, `agent/requirements.txt`).
- Decrypts GPG secrets from `secrets/*.gpg` to `.secrets.*` (if present) and loads `.env`.

**Setup:**
```bash
./development/setup-direnv.sh
```
Or allow manually if `direnv` is already installed on your system:
```bash
direnv allow .
```

### Option B: Windows Native PowerShell
Run the environment setup script:
```powershell
.\development\setup-env.ps1
```
This checks Python 3.12+, creates `.venv`, upgrades `pip`, installs all requirements including PyInstaller and UPX support, and creates a template `.env` file.

To activate the virtual environment in your PowerShell session:
```powershell
.venv\Scripts\Activate.ps1
```

### Running Services Locally (`run.py`)
To run directly without Docker:
```bash
# Start both Hub and Agent concurrently:
python run.py

# Start Hub (bot) only:
python run.py hub

# Start Agent only:
python run.py agent

# Start interactive Slack console emulator:
python run.py sim
```
> Pressing `Ctrl+C` cleanly shuts down all child processes without leaving background tasks running.

---

## 2. Dynamic Versioning System

The **single, true, and ONLY file** holding the version in the entire repository is:
$$\mathbf{version.txt}$$

All redundant template and temporary text files (`version.template`, `short_version.txt`) have been completely eliminated.

```text
version.txt (e.g. 1.0.0) — Single Source of Truth
       │
       ▼
development/update_version.py
       │
       ├──► agent/_version.py                          (bundled in PyInstaller & agent binary)
       ├──► bot/_version.py                            (bundled in Docker container & bot package)
       ├──► slacksim/_version.py                        (bundled in simulator package)
       └──► agent/version_info.txt                     (Windows PE resource manifest for PyInstaller)
```

> [!NOTE]
> All applications (`bot`, `agent`, and `slacksim`) bundle their version information directly via internal Python module **`_version.py`**. They never require or read `version.txt` at runtime. `version.txt` in the root is strictly the developer's source of truth.

### Version Resolution Rules:

1. **Base Version (`version.txt`):**
   Defines the `<major>.<minor>.<patch>` baseline (e.g., `1.0.0`).

2. **CI Builds (GitHub Actions):**
   - When building on GitHub Actions (`GITHUB_RUN_NUMBER` or `GITHUB_RUN_ID` is present), the third part of the version (patch) is automatically taken from the **GitHub build ID**:
     $$\text{VERSION} = \langle\text{major}\rangle.\langle\text{minor}\rangle.\langle\text{BUILD\_ID}\rangle$$
     *(Example: `1.0.42` for GitHub Actions run #42)*.

3. **Local Builds (Developer Machine):**
   - When building locally (outside GitHub Actions), the build system keeps the current **`dev+timestamp`** format: the current Unix timestamp is encoded as an uppercase **hexadecimal** (base16) string:
     $$\text{VERSION} = \langle\text{major}\rangle.\langle\text{minor}\rangle.\text{dev}\langle\text{HEX\_TIMESTAMP}\rangle$$
     *(Example: If timestamp is `1788611373`, its hex representation is `6A9C0B2D`, resulting in `1.0.dev6A9C0B2D`)*.
   - Hexadecimal formatting is fully compliant with PEP 440, Windows PE headers, and Docker image tag naming constraints (which disallow special base64 characters like `+`, `/`, and `=`).

4. **Windows PE Executable Metadata (`version_info.txt`):**
   - Windows PE binary headers require fixed 16-bit unsigned integers (`0`–`65535`) in `filevers=(major, minor, patch, build)`. For local dev builds, the lower 16 bits of the timestamp (`ts & 0xFFFF`) populate the binary tuple, while the full string (`1.0.dev6A9C0B2D`) is written to `FileVersion` and `ProductVersion`.
   - Right-clicking `cmdiqo-agent.exe` $\rightarrow$ Properties $\rightarrow$ Details shows:
     - **File version:** `1.0.dev6A9C0B2D` (or `1.0.42` in CI)
     - **Product version:** `1.0.dev6A9C0B2D` (or `1.0.42` in CI)
     - **Product name:** `Cmdiqo Agent`
     - **Legal copyright:** `Copyright (c) 2026 Jaroslav Lhotak. Verified GitHub build.`

---

### How to Bump or Update the Version (e.g. from 1.0 to 1.1)

To change or bump the version, you only touch **ONE** file:

#### Step-by-Step Workflow:

1. **Edit `version.txt`**:
   Update `version.txt` to the new `<major>.<minor>.<patch>` baseline:
   ```text
   1.1.0
   ```

2. **Propagate the Version (Optional / Automatic)**:
   Run the updater script or any build command:
   ```bash
   python development/update_version.py
   # Or run: .\build.ps1 / ./build.sh
   ```
   This automatically synchronizes:
   - **`agent/_version.py`** $\rightarrow$ `__version__ = "1.1..."` (bundled into agent binary)
   - **`bot/_version.py`** $\rightarrow$ `__version__ = "1.1..."` (bundled into Docker container)
   - **`slacksim/_version.py`** $\rightarrow$ `__version__ = "1.1..."` (bundled into simulator)
   - **`agent/version_info.txt`** $\rightarrow$ Windows PE binary manifest metadata

3. **Commit the Baseline Change**:
   ```bash
   git add version.txt
   git commit -m "Bump version to 1.1.0 /release"
   git push origin main
   ```

#### One-Off Version Override (Without Changing `version.txt`):
If you want to test-build an ad-hoc version without modifying `version.txt`:
- **Python script**: `python development/update_version.py --version 1.2.0`
- **PowerShell**: `.\build.ps1 --version "1.2.0"`
- **Bash**: `./build.sh -v 1.2.0`

---

## 3. Local Build System

Cmdiqo provides unified, self-contained native build scripts located at the repository root:
- `build.ps1` for Windows (PowerShell)
- `build.sh` for Linux, macOS, and WSL (Bash)

Supporting utilities (`build_agent.py`, `update_version.py`, certificates, and environment setup scripts) reside in `development/`.

### Building on Windows (PowerShell)

Run `.\build.ps1`:

```powershell
# In case you get errors with untrusted command, bypass the policy with (example to build installer for Win64)
powershell -ExecutionPolicy Bypass -File .\build.ps1 --target installer --platform win64

# 1. Build the Windows agent ZIP, PE binary, and WebView GUI installer:
.\build.ps1 --clean --platform win64 --target agent

# 2. Build and copy directly to a test location (e.g. C:\tmp\):
.\build.ps1 --copy-to "C:\tmp\cmdiqo-agent-windows-x64.exe"

# 3. Clean previous build artifacts and build with explicit version:
.\build.ps1 --clean --version "1.2.0"

# 4. Build both agent executable and bot Docker container:
.\build.ps1 --all

# 5. Build bot Docker container only:
.\build.ps1 --bot

# 6. Build and push multi-arch Docker container:
.\build.ps1 --bot --push
```

#### Parameters for `build.ps1`:
- `-Agent`: Switch to build standalone Windows agent executable and installer (default).
- `-Installer`: Switch to build standalone agent executable and GUI installer (WebView, Tkinter fallback).
- `-Bot`: Switch to build `cmdiqo-bot` Docker container image.
- `-All`: Switch to build standalone agent binary, installer package, and bot container.
- `-Target <Agent|Installer|Bot|All>`: Explicit target selection parameter.
- `-Platform, -p <win64|linux64|all>`: Target platform selection (default: `win64`). Use `all` with Docker Desktop to build Linux binaries too.
- `-Version, -v <string>`: Explicit version string override (e.g. `1.2.0`).
- `-Dev`: Force development build format `<major>.<minor>.dev<HEX>`.
- `-Clean`: Clean `build/` and `dist/` directories before compiling.
- `-SkipSign`: Skip Authenticode digital signing on Windows.
- `-CopyTo <path>`: Automatically copy built executable to destination path upon completion.
- `-Push`: Build and push multi-architecture Docker image to registry (`linux/amd64,linux/arm64`).
- `-DockerUser <string>`: Docker Hub registry namespace/username (defaults to `$env:DOCKERHUB_USERNAME` or `lhotakj`).
- `-Help, -h, -?`: Display detailed usage instructions.

### Building on Linux / macOS / WSL (Bash)

Run `./build.sh`:

```bash
# 1. Build the Linux agent ZIP, ELF binary, and WebView (GTK/WebKit2) GUI installer:
./build.sh --clean --target installer

# 2. Build Linux artifacts explicitly:
./build.sh --platform linux64

# 3. Build with explicit version and clean artifacts:
./build.sh -v 1.2.0 --clean

# 4. Build Docker container for cmdiqo-bot:
./build.sh --bot

# 5. Build both standalone agent and Docker bot:
./build.sh --all

# 6. Build and push multi-arch Docker container:
./build.sh --bot --push
```

#### Parameters for `build.sh`:
- `--agent, -a`: Build standalone agent binary and GUI installer (default).
- `--installer, -i`: Build standalone agent binary and GUI installer (WebView, Tkinter fallback).
- `--bot, -b, --docker`: Build Docker container for `cmdiqo-bot`.
- `--all`: Build Linux agent binary, GUI installer, and bot container.
- `--target <agent|installer|bot|all>`: Explicit target selection parameter.
- `--platform, -p <linux64>`: Linux target selection (default: `linux64`).
- `-v, --version <string>`: Explicit version override (e.g. `1.2.0`).
- `--dev`: Force development build format `<major>.<minor>.dev<HEX>`.
- `--clean`: Clean `build/` and `dist/` directories before compiling.
- `--copy-to <path>`: Automatically copy built binary to destination path upon completion.
- `--push`: Build and push multi-architecture Docker image to registry (`linux/amd64,linux/arm64`).
- `--docker-user <string>`: Docker Hub registry namespace/username (defaults to `$DOCKERHUB_USERNAME` or `lhotakj`).
- `-h, --help`: Display detailed usage instructions.

> [!TIP]
> **Containerized Linux builds**:
> When Docker is present on the Linux build host, `build.sh` automatically compiles the Linux agent and installer inside a lightweight container (`python:3.13-slim-trixie`, the latest Debian stable) that packages the GTK/WebKit2 Python backend. The target system must still provide a compatible native WebKit2GTK runtime; on openSUSE, install it with `sudo zypper install typelib-1_0-WebKit2-4_1`. If Docker is not available, the build cleanly falls back to the host Python. On a target without WebKit2GTK, the installer skips WebView and falls back to Tkinter, then to its interactive CLI wizard on headless hosts.

> **Windows artifacts:** PyInstaller produces native binaries and does not cross-compile. `build.sh` produces Linux artifacts only. Run `build.ps1 --platform win64` on Windows, or use the Windows GitHub Actions job, which publishes the native Windows agent and GUI installer alongside the Linux artifacts.

### Distribution Files

Each native build produces a platform ZIP containing the agent binary, command-line service installer/uninstaller scripts, and configuration examples. Release builds also upload the raw agent binary and GUI installer:

| Platform | ZIP service package | Raw agent | GUI installer |
|---|---|---|---|
| Linux x64 | `cmdiqo-agent-linux-x64.zip` | `cmdiqo-agent` | `cmdiqo-agent-linux-x64-setup` |
| Windows x64 | `cmdiqo-agent-windows-x64.zip` | `cmdiqo-agent.exe` | `cmdiqo-agent-windows-x64-setup.exe` |

Use the ZIP package for command-line installation. The raw installer is the graphical GUI installer for that platform — a modern WebView UI (Edge WebView2 on Windows, GTK/WebKit2 on Linux) that automatically falls back to Tkinter, and then to an interactive terminal wizard on headless hosts. Linux binaries downloaded outside the ZIP may need `chmod +x`.

### Building Container with Docker CLI & Compose

You can also build the `cmdiqo-bot` container directly using Docker or Docker Compose:

```bash
# Build locally with Docker CLI:
docker build -t lhotakj/cmdiqo-bot:latest -f docker/Dockerfile .

# Or build using Docker Compose:
docker compose -f docker/docker-compose.yml build
```

---

## 4. Windows Authenticode Signing & Certificate Trust

The build system signs both the Windows agent and GUI installer with Authenticode. A signature identifies the publisher, but Windows only trusts a certificate chain that the target computer recognizes.

### How Code Signing Works
When `development/build_agent.py` runs on Windows:
1. **Certificate Resolution:**
   - If `$env:SIGNING_CERT_BASE64` is provided (e.g. from GitHub Secrets), it imports your production code-signing certificate.
   - Otherwise, it checks `Cert:\CurrentUser\My` for an existing code-signing certificate, or generates a dedicated Cmdiqo Authenticode certificate (`CN=Cmdiqo, O=Cmdiqo, OU=Verified Build`).
2. **Dual Certificate Export:**
   - Exports the public certificate to `dist\cmdiqo-agent.cer` and `cmdiqo-agent.cer`.
3. **Trust Scope:**
   - A generated self-signed certificate is useful for development and internal distribution, but is not trusted by other computers until its exported `.cer` is deployed to their certificate stores.
   - A publicly trusted Authenticode certificate is required to avoid publisher warnings on unmanaged Windows computers. SmartScreen reputation can still take time to establish for a newly issued certificate or newly published binary.
4. **Timestamping:**
   - Digitally signs `cmdiqo-agent.exe` and `cmdiqo-agent-windows-x64-setup.exe` with SHA256 Authenticode signatures and timestamps them through Sectigo's RFC 3161 service (`http://timestamp.sectigo.com`).
   - Valid timestamps ensure the digital signature remains valid even after the certificate expires.

### How to Trust the Binary on Target Windows Machines

When you copy `cmdiqo-agent-windows-x64-setup.exe` or `cmdiqo-agent.exe` to another Windows PC, Windows Defender SmartScreen may warn about an untrusted publisher until the public certificate is installed into the machine's trusted certificate store. This applies to self-signed/internal certificates; use a publicly trusted Authenticode certificate for unmanaged machines.

#### Option A: Quick Command (Administrator or Standard User)
Open Command Prompt or PowerShell and run:
```cmd
certutil -addstore -user Root dist\cmdiqo-agent.cer
```
*(Or if using PowerShell)*:
```powershell
Import-Certificate -FilePath .\dist\cmdiqo-agent.cer -CertStoreLocation Cert:\CurrentUser\Root
```

#### Option B: Windows GUI (No Commands Needed)
1. Double-click `cmdiqo-agent.cer` (or right-click $\rightarrow$ **Install Certificate**).
2. Store Location: Select **Current User** $\rightarrow$ click **Next**.
3. Select **Place all certificates in the following store** $\rightarrow$ click **Browse...**.
4. Select **Trusted Root Certification Authorities** (or **Trusted Publishers**) $\rightarrow$ click **OK**.
5. Click **Next** $\rightarrow$ click **Finish**.
6. When the Windows Security confirmation dialog appears, click **Yes**.

Once the internal certificate is trusted, Windows recognizes binaries signed by that certificate. This does not establish SmartScreen reputation outside the managed environment.

---

## 5. GitHub Actions & Secrets Guide

Automated CI/CD workflows run in `.github/workflows/` using the exact same build engine as local builds:
- **`ci.yml`**: Runs Ruff linter, Pytest test suites, and SonarQube quality gate.
- **`build-agent.yml`**: Compiles standalone binaries for Linux (`ubuntu-24.04`) and Windows (`windows-latest`) using `development/build_agent.py`, applies Authenticode signing, and uploads downloadable artifacts.
- **`docker-publish.yml`**: Builds and publishes multi-arch Docker container images for `cmdiqo-bot`.

### 1. Summary of Repository Secrets

> [!NOTE]
> **No secrets are required to build and download binaries!**
> The binary build workflow works out of the box with zero secrets required. If signing secrets are omitted, the workflow automatically generates a dedicated Cmdiqo certificate, signs the `.exe`, and packages `cmdiqo-agent.cer` along with the binary.

| Secret Name | Workflow | Is it Required? | Description |
| :--- | :--- | :---: | :--- |
| `DOCKERHUB_USERNAME` | `docker-publish.yml` | **Only if publishing images** | Your Docker Hub account username. |
| `DOCKERHUB_TOKEN` | `docker-publish.yml` | **Only if publishing images** | Docker Hub Personal Access Token (PAT). |
| `SONAR_TOKEN` | `ci.yml` | **Optional** | SonarCloud token for code quality scanning. |
| `WINDOWS_SIGNING_CERT_BASE64` | `build-agent.yml` | **Optional** | Base64-encoded `.pfx` code-signing certificate. |
| `WINDOWS_SIGNING_CERT_PASSWORD` | `build-agent.yml` | **Optional** | Password for the `.pfx` certificate. |

---

### 2. How to Add Secrets in GitHub

1. Open your repository on GitHub:
   ```text
   https://github.com/<your-username>/Cmdiqo
   ```
2. Click the **Settings** tab in the top navigation bar.
3. In the left-hand sidebar:
   - Scroll down to **Security**.
   - Expand **Secrets and variables**.
   - Click **Actions**.
4. Under **Repository secrets**, click the green button: **New repository secret**.
5. Enter the **Name** (e.g. `DOCKERHUB_USERNAME`) and **Secret** value.
6. Click **Add secret**.

---

### 3. How to Obtain Secrets

#### A. Docker Hub Secrets (`DOCKERHUB_USERNAME` & `DOCKERHUB_TOKEN`)
1. Log into [Docker Hub](https://hub.docker.com).
2. Click your profile avatar $\rightarrow$ **Account Settings** $\rightarrow$ **Security**.
3. Under *Access Tokens*, click **New Access Token**.
4. Description: `GitHub Actions Cmdiqo`, Permissions: **Read & Write**.
5. Click **Generate** and copy the `dckr_pat_...` token into GitHub Secrets as `DOCKERHUB_TOKEN`.

#### B. SonarCloud Secret (`SONAR_TOKEN`)
1. Log into [SonarCloud.io](https://sonarcloud.io) using GitHub.
2. Click user avatar $\rightarrow$ **My Account** $\rightarrow$ **Security**.
3. Generate a new token named `Cmdiqo-CI` and paste into GitHub Secrets as `SONAR_TOKEN`.
*(If omitted, unit tests and linting still run 100% cleanly).*

#### C. Windows Code-Signing Certificate (Optional)
1. To reuse a persistent internal self-signed certificate, run `development\generate_signing_cert.ps1`. For a commercial Authenticode certificate, use its `.pfx` file instead.
2. Encode the certificate to a Base64 string in PowerShell:
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\signing-cert.pfx")) | Set-Clipboard
   ```
3. Save the Base64 string as `WINDOWS_SIGNING_CERT_BASE64` and its password as `WINDOWS_SIGNING_CERT_PASSWORD`.
4. Install the exported `.cer` in the trusted root and trusted publisher stores of managed target machines. This verifies the internal signature but does not create public SmartScreen reputation.

---

### 4. Release Automation & Trigger Flags

Cmdiqo CI/CD uses smart commit-message trigger commands to control formal releases, GitHub Release tag generation, binary asset uploads, and Docker Hub image publishing:

| Commit Flag | Action Taken | Artifacts & Destination |
| :--- | :--- | :--- |
| **`/release-agent`** | Builds the agent and GUI installer on native Linux and Windows runners. | Creates/updates GitHub Release tag (`v<version>`) and uploads both ZIP service packages, both raw agent binaries, and both GUI installers. |
| **`/release-bot`** | Builds multi-arch Docker image for `cmdiqo-bot`. | Creates/updates GitHub Release tag (`v<version>`) and pushes multi-arch image (`linux/amd64,linux/arm64`) to Docker Hub (`:latest`, `:<version>`, `:<major>.<minor>`). |
| **`/release`** | **Executes both** agent build/release and bot build/release in parallel. | Full production release: GitHub Release tag + agent `.zip` assets + Docker Hub container push. |

#### Usage Examples in Git Commits:
```bash
# 1. Release both Agent and Bot using base version from version.txt:
git commit -m "chore: release version 1.1.0 /release"
git push origin main

# 2. Release Agent only:
git commit -m "feat(agent): support new telemetry metrics /release-agent"
git push origin main

# 3. Release Bot only and publish to Docker Hub:
git commit -m "feat(hub): add wake-on-lan slash command /release-bot"
git push origin main

# 4. Release with an explicit version override directly in the commit message:
git commit -m "chore: prepare hotfix release /release v1.1.1"
git push origin main
```

> [!TIP]
> **Standard Commits (No Flags):**
> Pushing regular commits (without `/release*`) runs continuous integration tests and linting (`ci.yml`) in seconds, without triggering PyInstaller compilation or pushing unwanted intermediate images to Docker Hub.

#### Automated Decision via Commit Message:
Push-triggered release publication evaluates the commit message of the commit pushed to `main`:
- If the commit message contains `/release-agent` $\rightarrow$ Agent binaries are built, signed, and published to GitHub Releases.
- If the commit message contains `/release-bot` $\rightarrow$ Bot Docker image is built and pushed to Docker Hub, and GitHub Release tag is created.
- If the commit message contains `/release` $\rightarrow$ Both Agent and Bot are built, released to GitHub, and pushed to Docker Hub.
- If none of the flags are found $\rightarrow$ Workflows exit immediately without compiling binaries, tagging releases, or publishing Docker images.

The **Build Agent Binaries (Windows & Linux x64)** workflow can also be started manually from the Actions tab. A manual run builds and retains both platform artifact sets for 90 days, but does not create or modify a GitHub Release.

#### Downloading Built Binaries:
1. Navigate to **Releases** on GitHub (`https://github.com/<user>/Cmdiqo/releases`):
   - Every `/release-agent` or `/release` run attaches both platform ZIP service packages, raw agent binaries, and GUI installers listed in [Distribution Files](#distribution-files).
2. Or open any completed workflow run under the **Actions** tab:
   - Scroll to **Artifacts** to download build packages directly.
