# Native installer development

`installer.c` contains the C++20, wxWidgets-based native setup application
named `cmdiqo-agent-setup`. Its release build is invoked only by the
repository-root build scripts; `installer.c` deliberately has no `build.sh` or
`build.ps1`.

## Release builds

From the repository root, `--installer` / `-Installer` builds the platform
agent, creates its ZIP, verifies the ZIP's `version.txt`, then compiles,
tests, and packages the native installer. The matching ZIP and setup executable are published together in `dist/` so
payload discovery works without extra arguments.

```bash
./build.sh --installer
# Reuse dist/cmdiqo-agent-linux-x64.zip and rebuild only the native setup:
./build.sh --installer --skip-agent-build
```

```powershell
.\build.ps1 -Installer
# Reuse the existing Windows agent ZIP and rebuild only the native setup:
.\build.ps1 -Installer -SkipAgentBuild
```

The Linux command uses the Ubuntu 22.04 native-builder Docker image and
produces:

```text
dist/
  cmdiqo-agent-linux-x64-setup
  cmdiqo-agent-linux-x64.zip
```

The Windows command uses the native Visual C++ toolchain and produces:

```text
dist/
  cmdiqo-agent-windows-x64-setup.exe
  cmdiqo-agent-windows-x64.zip
```

From Windows, `.\build.ps1 -Installer -Platform Linux` uses Docker Desktop to
produce the Linux pair. `--all` / `-All` also includes the native installer in
addition to the agent package and bot image.

Release installers embed the precise version read from the payload ZIP:
`major.minor.<12-character-git-sha>` in CI, or
`major.minor.devel+<hex-timestamp>` locally. The Linux Release executable is
stripped after linking. Do not use UPX or switch it to dynamic linking without
validating all target distributions.

## One-time native environment setup

The release commands above build the Linux Docker image automatically. Use the
setup scripts only to prepare a local Windows or local Linux CMake/vcpkg
development environment:

```powershell
Set-Location installer.c
.\setup-env.ps1
```

```bash
cd installer.c
./setup-env.sh --local
```

Windows setup requires Git, CMake 3.24+, Ninja, and the Visual Studio C++
Desktop Development workload. Linux local setup supports Ubuntu/Debian and
requires `sudo` only to install host build prerequisites. It does not change
Docker group membership.

## Manual CMake development builds

Use these commands only while iterating on the C++ project. They create a
Debug executable and do not package an agent ZIP.

```powershell
Set-Location installer.c
$env:VCPKG_ROOT = (Join-Path (Get-Location) ".vcpkg")
cmake --preset windows-x64
cmake --build --preset build-windows-x64
ctest --preset test-windows-x64
```

```bash
cd installer.c
export VCPKG_ROOT="$PWD/.vcpkg"
cmake --preset linux-x64
cmake --build --preset build-linux-x64
ctest --preset test-linux-x64
```

For containerized Linux development, use the repository-root release command
instead of manually constructing Docker mounts, cache variables, and the
prebuilt vcpkg environment.

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
operations additionally write to `/var/log/cmdiqo-agent/installer.log`.

The executable discovers only the exact platform ZIP filename beside itself,
in a sibling `payloads` directory, or in its current working directory. Keep
the paired release ZIP with the executable when distributing it.
