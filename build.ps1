<#
.SYNOPSIS
    Cross-platform build script for Cmdiqo standalone agent and bot container (PowerShell).

.DESCRIPTION
    Builds the standalone cmdiqo-agent executable using PyInstaller, packages distribution
    archives, applies Authenticode digital signing on Windows, calculates SHA256 checksums,
    and builds/pushes multi-tagged Docker container images for cmdiqo-bot.

.PARAMETER Target
    Component to build: 'Agent', 'Installer', 'Bot', or 'All'. Defaults to 'Agent'.

.PARAMETER Agent
    Switch shortcut to build the standalone cmdiqo-agent executable and installer.

.PARAMETER Installer
    Switch shortcut to build the standalone cmdiqo-agent and setup installer executables.

.PARAMETER Bot
    Switch shortcut to build the cmdiqo-bot Docker container image.

.PARAMETER All
    Switch shortcut to build standalone agent executable, installer, and bot Docker container.


.PARAMETER Version
    Explicit semantic version string override (e.g. '1.2.0').

.PARAMETER Dev
    Tag version as a local development build (<major>.<minor>.dev<HEX_TIMESTAMP>).

.PARAMETER SkipSign
    Skip Authenticode digital signing and certificate generation on Windows.

.PARAMETER CopyTo
    Destination path to copy the compiled binary upon build completion.
    Defaults to 'C:\tmp\cmdiqo-agent-windows-x64.exe'.

.PARAMETER Clean
    Remove previous build artifacts in 'build/' and 'dist/' directories before building.

.PARAMETER Push
    Build and push multi-architecture Docker container images to registry (linux/amd64,linux/arm64).

.PARAMETER DockerUser
    Docker Hub username or namespace. Defaults to $env:DOCKERHUB_USERNAME or 'lhotakj'.

.PARAMETER Help
    Show detailed help with explained parameters and usage examples.

.EXAMPLE
    .\build.ps1
    Build standalone cmdiqo-agent executable for Windows.

.EXAMPLE
    .\build.ps1 -Bot
    Build Docker container image for cmdiqo-bot.

.EXAMPLE
    .\build.ps1 -All -Clean -Push
    Clean artifacts, build platform binaries and multi-arch Docker images, and push to Docker Hub.

.EXAMPLE
    .\build.ps1 -Version "1.2.0" -CopyTo "C:\deploy\cmdiqo-agent.exe"
    Build with explicit version override and copy binary to deploy directory.
#>

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"
$Target = $null
$Agent = $false
$Bot = $false
$Installer = $false
$All = $false
$Version = $null
$Dev = $false
$SkipSign = $false
$CopyTo = ""
$Clean = $false
$Push = $false
$DockerUser = if ($env:DOCKERHUB_USERNAME) { $env:DOCKERHUB_USERNAME } else { "lhotakj" }
$Platform = "win64"
$Help = $false

for ($index = 0; $index -lt $CliArgs.Count; $index++) {
    $argument = $CliArgs[$index]

    function Get-OptionValue {
        param([string]$OptionName)
        if ($index + 1 -ge $CliArgs.Count) {
            throw "Option $OptionName requires a value."
        }
        $script:index++
        return $CliArgs[$script:index]
    }

    switch -Regex ($argument) {
        "^--platform$|^-p$" { $Platform = Get-OptionValue $argument; break }
        "^--platform=(.+)$" { $Platform = $Matches[1]; break }
        "^--agent$|^-a$" { $Agent = $true; break }
        "^--installer$|^-i$" { $Installer = $true; break }
        "^--bot$|^-b$|^--docker$" { $Bot = $true; break }
        "^--all$" { $All = $true; break }
        "^--target$" { $Target = Get-OptionValue $argument; break }
        "^--target=(.+)$" { $Target = $Matches[1]; break }
        "^--clean$" { $Clean = $true; break }
        "^--skip-sign$" { $SkipSign = $true; break }
        "^--copy-to$" { $CopyTo = Get-OptionValue $argument; break }
        "^--copy-to=(.+)$" { $CopyTo = $Matches[1]; break }
        "^--push$" { $Push = $true; $Bot = $true; break }
        "^--docker-user$" { $DockerUser = Get-OptionValue $argument; break }
        "^--docker-user=(.+)$" { $DockerUser = $Matches[1]; break }
        "^--dev$" { $Dev = $true; break }
        "^--version$|^-v$" { $Version = Get-OptionValue $argument; break }
        "^--version=(.+)$" { $Version = $Matches[1]; break }
        "^--help$|^-h$" { $Help = $true; break }
        default { throw "Unknown argument '$argument'. Run with --help for available options." }
    }
}

