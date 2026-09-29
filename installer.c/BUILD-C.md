# Native installer development

`installer.c` contains the C++20, wxWidgets-based native setup application
named `cmdiqo-agent-setup`. Its release build is invoked only by the
repository-root build scripts; `installer.c` deliberately has no `build.sh` or
`build.ps1`.

## Release builds

From the repository root, `--installer` / `-Installer` builds the Release platform
agent, creates its ZIP, verifies the ZIP's `version.txt`, then compiles,
tests, and packages the native installer. The matching ZIP and setup executable are published together in `dist/` so
payload discovery works without extra arguments.

All native C++ installer binaries are built in **`Release`** configuration with compiler optimizations and stripped symbols.

### Building on Linux / macOS (Bash)

```bash
# Build Linux agent package and native Linux setup (Release by default):
./build.sh --installer

# Build Windows agent package and native Windows setup in Docker via MinGW-w64:
./build.sh --installer --platform win64

# Reuse existing ZIP package and rebuild only the native setup:
./build.sh --installer --skip-agent-build
```

### Building on Windows (PowerShell)

```powershell
# Build Windows agent ZIP and native Windows setup (Release by default):
.\build.ps1 -Installer

# Reuse existing Windows agent ZIP and rebuild only the native setup:
.\build.ps1 -Installer -SkipAgentBuild

# Build Linux agent ZIP and native Linux setup using Docker Desktop:
.\build.ps1 -Installer -Platform Linux

```

### Produced Artifacts in `dist/`

The Linux build produces:

```text
dist/
  cmdiqo-agent
  cmdiqo-agent-linux-x64.zip
  cmdiqo-agent-linux-x64-setup
```

The Windows build produces:

```text
dist/
  cmdiqo-agent.exe
  cmdiqo-agent-windows-x64.zip
  cmdiqo-agent-windows-x64-setup.exe
```

From Windows, `.\build.ps1 -Installer -Platform Linux` uses Docker Desktop to
produce the Linux pair. `--all` / `-All` also includes the native installer in
addition to the agent package and bot image.

Release installers embed the precise version read from the payload ZIP:
`major.minor.<12-character-git-sha>` in CI, or
`major.minor.devel+<hex-timestamp>` locally. The Release executables are
stripped after linking. MinGW Windows builds statically link the C/C++ runtime
(`-static -static-libgcc -static-libstdc++`) via the `x64-mingw-static` vcpkg
triplet so the output binary runs on any 64-bit Windows host without external MinGW DLLs.

## Docker build architecture & Pre-built Docker Hub Images

