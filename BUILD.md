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
   Defines the `<major>.<minor>` baseline (e.g., `0.10.0`).

2. **CI Builds (GitHub Actions):**
   - The third component (patch number) is the GitHub Actions workflow run number (`GITHUB_RUN_NUMBER` or `BUILD_ID`):
     $$\text{VERSION} = \langle\text{major}\rangle.\langle\text{minor}\rangle.\langle\text{GITHUB\_RUN\_NUMBER}\rangle$$
     *(Example: `0.10.42` for GitHub Actions run #42)*.

3. **Local Builds (Developer Machine):**
   - The build system uses `devel+` followed by the uppercase hexadecimal Unix
     timestamp:
     $$\text{VERSION} = \langle\text{major}\rangle.\langle\text{minor}\rangle.\text{devel}+\langle\text{HEX\_TIMESTAMP}\rangle$$
     *(Example: timestamp `1788611373` becomes
     `0.10.devel+6A9C0B2D`)*.

4. **Windows PE Executable Metadata (`version_info.txt`):**
   - Windows PE binary headers require fixed 16-bit unsigned integers (`0`–`65535`) in `filevers=(major, minor, patch, build)`. For local builds, the lower 16 bits of the timestamp (`ts & 0xFFFF`) populate the binary tuple. For CI builds, the run number (`int(GITHUB_RUN_NUMBER) & 0xFFFF`) populates it. The full build version is written to `FileVersion` and `ProductVersion`.
   - Right-clicking `cmdiqo-agent.exe` $\rightarrow$ Properties $\rightarrow$ Details shows:
     - **File version:** `0.10.devel+6A9C0B2D` (or `0.10.42` in CI)
     - **Product version:** `0.10.devel+6A9C0B2D` (or `0.10.42` in CI)
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
- `build.sh` for Linux, macOS, WSL, and Windows Git Bash

Supporting utilities (`build_agent.py`, `update_version.py`, certificates, and environment setup scripts) reside in `development/`.

### Building on Windows (PowerShell)

Run `.\build.ps1`:

```powershell
# In case you get errors with an untrusted command, bypass the policy (Windows agent package example)
powershell -ExecutionPolicy Bypass -File .\build.ps1 --target agent --platform win64

# 1. Build the Windows agent ZIP and PE binary:
.\build.ps1 --clean --platform win64 --target agent

# 2. Build and copy directly to a test location (e.g. C:\tmp\):
.\build.ps1 --copy-to "C:\tmp\cmdiqo-agent-windows-x64.exe"

# 3. Clean previous build artifacts and build with explicit version:
.\build.ps1 --clean --version "1.2.0"

# 4. Build the Windows agent package and native setup executable:
.\build.ps1 --installer

# 5. Rebuild only the native setup using the existing agent ZIP:
.\build.ps1 --installer --skip-agent-build

# 6. Build agent, native setup, and bot Docker container:
.\build.ps1 --all

# 7. Build bot Docker container only:
.\build.ps1 --bot

# 8. Build and push multi-arch Docker container:
.\build.ps1 --bot --push
```

#### Parameters for `build.ps1`:
- `-Agent`: Switch to build the standalone Windows agent executable and ZIP package (default).
- `-Installer`: Build the standalone Windows agent executable, then build the native C++ setup executable with the agent binary embedded directly into the installer.
- `-Bot`: Switch to build `cmdiqo-bot` Docker container image.
- `-All`: Switch to build the agent package, native setup executable, and bot container.
- `-Target <Agent|Installer|Bot|All>`: Explicit target selection parameter.
- `-Platform, -p <win64|linux64|all>`: Target platform selection (default: `win64`). Use `all` or `linux64` with Docker Desktop to build Linux binaries too.
- `-Version, -v <string>`: Explicit version string override (e.g. `1.2.0`).
- `-Dev`: Force development build format `<major>.<minor>.devel+<HEX>`.
- `-Clean`: Clean `build/` and `dist/` directories before compiling.
- `-SkipAgentBuild`: With `-Installer`, reuse the existing agent binary in `dist/` and rebuild only the native C++ setup executable.
- `-SkipSign`: Skip Authenticode digital signing on Windows.
- `-CopyTo <path>`: Automatically copy the built agent binary to a destination
  path. Requires an agent build for a single platform; Linux Docker builds also
  copy the ZIP beside it.
- `-Push`: Build and push multi-architecture Docker image to registry (`linux/amd64,linux/arm64`).
- `-DockerUser <string>`: Docker Hub registry namespace/username (defaults to `$env:DOCKERHUB_USERNAME` or `lhotakj`).
- `-Help, -h, -?`: Display detailed usage instructions.

### Building on Linux / macOS / WSL (Bash)

Run `./build.sh`:

```bash
# 1. Build the Linux agent ZIP and ELF binary:
./build.sh --clean --target agent

# 2. Build Linux artifacts explicitly:
./build.sh --platform linux64

# 3. Build with explicit version and clean artifacts:
./build.sh -v 1.2.0 --clean

# 4. Build the Linux agent package and native setup executable (Release by default):
./build.sh --installer

# 5. Cross-build Windows setup using a ZIP built on Windows:
./build.sh --installer --platform win64 --skip-agent-build

# 6. Rebuild only the native setup using the existing agent ZIP:
./build.sh --installer --skip-agent-build

# Rebuild both native setups from existing Linux and Windows ZIPs:
./build.sh --installer --platform all --skip-agent-build

# 7. Build Docker container for cmdiqo-bot:
./build.sh --bot

# 8. Build agent, native setup, and Docker bot:
./build.sh --all

# 9. Build and push multi-arch Docker container:
./build.sh --bot --push
```

#### Parameters for `build.sh`:
- `--agent, -a`: Build standalone agent binary and ZIP package (default).
- `--installer, -i`: Build the standalone agent binary, then
  build the native C++ setup executable with the agent binary embedded directly into the installer.
- `--bot, -b, --docker`: Build Docker container for `cmdiqo-bot`.
- `--all`: Build the agent package, native setup executable, and bot container
  for the selected platform. This is a component selector, not `--platform all`.
- `--target <agent|installer|bot|all>`: Explicit target selection parameter.
- `--platform, -p <linux64|win64|all>`: Target platform selection. Defaults to
  `win64` on Windows Git Bash and `linux64` elsewhere; `linux`, `windows`, and
  `win` are accepted aliases.
- `-v, --version <string>`: Explicit version override (e.g. `1.2.0`).
- `--dev`: Force development build format `<major>.<minor>.devel+<HEX>`.
- `--clean`: Clean selected build outputs; preserve input ZIPs when
  `--skip-agent-build` is used and preserve other platforms' distributions.
- `--skip-agent-build`: With `--installer`, reuse the selected platforms'
  existing agent binaries in `dist/` and rebuild only the native C++ setup executables.
- `--copy-to <path>`: Copy the agent binary and its ZIP to a destination
  directory or binary filename. Requires an agent build for a single platform;
  works for both Docker and host-Python builds.
- `--skip-sign`: Skip Authenticode signing when building with native Windows Python.
- `--push`: Build and push multi-architecture Docker image to registry (`linux/amd64,linux/arm64`).
- `--docker-user <string>`: Docker Hub registry namespace/username (defaults to
  `$DOCKERHUB_USER`, `$DOCKERHUB_USERNAME`, or `lhotakj`).
- `-h, --help`: Display detailed usage instructions.

> [!TIP]
> **Containerized builds & Pre-built Docker Hub Images**:
> Docker installer builds from both entry points use the unified
> `lhotakj/cmdiqo-installer-builder:all` image for Linux GTK3 and Windows wxMSW.
> A matching local image is reused; otherwise the image is pulled and its
> Dockerfile/manifest/triplet hash must match. Missing or mismatched hashes
> trigger a local `all` image build, never reuse of unknown dependencies.

### Host and target requirements

| Host | Linux x64 agent | Windows x64 agent | Native C++ setup |
|---|---|---|---|
| Linux x64 | Docker, or native Linux x64 Python | Build the ZIP on Windows first | Docker for either target |
| macOS / non-x64 Linux | Linux-container Docker | Build the ZIP on Windows first | Docker for either target |
| Windows PowerShell / Git Bash | Linux-container Docker | Native Windows x64 Python | Unified Docker builder; optional local MSVC from PowerShell |

PyInstaller builds for the operating system of its Python interpreter; MinGW
cross-compiles only the C++ setup, not the Python agent. A Unix invocation that
requests a Windows agent fails explicitly rather than accepting an old `.exe`
or ZIP as a successful build. To produce both agent packages and both setups
in one invocation, run `.\build.ps1 -Installer -Platform all` on Windows.
From Unix, place both platform ZIPs in `dist/` and use the combined
`--skip-agent-build` command above.

All agent and native-installer Docker builds target `linux/amd64`. ARM hosts
need Docker's amd64 emulation; the bot's `--push` path remains multi-architecture.
Local Buildx builds use `--load` so their images are available even with the
`docker-container` driver. Docker Desktop must run Linux containers. Git Bash
mount paths are converted to Windows paths without rewriting container paths.
Without Docker, only native Linux x64 agent builds are supported by `build.sh`.

Installer builds consume pre-built dependencies with
`VCPKG_MANIFEST_INSTALL=OFF` and fail on configure, compilation, or test errors.
Windows C++ tests execute through Wine with a writable temporary prefix.
Payload `version.txt` accepts normal numeric versions such as `1.2.0`, CI SHA
versions, and local `devel+HEX` versions; the setup embeds the payload version.
Development Docker image tags use `devel-HEX` because Docker tags cannot contain
`+`; agent and installer version metadata remains unchanged.

### Distribution Files

Each native build produces a platform ZIP containing the agent binary, command-line service install/uninstall scripts, and configuration examples. Release builds also upload the raw agent binary:

| Platform | ZIP service package | Raw agent | ZIP scripts |
|---|---|---|---|
| Linux x64 | `cmdiqo-agent-linux-x64.zip` | `cmdiqo-agent` | `install.sh`, `uninstall.sh` |
| Windows x64 | `cmdiqo-agent-windows-x64.zip` | `cmdiqo-agent.exe` | `install.ps1`, `uninstall.ps1` |

Use the ZIP package for service installation. Extract it, then run the platform-specific install script as an administrator/root user. Linux binaries downloaded outside the ZIP may need `chmod +x`.

`--installer` / `-Installer` additionally produces a self-contained native setup executable with the agent binary embedded inside:

| Platform | Native setup executable |
|---|---|
| Linux x64 | `dist/cmdiqo-agent-linux-x64-setup` |
| Windows x64 | `dist\cmdiqo-agent-windows-x64-setup.exe` |

The root build scripts compile the agent binary first, embed it directly into the native C++ setup executable in Release mode, and publish it to `dist/`. The installer is completely standalone and does not require an external `.zip` package. See [installer.c/BUILD-C.md](installer.c/BUILD-C.md) for native
development environment setup, Docker Hub publishing, and manual CMake commands.

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

The build system signs the Windows agent with Authenticode. A signature identifies the publisher, but Windows only trusts a certificate chain that the target computer recognizes.

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
   - Digitally signs `cmdiqo-agent.exe` with SHA256 Authenticode signatures and timestamps it through Sectigo's RFC 3161 service (`http://timestamp.sectigo.com`).
   - Valid timestamps ensure the digital signature remains valid even after the certificate expires.

### How to Trust the Binary on Target Windows Machines

When you copy `cmdiqo-agent.exe` to another Windows PC, Windows Defender SmartScreen may warn about an untrusted publisher until the public certificate is installed into the machine's trusted certificate store. This applies to self-signed/internal certificates; use a publicly trusted Authenticode certificate for unmanaged machines.

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

## 5. GitHub Actions & Workflows Architecture

Automated CI/CD workflows live in `.github/workflows/` and provide a clean, streamlined 3-pipeline architecture:

1. **`ci.yml` (CI: Lint & Test)**:
   - **Trigger:** Runs on **any commit to any branch** (`push:`) and all pull requests (`pull_request:`).
   - **Actions:** Sets up Python 3.12, installs dependencies, runs Ruff linter across all packages (`bot`, `agent`, `development`, `tests`), runs Pytest unit and integration test suites with coverage, and performs SonarCloud analysis (if `SONAR_TOKEN` is present).
   - **Speed:** Finishes in under 60 seconds with no container builds or binary compilation.

2. **`release.yml` (Build & Release)**:
   - **Trigger:** Runs on every push to `main` and manual `workflow_dispatch`.
   - **Build Execution:** Reuses `./build.sh` on an `ubuntu-24.04` runner to compile all apps cross-platform from a single Linux environment:
     - Linux native agent binary (`cmdiqo-agent`)
     - Windows native agent binary (`cmdiqo-agent.exe`)
     - Linux package with systemd service installer (`cmdiqo-agent-linux-x64.zip`)
     - Windows package with PowerShell service installer (`cmdiqo-agent-windows-x64.zip`)
     - Linux native GTK3 GUI setup wizard (`cmdiqo-agent-linux-x64-setup`)
     - Windows native Win32 GUI setup wizard (`cmdiqo-agent-windows-x64-setup.exe`)
     - Cmdiqo Slack bot multi-arch container (`linux/amd64`, `linux/arm64`)
   - **Artifacts:** Always verifies binaries and uploads them to GitHub Actions run storage (retained 90 days).
   - **`/release` Automation:** If the commit message contains `/release` (e.g. `/release v0.10.0` or `/release 0.10.0`):
     - Pushes multi-arch Docker image (`:latest` and `:<version>`) to Docker Hub.
     - Automatically generates a formatted Markdown release summary (`development/generate_release_summary.py`) with a complete table of artifact names, architectures, file sizes, SHA256 checksums, and container pull commands.
     - Publishes the release to GitHub Releases with tag `v<version>`, attached files, and structured release notes.
     - Renders the summary directly in the GitHub Actions Workflow Step Summary.

3. **`builder-image.yml` (Build C++ Builder Image)**:
   - **Trigger:** Triggered **manually** (`workflow_dispatch`) or on push **only if `installer.c/Dockerfile` changes**.
   - **Actions:** Uses `installer.c/setup-env.sh --platform all --docker-user lhotakj --push` to compile the multi-toolchain C++ builder container (`lhotakj/cmdiqo-installer-builder:all`), pushes it to Docker Hub, and smoke-tests it against the native installer CMake configuration.
   - **Guarantee:** Never runs during normal development commits or releases; pre-built images are pulled by `build.sh` in seconds.

### 1. Summary of Repository Secrets

> [!NOTE]
> **No secrets are required to build and download binaries!**
> The unified build workflow compiles both Linux and Windows artifacts out of the box using Linux container cross-compilers with zero secrets required. Docker Hub credentials are only needed if publishing container images.

| Secret Name | Workflow | Is it Required? | Description |
| :--- | :--- | :--- | :--- |
| `DOCKERHUB_USERNAME` | `release.yml` | **Only if publishing images** | Your Docker Hub account username. |
| `DOCKERHUB_TOKEN` | `release.yml` | **Only if publishing images** | Docker Hub Personal Access Token (PAT). |
| `SONAR_TOKEN` | `ci.yml` | **Optional** | SonarCloud token for code quality scanning. |

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

| Commit Command | Action Taken | Artifacts & Destination |
| :--- | :--- | :--- |
| *(standard push to main)* | Compiles all Linux & Windows binaries, zips, setups, and Docker container. | Stored in GitHub Actions run artifacts (90 days). No public release or tag created. |
| **`/release`** | Full release using baseline version in `version.txt`. | All binaries built + Docker container pushed to Docker Hub + GitHub Release created with summary notes. |
| **`/release <version>`** | Full release with specific version override (e.g. `/release 0.10.0` or `/release v0.10.0`). | Same as `/release`, with version updated across all artifacts and Git tag `v<version>`. |
| **`/release-agent`** | Release targeting only the native C++ agent packages. | Linux & Windows agent binaries, ZIPs, and setups attached to GitHub Release. |
| **`/release-bot`** | Release targeting only the Cmdiqo Hub container. | Builds and pushes multi-arch Docker image to Docker Hub, creates release tag. |

#### Usage Examples in Git Commits:
```bash
# 1. Standard commit (builds all apps and tests, no public release created):
git commit -m "feat: add application domain blocking rules"
git push origin main

# 2. Release with baseline version from version.txt:
git commit -m "chore: release /release"
git push origin main

# 3. Release with explicit version override:
git commit -m "chore: /release v0.10.0"
git push origin main
```

#### Downloading Built Binaries:
1. Navigate to **Releases** on GitHub (`https://github.com/<user>/Cmdiqo/releases`):
   - Every `/release` run attaches both platform ZIP service packages, setup wizards, raw agent binaries, and a detailed checksum table.
2. Or open any completed workflow run under the **Actions** tab:
   - Scroll to **Artifacts** to download build packages directly from any main push.

## Universal Clean Rebuild of Builder Images and All Toolchains

When toolchains, vcpkg dependencies (e.g., `CLI11`, `ixwebsocket`, `wxWidgets`, `libzip`), or platform Dockerfiles change, use the following universal command to perform a 100% clean, end-to-end rebuild on the host:

```bash
nohup bash -c "cd installer.c && ./setup-env.sh --platform all --rebuild-image --push && cd .. && ./build.sh --installer --platform all --clean" > installer.log 2>&1 &
```

### Why It Is Structured This Way

| Segment | Purpose & Rationale |
| :--- | :--- |
| `nohup bash -c "..." > installer.log 2>&1 &` | **Detached Background Execution & Unified Logging:** Rebuilding multi-toolchain Docker images from scratch (compiling wxWidgets, vcpkg ports, etc.) takes 15–30+ minutes. `nohup` prevents terminal disconnects (`SIGHUP`) from killing the job. Wrapping the commands in `bash -c "..."` runs the pipeline as a single background task, redirecting all stdout/stderr to `installer.log`. |
| `cd installer.c && ./setup-env.sh` | **Environment Setup:** Switches to the native installer directory where Docker builder definitions, overlay triplets, and vcpkg manifests reside. |
| `--platform all` *(setup-env)* | **Unified Multi-Platform Builder:** Prepares the unified Docker builder container containing both Linux GCC + GTK3 and Windows MinGW-w64 cross-compilers. |
| `--rebuild-image` | **Forced Layer Cache Invalidation:** Bypasses Docker build cache (`--pull --no-cache`) and recompiles all toolchains and vcpkg packages fresh, ensuring newly added ports (like `CLI11`) are genuinely installed. |
| `--push` | **Publish to Registry & Local Load:** Exports the freshly built image to the local Docker daemon (`--load`) and simultaneously pushes the tagged images to Docker Hub so CI/CD and other developers can reuse them. |
| `&& cd ..` | **Safe Sequential Step:** Using `&&` guarantees the agent and installer builds will only proceed if the builder image build and push succeed completely. |
| `./build.sh --installer --platform all` | **Full Packaging:** Compiles native C++ agents for both `linux64` and `win64`, packages their service ZIPs, and builds both native C++ GUI setup installers. |
| `--clean` | **Purge Stale Artifacts:** Deletes previous `dist/` outputs and intermediate `out/build/` CMake directories to guarantee an entirely clean, reproducible release. |

### Monitoring the Build Progress

To stream the build output in real time:
```bash
tail -f installer.log
```

To check if the background build process is still running:
```bash
ps aux | grep build.sh
```