$Platform = $Platform.ToLower()
if ($Platform -notin @("win64", "linux64", "all")) {
    throw "Invalid platform '$Platform'. Expected win64, linux64, or all."
}

if ($Target) {
    $Target = $Target.ToLower()
    if ($Target -notin @("agent", "installer", "bot", "all")) {
        throw "Invalid target '$Target'. Expected agent, installer, bot, or all."
    }
}

function Write-Info($msg) {
    Write-Host "[INFO] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

function Show-Usage {
    Write-Host @"
Usage: .\build.ps1 [options]

Build Targets:
  --agent, -a          Build standalone cmdiqo-agent executable and installer (default)
  --installer, -i      Build agent executable and installer package
  --bot, -b, --docker  Build Docker container image for cmdiqo-bot
  --all                Build agent executable, installer package, and bot container
  --target <string>    Explicit target: 'agent', 'installer', 'bot', or 'all'

Platform Selection:
  --platform, -p <str> Target platform: 'win64', 'linux64', or 'all' (default: win64)

Versioning:
  --version, -v <str>  Override version string (e.g. '1.2.0')
  --dev                Tag version as development build (<major>.<minor>.dev<HEX>)

Compilation & Packaging:
  --clean              Clean build/ and dist/ directories before building
  --skip-sign          Skip Authenticode digital signing on Windows
  --copy-to <path>     Copy compiled binary to destination path upon completion

Docker Options:
  --push               Build and push multi-arch Docker images to registry
  --docker-user <usr>  Docker Hub registry username (default: `$env:DOCKERHUB_USERNAME or 'lhotakj')

Help:
  --help, -h           Show this help message and exit

Examples:
  .\build.ps1                                       # Build standalone agent binary
  .\build.ps1 --bot                                 # Build bot Docker container
  .\build.ps1 --all --clean                         # Clean build artifacts and build both
  .\build.ps1 --version "1.2.0" --copy-to "C:\bin\app.exe" # Build specific version and copy
  .\build.ps1 --bot --push                          # Build and push multi-arch container
"@ -ForegroundColor Cyan
}

if ($Help) {
    Show-Usage
    exit 0
}

$rootDir = $PSScriptRoot
Set-Location $rootDir
$windowsInstallerName = "cmdiqo-agent-windows-x64-setup.exe"
$linuxInstallerName = "cmdiqo-agent-linux-x64-setup"

# Resolve build targets
$buildAgent = $false
$buildInstaller = $false
$buildBot = $false

if ($All -or ($Target -and $Target.ToLower() -eq "all")) {
    $buildAgent = $true
    $buildInstaller = $true
    $buildBot = $true
} elseif ($Bot -or ($Target -and $Target.ToLower() -eq "bot")) {
    $buildBot = $true
} elseif ($Installer -or ($Target -and $Target.ToLower() -eq "installer")) {
    $buildAgent = $true
    $buildInstaller = $true
} elseif ($Agent -or ($Target -and $Target.ToLower() -eq "agent")) {
    $buildAgent = $true
    $buildInstaller = $true
} else {
    # Default target is Agent + Installer
    $buildAgent = $true
    $buildInstaller = $true
}

# 0. Clean old build artifacts if requested
if ($Clean) {
    Write-Info "Cleaning previous build artifacts in build/ and dist/ ..."
    if (Test-Path "$rootDir\build") { Remove-Item -Recurse -Force "$rootDir\build" }
    if (Test-Path "$rootDir\dist") { Remove-Item -Recurse -Force "$rootDir\dist" }
}

# 1. Synchronize version
Write-Info "Synchronizing version numbers from version.txt ..."
$versionArgs = @()
if ($Version) {
    $versionArgs += @("--version", $Version)
} elseif ($Dev) {
    $versionArgs += @("--dev")
}

$currentVersion = (python "$rootDir\development\update_version.py" @versionArgs --print-version).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($currentVersion)) {
    Write-Err "Failed to synchronize version."
    exit 1
}

$parts = $currentVersion.Split('.')
$shortVersion = "$($parts[0]).$($parts[1])"
Write-Info "Building target version: $currentVersion (Short: $shortVersion)"

$plat = $Platform.ToLower()
$buildWin = ($plat -eq "win64" -or $plat -eq "all")
$buildLinux = ($plat -eq "linux64" -or $plat -eq "all")

Write-Info "Platform selection: $Platform (win64: $buildWin, linux64: $buildLinux)"

# 2. Build standalone Windows agent binary
if ($buildAgent -and $buildWin) {
    Write-Info "=== Building standalone cmdiqo-agent Windows executable ==="
    $buildArgs = @("$rootDir\development\build_agent.py", "--version", $currentVersion)
    if ($SkipSign) { $buildArgs += @("--skip-sign") }
    if ($CopyTo) { $buildArgs += @("--copy-to", $CopyTo) }

    python @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Agent build failed."
        exit 1
    }

    $distExe = "$rootDir\dist\cmdiqo-agent.exe"
    $distZip = "$rootDir\dist\cmdiqo-agent-windows-x64.zip"
    if (Test-Path $distExe) {
        Write-Host ""
        Write-Info "=== Artifact Verification ($distExe) ==="
        (Get-Item $distExe).VersionInfo | Format-List FileDescription, FileVersion, ProductName, ProductVersion, LegalCopyright, CompanyName
        if (-not $SkipSign) {
            Get-AuthenticodeSignature $distExe | Format-List Status, StatusMessage, SignatureType, SignerCertificate, TimeStamperCertificate
        }
        
        $hashExe = (Get-FileHash -Path $distExe -Algorithm SHA256).Hash
        Write-Info "Binary SHA256 Checksum: $hashExe"
    }
    if (Test-Path $distZip) {
        $hashZip = (Get-FileHash -Path $distZip -Algorithm SHA256).Hash
        Write-Info "Zip Package ($distZip) SHA256 Checksum: $hashZip"
    }
}

# 2b. Build standalone Windows installer executable (single executable in dist\)
if ($buildInstaller -and $buildWin) {
    Write-Info "=== Building standalone $windowsInstallerName ==="
    python -c "import webview" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Info "PyWebView is required to build the GUI installer. Installing installer dependencies..."
        python -m pip install -r "$rootDir\requirements.txt"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not install PyWebView. Run 'python -m pip install -r requirements.txt' and try again."
        }
        python -c "import webview" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "PyWebView is required to build the GUI installer but could not be imported after installation."
        }
    }

    $payloadZip = "$rootDir\dist\cmdiqo-agent-windows-x64.zip"
    $resolvedVersionFile = "$rootDir\dist\build-version.txt"
    if (-not (Test-Path $payloadZip)) {
        Write-Info "Payload zip not found. Running build_agent.py first..."
        python "$rootDir\development\build_agent.py" --version $currentVersion
    }
    if (-not (Test-Path $resolvedVersionFile)) {
        Set-Content -Path $resolvedVersionFile -Value $currentVersion -NoNewline
    }

    # Clean previous build directory to avoid Samba rmtree collisions
    python -c "import shutil; shutil.rmtree(r'$rootDir\build\cmdiqo-agent-windows-x64-setup', ignore_errors=True)"

    # Use a local temp workpath to avoid network-share localpycs failures (PyInstaller on UNC/Samba)
    $localWorkDir = Join-Path $env:TEMP "cmdiqo-pyi-setup"
    $localSpecDir = Join-Path $env:TEMP "cmdiqo-pyi-setup-spec"
    New-Item -ItemType Directory -Force -Path $localWorkDir | Out-Null
    New-Item -ItemType Directory -Force -Path $localSpecDir | Out-Null

    $pyArgs = @(
        "-m", "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--optimize", "2",
        "--workpath", $localWorkDir,
        "--specpath", $localSpecDir,
        "--distpath", "$rootDir\dist",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "test",
        "--exclude-module", "sqlite3",
        "--exclude-module", "tkinter",
        "--name", "cmdiqo-agent-windows-x64-setup",
        "--paths", $rootDir,
        "--paths", "$rootDir\installer",
        "--add-data", "$payloadZip;.",
        "--add-data", "$rootDir\version.txt;.",
        "--add-data", "$resolvedVersionFile;.",
        "--hidden-import", "installer.backend",
        "--hidden-import", "installer.gui",
        "--uac-admin",
        "--windowed"
    )

    if (Test-Path "$rootDir\development\cmdiqo.ico") {
        $pyArgs += @(
            "--icon", "$rootDir\development\cmdiqo.ico",
            "--add-data", "$rootDir\development\cmdiqo.ico;."
        )
    }

    if (Test-Path "$rootDir\agent\version_info.txt") {
        $pyArgs += @("--version-file", "$rootDir\agent\version_info.txt")
    }

    $pyArgs += "$rootDir\installer\gui.py"

    Write-Info "Running PyInstaller (single executable mode)..."
    python @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Installer compilation failed."
        exit 1
    }

    $distSetupExe = "$rootDir\dist\$windowsInstallerName"
    if (-not (Test-Path $distSetupExe)) {
        Write-Err "Installer executable not found at $distSetupExe."
        exit 1
    }

    # Authenticode Signing
    if (-not $SkipSign) {
        Write-Info "Applying Authenticode digital signature to installer executable..."
        python -c "from development.build_agent import sign_windows_binary; from pathlib import Path; sign_windows_binary(Path(r'$distSetupExe'))"
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Installer signing failed."
            exit 1
        }
    }

    Write-Host ""
    Write-Info "=== Installer Artifact: $distSetupExe ==="
    (Get-Item $distSetupExe).VersionInfo | Format-List FileDescription, FileVersion, ProductName, ProductVersion, LegalCopyright
    if (-not $SkipSign) {
        Get-AuthenticodeSignature $distSetupExe | Format-List Status, StatusMessage, SignatureType, SignerCertificate
    }

    $hashSetupExe = (Get-FileHash -Path $distSetupExe -Algorithm SHA256).Hash
    Write-Info "Installer Executable ($distSetupExe) SHA256: $hashSetupExe"
}

# 2c. Build Linux binaries in the latest Debian stable container (if requested on Windows via Docker)
if ($buildLinux -and ($buildAgent -or $buildInstaller)) {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCmd) {
        Write-Info "=== Building Linux binaries in Debian trixie container via Docker ==="
        docker build -t cmdiqo-builder-linux:trixie -f "$rootDir\docker\Dockerfile.builder-linux" "$rootDir"
        if ($LASTEXITCODE -eq 0) {
            $linuxBashScript = "rm -rf /workspace/dist/cmdiqo-agent-linux-x64-setup /workspace/dist/_delete_me_on_linux 2>/dev/null; python3 development/build_agent.py --version $currentVersion && python3 -m PyInstaller --onefile --clean --optimize 2 --exclude-module unittest --exclude-module pydoc --exclude-module test --exclude-module sqlite3 --name cmdiqo-agent-linux-x64-setup --paths /workspace --paths /workspace/installer --additional-hooks-dir /workspace/development/pyinstaller-hooks --add-data /workspace/dist/cmdiqo-agent-linux-x64.zip:. --add-data /workspace/version.txt:. --add-data /workspace/development/cmdiqo.ico:. --collect-all webview --collect-all gi --hidden-import installer.backend --hidden-import installer.gui --hidden-import webview.platforms.gtk --strip /workspace/installer/gui.py && chmod +x /workspace/dist/cmdiqo-agent /workspace/dist/cmdiqo-agent-linux-x64-setup"
            docker run --rm -v "${rootDir}:/workspace" -w /workspace -e HOME=/tmp cmdiqo-builder-linux:trixie bash -c $linuxBashScript
            $distLinuxSetup = "$rootDir\dist\$linuxInstallerName"
            if (Test-Path $distLinuxSetup) {
                Write-Info "Linux Installer Executable ($distLinuxSetup) SHA256: $((Get-FileHash -Path $distLinuxSetup -Algorithm SHA256).Hash)"
            }
        }
    } else {
        if ($plat -eq "linux64") {
            Write-Warn "Building linux64 binaries on Windows requires Docker Desktop. Please start Docker or run build.sh on Linux."
        } else {
            Write-Info "Docker Desktop not detected; skipping linux64 binary build on Windows host."
        }
    }
}

# Unified installer verification
Write-Host ""
Write-Info "=== Installer Artifacts Verification in dist\ ==="
$installerFiles = @("$rootDir\dist\$windowsInstallerName", "$rootDir\dist\$linuxInstallerName")
foreach ($f in $installerFiles) {
    if (Test-Path $f -PathType Leaf) {
        $mb = [math]::Round(((Get-Item $f).Length / 1MB), 2)
        $h = (Get-FileHash -Path $f -Algorithm SHA256).Hash
        Write-Info "  + Found: $f ($mb MB)"
        Write-Info "    SHA256: $h"
    }
}
if ($buildWin -and -not (Test-Path "$rootDir\dist\$windowsInstallerName" -PathType Leaf)) {
    Write-Info "  * Note: Windows installer ($windowsInstallerName) can be built on Windows (.\build.ps1 -Installer)."
}
if ($buildLinux -and -not (Test-Path "$rootDir\dist\$linuxInstallerName" -PathType Leaf)) {
    Write-Info "  * Note: Linux installer ($linuxInstallerName) can be built on Linux (./build.sh --installer) or with Docker Desktop."
}

# 3. Build Docker container for bot (if requested)
if ($buildBot) {
    Write-Info "=== Building Docker image for cmdiqo-bot ==="
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        Write-Warn "Docker CLI is not installed or not in PATH. Skipping container build."
    } else {
        $timestamp = (Get-Date -Format "yyyyMMddHHmmss")
        $imgLatest = "$DockerUser/cmdiqo-bot:latest"
        $imgShort = "$DockerUser/cmdiqo-bot:$shortVersion"
        $imgFull = "$DockerUser/cmdiqo-bot:$currentVersion"
        $imgTs = "$DockerUser/cmdiqo-bot:$timestamp"

        if ($Push) {
            Write-Info "Building and pushing multi-arch images ($imgFull) via buildx ..."
            docker buildx build `
                --platform linux/amd64,linux/arm64 `
                --no-cache `
                -t $imgLatest `
                -t $imgShort `
                -t $imgFull `
                -t $imgTs `
                -f docker/Dockerfile `
                --push `
                .
            if ($LASTEXITCODE -eq 0) {
                Write-Info "Docker multi-arch images built and pushed successfully."
            } else {
                Write-Err "Docker multi-arch build/push failed."
                exit 1
            }
        } else {
            Write-Info "Building local container image ($imgFull) ..."
            docker build `
                -t $imgLatest `
                -t $imgShort `
                -t $imgFull `
                -t $imgTs `
                -f docker/Dockerfile `
                .
            if ($LASTEXITCODE -eq 0) {
                Write-Info "Built container image: $imgFull"
            } else {
                Write-Err "Docker build failed."
                exit 1
            }
        }
    }
}