The Docker build environment is 100% self-contained within a single multi-stage
[Dockerfile](file:///h:/WORKING/cmdiqo/installer.c/Dockerfile). All vcpkg dependencies are pinned to tag `2026.07.29` and pre-compiled into `/opt/cmdiqo-vcpkg-installed`. No external host caches or directory mounts are required.

Pre-built builder images are published to Docker Hub under `lhotakj/cmdiqo-installer-builder:*`:

| Target Stage (`Dockerfile`) | Docker Hub Image Tag | Local Image Tag | Toolchain & Triplet | Description |
|---|---|---|---|---|
| `target: linux` | `lhotakj/cmdiqo-installer-builder:ubuntu-22.04` (and `:linux`) | `cmdiqo-installer-builder:ubuntu-22.04` | GCC + GTK3 (`x64-linux`) | Native Linux installer builder |
| `target: windows` | `lhotakj/cmdiqo-installer-builder:windows-mingw` (and `:windows`) | `cmdiqo-installer-builder:windows-mingw` | MinGW-w64 GCC (`x64-mingw-static`) | Windows x64 cross-compiler builder with Wine test runner |
| `target: all` | `lhotakj/cmdiqo-installer-builder:all` (and `:ubuntu-22.04-all`) | `cmdiqo-installer-builder:all` | Combined dual toolchains | Unified builder image with toolchain switch |

### Smart Pre-built Image Resolution

When `./build.sh --installer` or `.\build.ps1 -Installer` runs:
1. It calculates the SHA-256 hash of `Dockerfile` + `vcpkg.json`.
2. It checks if a local image with matching `io.cmdiqo.installer-builder.build-input-sha256` exists.
3. If not present locally, it automatically pulls the matching pre-built image from Docker Hub (`lhotakj/cmdiqo-installer-builder:...`), eliminating compilation time.
4. If pulling is unavailable or local changes were made to `Dockerfile` / `vcpkg.json`, it cleanly falls back to building the Docker image locally.

### GitHub Actions Dispatcher Pipeline

The workflow `.github/workflows/build-installer-builder.yml` provides an automated dispatcher to build and publish the builder images to Docker Hub:
- Can be triggered manually via `workflow_dispatch` with target platform choices (`all`, `linux`, `windows`).
- Automatically triggers on `push` to `main` when `installer.c/Dockerfile`, `vcpkg.json`, or `setup-env.*` are modified.

## Building and publishing Docker builder images (`setup-env.sh` / `setup-env.ps1`)

Use the setup scripts to pre-build, refresh, or push Docker builder images:

### Linux / macOS:
```bash
cd installer.c

# Prepare Linux Docker builder locally:
./setup-env.sh

# Prepare Windows MinGW-w64 Docker builder locally:
./setup-env.sh --platform windows

# Prepare both builder images locally:
./setup-env.sh --platform all

# Build and push all builder images to Docker Hub:
./setup-env.sh --platform all --docker-user lhotakj --push
```

### Windows:
```powershell
Set-Location installer.c

# Prepare Windows MinGW-w64 Docker builder locally (default):
.\setup-env.ps1

# Prepare Linux Docker builder locally:
.\setup-env.ps1 -Platform Linux

# Prepare all Docker builders locally:
.\setup-env.ps1 -Platform All

# Build and push all builder images to Docker Hub:
.\setup-env.ps1 -Platform All -Push
```

## Manual CMake development builds

Use these commands only while iterating on the C++ project. They create a
build executable without packaging an agent ZIP.

### Windows (Local MSVC):
```powershell
Set-Location installer.c
$env:VCPKG_ROOT = (Join-Path (Get-Location) ".vcpkg")
# Release (default):
cmake --preset windows-x64
cmake --build --preset build-windows-x64
ctest --preset test-windows-x64

```

### Windows (MinGW-w64 Cross-compilation in Docker or Linux host):
```bash
cd installer.c
export VCPKG_ROOT="$PWD/.vcpkg"
# Release (default):
cmake --preset windows-x64-mingw
cmake --build --preset build-windows-x64-mingw
ctest --preset test-windows-x64-mingw

```

### Linux (Native):
```bash
cd installer.c
export VCPKG_ROOT="$PWD/.vcpkg"
# Release (default):
cmake --preset linux-x64
cmake --build --preset build-linux-x64
ctest --preset test-linux-x64

```

## Application behavior

The GUI supports install, upgrade, repair, uninstall, purge, service control,
configuration, and a TCP Hub reachability test. It falls back to CLI mode on a
Linux host without `DISPLAY` or `WAYLAND_DISPLAY`; use `--help` for the CLI
contract.

The application validates every ZIP entry before extraction. Absolute paths,
drive-rooted paths, and traversal entries are rejected. On Linux, privileged
GUI actions use PolicyKit (`pkexec`) and headless operations use `sudo`; the
application never collects a system password. Windows uses the normal UAC
prompt.

Before elevation, GUI diagnostics are written to
`~/.config/cmdiqo-installer/installer.log` (or
`$XDG_CONFIG_HOME/cmdiqo-installer/installer.log`). Elevated installation
operations additionally write to `/var/log/cmdiqo-agent/installer.log` on Linux
or `%ProgramData%\Cmdiqo\Agent\logs\installer.log` on Windows.

The executable discovers only the exact platform ZIP filename beside itself,
in a sibling `payloads` directory, or in its current working directory. Keep
the paired release ZIP with the executable when distributing it.
