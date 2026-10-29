# Native installer development

`installer.c` contains the C++20, wxWidgets-based native setup application
named `cmdiqo-agent-setup`. Its release build is invoked only by the
repository-root build scripts; `installer.c` deliberately has no `build.sh` or
`build.ps1`.

## Release builds

From the repository root, `--installer` / `-Installer` builds the Release platform
agent, then compiles, tests, and packages the native installer with the agent binary directly embedded into the setup executable. The setup executable is completely self-contained and does not require an external ZIP payload on the target machine.

All native C++ installer binaries are built in **`Release`** configuration with compiler optimizations and stripped symbols.

### Building on Linux / macOS (Bash)

```bash
# Build Linux agent and native Linux setup (Release by default):
./build.sh --installer

# Cross-build Windows setup via MinGW-w64, using a binary built on Windows:
./build.sh --installer --platform win64 --skip-agent-build

# Reuse existing binary and rebuild only the native setup:
./build.sh --installer --skip-agent-build

# Rebuild both setups from their existing platform binaries:
./build.sh --installer --platform all --skip-agent-build
```

### Building on Windows (PowerShell)

```powershell
# Build Windows agent and native Windows setup (Release by default):
.\build.ps1 -Installer

# Reuse existing Windows agent binary and rebuild only the native setup:
.\build.ps1 -Installer -SkipAgentBuild

# Build Linux agent and native Linux setup using Docker Desktop:
.\build.ps1 -Installer -Platform Linux

# Build both agents and both setups:
.\build.ps1 -Installer -Platform All
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
`major.minor.<GitHub-run-number>` in CI, or
`major.minor.devel+<hex-timestamp>` locally; explicit numeric overrides such as
`1.2.0` are also accepted for local builds. GitHub Actions uses the workflow
run number as the patch even when a release request supplies a baseline
version. The Release executables are
stripped after linking. MinGW Windows builds statically link the C/C++ runtime
(`-static -static-libgcc -static-libstdc++`) via the `x64-mingw-static` vcpkg
triplet so the output binary runs on any 64-bit Windows host without external MinGW DLLs. The native Windows GUI installer (`cmdiqo-agent-windows-x64-setup.exe`) is super-lightweight, taking only **~5.2 MB RAM** during execution while providing an interactive setup wizard with UAC elevation, token validation, and automated Windows Service (SCM) registration.

## Docker build architecture & Pre-built Docker Hub Images

The Docker build environment is 100% self-contained within a single multi-stage
[Dockerfile](file:///h:/WORKING/cmdiqo/installer.c/Dockerfile). All vcpkg dependencies are pinned to tag `2026.07.29` and pre-compiled into one install root per triplet:

| Environment variable | Install root | Triplet |
|---|---|---|
| `VCPKG_INSTALLED_DIR_LINUX` | `/opt/cmdiqo-vcpkg-installed/linux` | `x64-linux-release` |
| `VCPKG_INSTALLED_DIR_WINDOWS` | `/opt/cmdiqo-vcpkg-installed/windows-mingw` | `x64-mingw-static-release` |

Separate roots are mandatory: a vcpkg manifest-mode install behaves like
`x-set-installed` and deletes everything from the install root that is not part of the
requested plan, so a shared root would lose the Linux packages when the MinGW packages
are installed in the `windows`/`all` stages. `VCPKG_INSTALLED_DIR` still points at the
default root of the stage (Linux for `linux`/`all`, MinGW for `windows`), and the build
scripts pass the matching platform-specific root to CMake. No external host caches or
directory mounts are required.

The root Bash and PowerShell Docker routes both use
`lhotakj/cmdiqo-installer-builder:all`, even for a single-platform installer
build. The optional local MSVC route in PowerShell remains available. The
platform-specific image stages below are available for manual use, but are not
selected by the root build scripts.

All native-installer containers target `linux/amd64`. CMake consumes the selected
pre-built root with `VCPKG_MANIFEST_INSTALL=OFF`; a missing dependency fails the
build instead of triggering a second vcpkg installation. Linux tests run
natively, and Windows tests run through `wine64`, available on the image's
`PATH`. The root scripts and builder workflow fail on either test suite's
errors and give Wine a writable `WINEPREFIX=/tmp/cmdiqo-wine`.

Windows agent packaging is separate from C++ cross-compilation: PyInstaller
needs native Windows Python. On Unix, first obtain the Windows ZIP from
`build.ps1` or the Windows CI runner and put it in `dist/`; then use
`--skip-agent-build`. On Windows Git Bash, `build.sh` defaults to `win64` and
uses native Windows Python for the agent, with Docker for the setup.

### GUI backends and Linux-hosted Windows cross-compilation

The same application source uses **wxGTK with GTK3 on Linux** and **wxMSW with
native Windows controls on Windows**. Both can be compiled inside the `all`
Linux image: native GCC builds the Linux executable, and MinGW-w64 builds the
Windows executable. Linux GTK3 development libraries are not Windows target
libraries; the Windows executable does not depend on GTK3.

The overlay triplets configure wxWidgets through vcpkg's supported
`VCPKG_CMAKE_CONFIGURE_OPTIONS`, scoped to `PORT STREQUAL "wxwidgets"`:

| Triplet | wxWidgets configuration |
|---|---|
| `x64-linux-release` | `wxBUILD_TOOLKIT=gtk3` |
| `x64-mingw-static-release` | `wxBUILD_TOOLKIT=msw`, `wxBUILD_WIN32_MSVC_NAMING=ON` |

The explicit Windows naming option is important even though the compiler is
MinGW, not MSVC. With its default `AUTO` setting, wxWidgets chooses Unix-style
library names and versioned include directories for Linux-hosted cross-builds.
The vcpkg Windows package finder expects Windows-style names such as
`libwxbase33u.a` and the `include/wx` header layout. Selecting the upstream
option aligns the producer and consumer without rewriting portfiles, renaming
libraries, skipping header processing, or forcing `CMAKE_CROSSCOMPILING` off
in application configuration.

References:

- [wxWidgets naming option and cross-compilation defaults](https://github.com/wxWidgets/wxWidgets/blob/v3.3.1/build/cmake/options.cmake#L96-L110)
- [vcpkg Windows wxWidgets package finder](https://github.com/microsoft/vcpkg/blob/2026.07.29/ports/wxwidgets/vcpkg-cmake-wrapper.cmake#L11-L32)
- [Supported vcpkg per-port triplet customization](https://learn.microsoft.com/en-us/vcpkg/users/triplets#per-port-customization)
- [Supported vcpkg CMake configuration options](https://learn.microsoft.com/en-us/vcpkg/users/triplets#vcpkg_cmake_configure_options)

Dockerfile and triplet changes invalidate the builder-input hash. Rebuild and
republish the affected images rather than reusing the previous pre-built
dependencies. The builder workflow compiles the real installer for both targets
with manifest installation disabled, so it cannot mask a bad pre-built package
by downloading or rebuilding dependencies.

Pre-built builder images are published to Docker Hub under `lhotakj/cmdiqo-installer-builder:*`:

| Target Stage (`Dockerfile`) | Docker Hub Image Tag | Local Image Tag | Toolchain & Triplet | Description |
|---|---|---|---|---|
| `target: linux` | `lhotakj/cmdiqo-installer-builder:ubuntu-22.04` (and `:linux`) | `cmdiqo-installer-builder:ubuntu-22.04` | GCC + GTK3 (`x64-linux`) | Native Linux installer builder |
| `target: windows` | `lhotakj/cmdiqo-installer-builder:windows-mingw` (and `:windows`) | `cmdiqo-installer-builder:windows-mingw` | MinGW-w64 GCC (`x64-mingw-static`) | Windows x64 cross-compiler builder with Wine test runner |
| `target: all` | `lhotakj/cmdiqo-installer-builder:all` (and `:ubuntu-22.04-all`) | `cmdiqo-installer-builder:all` | Combined dual toolchains | Unified builder image with toolchain switch |

### Smart Pre-built Image Resolution

When `./build.sh --installer` or `.\build.ps1 -Installer` runs:
1. It calculates the SHA-256 hash of `Dockerfile` + `vcpkg.json` + the sorted overlay triplet files, normalizing checkout CRLF line endings to LF.
2. It checks if a local image with matching `io.cmdiqo.installer-builder.build-input-sha256` exists.
3. If not present locally, it pulls `lhotakj/cmdiqo-installer-builder:all` and requires the same hash. Images with absent labels are not trusted.
4. If pulling is unavailable or the hash differs, it builds target `all` locally with `docker buildx build --load --platform linux/amd64`. This also works with the `docker-container` Buildx driver.

### GitHub Actions Dispatcher Pipeline

The workflow `.github/workflows/builder-image.yml` provides an automated dispatcher to build and publish the builder images to Docker Hub:
- Can be triggered manually via `workflow_dispatch` with target platform choices (`all`, `linux`, `windows`).
- Automatically triggers on `push` to `main` only when `installer.c/Dockerfile` is modified.

## Building and publishing Docker builder images (`setup-env.sh` / `setup-env.ps1`)

Use the setup scripts to pre-build, refresh, or push Docker builder images:

### Linux / macOS:
```bash
cd installer.c

