# Shango Build & Development Guide

This guide covers:
1. **Local Environment Setup**: Automated environment activation via `.direnv` or PowerShell.
2. **Local Build System**: Unified native build scripts (`build.ps1` / `build.sh`) located in `development/` for standalone binaries and Docker images.
3. **Dynamic Versioning System**: Semantic versioning with GitHub Actions CI run numbers and hexadecimal Linux timestamp dev versions.
4. **Windows Authenticode Digital Signing & Trust**: How executables are signed, timestamped, and trusted across machines.
5. **GitHub Actions CI/CD & Secrets Guide**: Step-by-step tutorial for hosted builds, secrets configuration, and artifact downloads.

---

## 1. Local Environment Setup

Shango provides automated virtual environment setup across Linux, macOS, and Windows.

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

Version numbers are maintained centrally and dynamically propagated across the codebase:

```text
version.template (e.g. 1.0.0)
       │
       ▼
development/update_version.py
       │
       ├──► version.txt               (e.g. 1.0.42 in CI or 1.0.dev6A9C0B2D locally)
       ├──► short_version.txt         (e.g. 1.0)
       ├──► agent/_version.py         (__version__ in Python agent)
       ├──► bot/_version.py           (__version__ in Python bot)
       └──► agent/version_info.txt    (Windows PE resource manifest for PyInstaller)
```

