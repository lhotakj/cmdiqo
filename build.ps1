<#
.SYNOPSIS
    Cross-platform build script for Cmdiqo standalone agent and bot container (PowerShell).

.DESCRIPTION
    Builds the standalone cmdiqo-agent executable using PyInstaller, packages distribution
    archives, applies Authenticode digital signing on Windows, calculates SHA256 checksums,
    and builds/pushes multi-tagged Docker container images for cmdiqo-bot.

.PARAMETER Target
    Component to build: 'Agent', 'Bot', or 'All'. Defaults to 'Agent'.

.PARAMETER Agent
    Switch shortcut to build the standalone cmdiqo-agent executable.

.PARAMETER Bot
    Switch shortcut to build the cmdiqo-bot Docker container image.

.PARAMETER All
    Switch shortcut to build both standalone agent executable and bot Docker container.

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
    Clean artifacts, build agent binary and multi-arch Docker images, and push to Docker Hub.

.EXAMPLE
    .\build.ps1 -Version "1.2.0" -CopyTo "C:\deploy\cmdiqo-agent.exe"
    Build with explicit version override and copy binary to deploy directory.
#>

[CmdletBinding(DefaultParameterSetName = "Default")]
param(
    [Parameter(ParameterSetName = "Default")]
    [ValidateSet("Agent", "Bot", "All", IgnoreCase = $true)]
    [string]$Target,

    [Parameter(ParameterSetName = "AgentSet")]
    [switch]$Agent,

    [Parameter(ParameterSetName = "BotSet")]
    [switch]$Bot,

    [Parameter(ParameterSetName = "AllSet")]
    [switch]$All,

    [Alias("v")]
    [string]$Version,

    [switch]$Dev,

    [switch]$SkipSign,

    [string]$CopyTo = "C:\tmp\cmdiqo-agent-windows-x64.exe",

    [switch]$Clean,

    [switch]$Push,

    [string]$DockerUser = $(if ($env:DOCKERHUB_USERNAME) { $env:DOCKERHUB_USERNAME } else { "lhotakj" }),

    [Alias("h", "?")]
    [switch]$Help
)

$ErrorActionPreference = "Stop"

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
  -Agent               Build standalone cmdiqo-agent executable (default)
  -Bot                 Build Docker container image for cmdiqo-bot
  -All                 Build both agent executable and bot container
  -Target <string>     Explicit target: 'Agent', 'Bot', or 'All'

Versioning:
  -Version, -v <str>   Override version string (e.g. '1.2.0')
  -Dev                 Tag version as development build (<major>.<minor>.dev<HEX>)

Compilation & Packaging:
  -Clean               Clean build/ and dist/ directories before building
  -SkipSign            Skip Authenticode digital signing on Windows
  -CopyTo <path>       Copy compiled binary to destination path upon completion
                       (default: C:\tmp\cmdiqo-agent-windows-x64.exe)

Docker Options:
  -Push                Build and push multi-arch Docker images to registry
  -DockerUser <string> Docker Hub registry username (default: `$env:DOCKERHUB_USERNAME or 'lhotakj')

Help:
  -Help, -h, -?        Show this help message and exit

Examples:
  .\build.ps1                                       # Build standalone agent binary
  .\build.ps1 -Bot                                  # Build bot Docker container
  .\build.ps1 -All -Clean                           # Clean build artifacts and build both
  .\build.ps1 -Version "1.2.0" -CopyTo "C:\bin\app.exe" # Build specific version and copy
  .\build.ps1 -Bot -Push                            # Build and push multi-arch container
"@ -ForegroundColor Cyan
}

if ($Help) {
    Show-Usage
    exit 0
}

$rootDir = $PSScriptRoot
Set-Location $rootDir

# Resolve build targets
$buildAgent = $false
$buildBot = $false

if ($All -or ($Target -and $Target.ToLower() -eq "all")) {
    $buildAgent = $true
    $buildBot = $true
} elseif ($Bot -or ($Target -and $Target.ToLower() -eq "bot")) {
    $buildBot = $true
} elseif ($Agent -or ($Target -and $Target.ToLower() -eq "agent")) {
    $buildAgent = $true
} else {
    # Default target is Agent
    $buildAgent = $true
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

# 2. Build standalone agent binary
if ($buildAgent) {
    Write-Info "=== Building standalone cmdiqo-agent executable ==="
    $buildArgs = @("$rootDir\development\build_agent.py")
    if ($Version) { $buildArgs += @("--version", $Version) }
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

Write-Info "[+] Build complete successfully for version $currentVersion!"