# Prepare the unified Docker builder locally (default):
./setup-env.sh

# Prepare a Linux-only image for manual use:
./setup-env.sh --platform linux

# Prepare Windows MinGW-w64 Docker builder locally:
./setup-env.sh --platform windows

# Prepare the unified builder image locally:
./setup-env.sh --platform all

# Build and push all builder images to Docker Hub:
./setup-env.sh --platform all --docker-user lhotakj --push
```

### Windows:
```powershell
Set-Location installer.c

# Prepare the unified Docker builder locally (default):
.\setup-env.ps1

# Prepare a Windows-only image for manual use:
.\setup-env.ps1 -Platform Windows

# Prepare Linux Docker builder locally:
.\setup-env.ps1 -Platform Linux

# Prepare the unified Docker builder locally:
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

### Linux (Native Desktop with GTK3 GUI):
```bash
cd installer.c
export VCPKG_ROOT="$PWD/.vcpkg"
# Release (default):
cmake --preset linux-x64
cmake --build --preset build-linux-x64
ctest --preset test-linux-x64
```

### Linux (Headless Cloud / Server CLI setup - zero GTK/X11 dependencies):
```bash
cd installer.c
export VCPKG_ROOT="$PWD/.vcpkg"
# Headless CLI Release:
cmake --preset linux-x64-cli
cmake --build --preset build-linux-x64-cli
ctest --preset test-linux-x64-cli
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