### Version Resolution Rules:
1. **Base Version (`version.template`):** Defines the `<major>.<minor>.<patch>` baseline (e.g., `1.0.0`).
2. **CI Builds (GitHub Actions):**
   - When running under GitHub Actions, the `GITHUB_RUN_NUMBER` environment variable is automatically detected.
   - The third number is replaced with the GitHub run number:
     $$\text{VERSION} = \langle\text{major}\rangle.\langle\text{minor}\rangle.\langle\text{GITHUB\_RUN\_NUMBER}\rangle$$
     *(Example: `1.0.42` for run #42)*.
3. **Local Builds (Developer Machine):**
   - When running locally (where `GITHUB_RUN_NUMBER` is not present), the build system takes the current Linux timestamp (seconds since Unix epoch), formats it as an uppercase **Hexadecimal** string, and prefixes it with `.dev`:
     $$\text{VERSION} = \langle\text{major}\rangle.\langle\text{minor}\rangle.\text{dev}\langle\text{HEX\_TIMESTAMP}\rangle$$
     *(Example: If timestamp is `1788611373`, its hex representation is `6A9C0B2D`, resulting in `1.0.dev6A9C0B2D`)*.
4. **Windows PE Executable Metadata (`version_info.txt`):**
   - Windows PE binary headers require fixed 16-bit unsigned integers (`0`–`65535`) in `filevers=(major, minor, patch, build)`. For local dev builds, the lower 16 bits of the timestamp (`ts & 0xFFFF`) populate the binary tuple, while the full string (`1.0.dev6A9C0B2D`) is written to `FileVersion` and `ProductVersion`.
   - Right-clicking `shango-agent.exe` $\rightarrow$ Properties $\rightarrow$ Details shows:
     - **File version:** `1.0.dev6A9C0B2D` (or `1.0.42` in CI)
     - **Product version:** `1.0.dev6A9C0B2D` (or `1.0.42` in CI)
     - **Product name:** `Shango Agent`
     - **Legal copyright:** `Copyright (c) 2026 Jaroslav Lhotak. Verified GitHub build.`

### Manual Version Synchronization:
To manually synchronize version numbers across all files without compiling:
```bash
python development/update_version.py
```
Or with an explicit override:
```bash
python development/update_version.py --version 1.2.0
```

---

## 3. Local Build System

All build machinery resides in `development/`, with root-level wrapper scripts (`build.ps1` and `build.sh`) provided for developer convenience.

### Building on Windows (PowerShell)

Use `.\build.ps1` (or `.\development\build.ps1`):

```powershell
# 1. Build standalone Windows agent executable (dist\shango-agent.exe):
.\build.ps1

# 2. Build and copy directly to a test location (e.g. C:\tmp\):
.\build.ps1 -CopyTo "C:\tmp\shango-agent-windows-x64.exe"

# 3. Clean previous build artifacts and build with explicit version:
.\build.ps1 -Clean -Version "1.2.0"

# 4. Build both agent executable and bot Docker container:
.\build.ps1 -Target All

# 5. Build bot Docker container only:
.\build.ps1 -Target Bot
```

#### Parameters for `build.ps1`:
- `-Target <Agent|Bot|All>`: Target component to build (default: `Agent`).
- `-Version <string>`: Explicit version string override.
- `-SkipSign`: Skip Authenticode digital signing on Windows.
- `-CopyTo <path>`: Automatically copy built executable to destination path.
- `-Clean`: Remove `build/` and `dist/` directories before compiling.

### Building on Linux / macOS / WSL (Bash)

Use `./build.sh` (or `./development/build.sh`):

```bash
# 1. Build standalone Linux agent binary (dist/shango-agent):
./build.sh

# 2. Build with explicit version:
./build.sh -v 1.2.0

# 3. Build Docker container for shango-bot:
./build.sh --bot

# 4. Build both standalone agent and Docker bot:
./build.sh --all
```

#### Parameters for `build.sh`:
- `--agent`: Build standalone agent binary (default).
- `--bot`: Build Docker container for `shango-bot`.
- `--all`: Build both agent binary and bot container.
- `--dev`: Force development build format `<major>.<minor>.dev<HEX>`.
- `--push`: Build and push multi-architecture Docker image to registry.
- `-v, --version <string>`: Explicit version override.

---

## 4. Windows Authenticode Signing & Certificate Trust

To ensure the Windows binary is safe, verified, and free of security warnings, the build system integrates Authenticode digital signing and certificate management.

### How Code Signing Works
When `development/build_agent.py` runs on Windows:
1. **Certificate Resolution:**
   - If `$env:SIGNING_CERT_BASE64` is provided (e.g. from GitHub Secrets), it imports your production code-signing certificate.
   - Otherwise, it checks `Cert:\CurrentUser\My` for an existing code-signing certificate, or generates a dedicated Shango Authenticode certificate (`CN=Shango, O=Shango, OU=Verified Build`).
2. **Dual Certificate Export:**
   - Exports the public certificate to `dist\shango-agent.cer` and `shango-agent.cer`.
3. **Local Store Registration:**
   - Adds the certificate to `Cert:\CurrentUser\TrustedPublisher` store, making the executable trusted on the local build machine.
4. **Dual Timestamping:**
   - Digitally signs `shango-agent.exe` with SHA256 Authenticode signatures and queries official RFC 3161 timestamp servers (`http://timestamp.digicert.com` with fallback to `http://timestamp.sectigo.com`).
   - Valid timestamps ensure the digital signature remains valid even after the certificate expires.

### How to Trust the Binary on Target Windows Machines

When you copy `shango-agent.exe` to another Windows PC or download it from GitHub Actions, Windows Defender SmartScreen may inspect untrusted publishers until the public certificate is installed into the machine's trusted certificate store.

#### Option A: Quick Command (Administrator or Standard User)
Open Command Prompt or PowerShell and run:
```cmd
certutil -addstore -user Root dist\shango-agent.cer
```
*(Or if using PowerShell)*:
```powershell
Import-Certificate -FilePath .\dist\shango-agent.cer -CertStoreLocation Cert:\CurrentUser\Root
```

#### Option B: Windows GUI (No Commands Needed)
1. Double-click `shango-agent.cer` (or right-click $\rightarrow$ **Install Certificate**).
2. Store Location: Select **Current User** $\rightarrow$ click **Next**.
3. Select **Place all certificates in the following store** $\rightarrow$ click **Browse...**.
4. Select **Trusted Root Certification Authorities** (or **Trusted Publishers**) $\rightarrow$ click **OK**.
5. Click **Next** $\rightarrow$ click **Finish**.
6. When the Windows Security confirmation dialog appears, click **Yes**.

Once trusted, `shango-agent.exe` runs smoothly without warnings.

---

## 5. GitHub Actions & Secrets Guide

Automated CI/CD workflows run in `.github/workflows/` using the exact same build engine as local builds:
- **`ci.yml`**: Runs Ruff linter, Pytest test suites, and SonarQube quality gate.
- **`build-agent.yml`**: Compiles standalone binaries for Linux (`ubuntu-24.04`) and Windows (`windows-latest`) using `development/build_agent.py`, applies Authenticode signing, and uploads downloadable artifacts.
- **`docker-publish.yml`**: Builds and publishes multi-arch Docker container images for `shango-bot`.

### 1. Summary of Repository Secrets

> [!NOTE]
> **No secrets are required to build and download binaries!**
> The binary build workflow works 100% out of the box with zero secrets required. If signing secrets are omitted, the workflow automatically generates a dedicated Shango certificate, signs the `.exe`, and packages `shango-agent.cer` along with the binary.

| Secret Name | Workflow | Is it Required? | Description |
| :--- | :--- | :---: | :--- |
| `DOCKERHUB_USERNAME` | `docker-publish.yml` | **Only if publishing images** | Your Docker Hub account username. |
| `DOCKERHUB_TOKEN` | `docker-publish.yml` | **Only if publishing images** | Docker Hub Personal Access Token (PAT). |
| `SONAR_TOKEN` | `ci.yml` | **Optional** | SonarCloud token for code quality scanning. |
| `WINDOWS_SIGNING_CERT_BASE64` | `build-agent.yml` | **Optional** | Base64-encoded commercial `.pfx` code-signing certificate. |
| `WINDOWS_SIGNING_CERT_PASSWORD` | `build-agent.yml` | **Optional** | Password for the commercial `.pfx` certificate. |

---

### 2. How to Add Secrets in GitHub

1. Open your repository on GitHub:
   ```text
   https://github.com/<your-username>/Shango
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
4. Description: `GitHub Actions Shango`, Permissions: **Read & Write**.
5. Click **Generate** and copy the `dckr_pat_...` token into GitHub Secrets as `DOCKERHUB_TOKEN`.

#### B. SonarCloud Secret (`SONAR_TOKEN`)
1. Log into [SonarCloud.io](https://sonarcloud.io) using GitHub.
2. Click user avatar $\rightarrow$ **My Account** $\rightarrow$ **Security**.
3. Generate a new token named `Shango-CI` and paste into GitHub Secrets as `SONAR_TOKEN`.
*(If omitted, unit tests and linting still run 100% cleanly).*

#### C. Commercial Windows Code-Signing Secrets (Optional)
If you own a commercial Authenticode `.pfx` certificate:
1. Encode your certificate to a Base64 string in PowerShell:
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\mycert.pfx")) | Set-Clipboard
   ```
2. Save into GitHub Secrets as `WINDOWS_SIGNING_CERT_BASE64`, and save your certificate password as `WINDOWS_SIGNING_CERT_PASSWORD`.

---

### 4. How to Trigger Builds & Download Binaries

#### Automated Trigger
Pushing any commit to the `main` branch automatically triggers CI tests and binary builds.

#### Manual Trigger (On Demand)
1. Go to your GitHub repository $\rightarrow$ click the **Actions** tab.
2. In the left sidebar, click **Build Agent Binaries (Windows & Linux x64)**.
3. Click **Run workflow** $\rightarrow$ select branch `main` $\rightarrow$ click **Run workflow**.

#### Downloading Built Binaries:
1. Click the completed workflow run.
2. Scroll to the bottom to **Artifacts**:
   - **`shango-agent-windows-x64`**: Contains `shango-agent-windows-x64.exe` (digitally signed with version and metadata) and `shango-agent.cer`.
   - **`shango-agent-linux-x64`**: Standalone Linux x64 executable.
3. Click to download the `.zip` archive.