Write-Host ""
Write-Info "=== Distribution Binary Artifacts (dist\) ==="
Write-Info "Windows (x64):"
if (Test-Path "$rootDir\dist\cmdiqo-agent.exe" -PathType Leaf) {
    Write-Info "  • Application Binary:     $rootDir\dist\cmdiqo-agent.exe ($([math]::Round((Get-Item "$rootDir\dist\cmdiqo-agent.exe").Length / 1MB, 2)) MB)"
} else {
    Write-Warn "  • Application Binary:     Missing"
}
if (Test-Path "$rootDir\dist\$windowsInstallerName" -PathType Leaf) {
    Write-Info "  • Installer Binary:       $rootDir\dist\$windowsInstallerName ($([math]::Round((Get-Item "$rootDir\dist\$windowsInstallerName").Length / 1MB, 2)) MB)"
} else {
    Write-Warn "  • Installer Binary:       Missing"
}
Write-Info "Linux (x86_64):"
if (Test-Path "$rootDir\dist\cmdiqo-agent" -PathType Leaf) {
    Write-Info "  • Application Binary:     $rootDir\dist\cmdiqo-agent ($([math]::Round((Get-Item "$rootDir\dist\cmdiqo-agent").Length / 1MB, 2)) MB)"
} else {
    Write-Warn "  • Application Binary:     Missing"
}
if (Test-Path "$rootDir\dist\$linuxInstallerName" -PathType Leaf) {
    Write-Info "  • Installer Binary:       $rootDir\dist\$linuxInstallerName ($([math]::Round((Get-Item "$rootDir\dist\$linuxInstallerName").Length / 1MB, 2)) MB)"
} else {
    Write-Warn "  • Installer Binary:       Missing"
}

if ($buildAgent -and $buildWin -and -not (Test-Path "$rootDir\dist\cmdiqo-agent.exe" -PathType Leaf)) {
    throw "Requested Windows agent binary was not produced."
}
if ($buildInstaller -and $buildWin -and -not (Test-Path "$rootDir\dist\$windowsInstallerName" -PathType Leaf)) {
    throw "Requested Windows installer was not produced."
}
if ($buildAgent -and $buildLinux -and -not (Test-Path "$rootDir\dist\cmdiqo-agent" -PathType Leaf)) {
    throw "Requested Linux agent binary was not produced. Docker Desktop must be running."
}
if ($buildInstaller -and $buildLinux -and -not (Test-Path "$rootDir\dist\$linuxInstallerName" -PathType Leaf)) {
    throw "Requested Linux installer was not produced. Docker Desktop must be running."
}

Write-Info "[+] Build complete successfully for version $currentVersion!"
