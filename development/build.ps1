# PowerShell local build script for Shango
# Builds standalone Windows agent executable and Docker container for bot.
#
# Usage:
#   .\development\build.ps1                        # Build standalone shango-agent.exe
#   .\development\build.ps1 -Version "1.2.0"       # Build with specific version override
#   .\development\build.ps1 -Target All            # Build both agent binary and bot container
#   .\development\build.ps1 -CopyTo "C:\tmp\shango-agent-windows-x64.exe"

[CmdletBinding()]
param(
    [ValidateSet("Agent", "Bot", "All")]
    [string]$Target = "Agent",

    [string]$Version,

    [switch]$SkipSign,

    [string]$CopyTo = "C:\tmp\shango-agent-windows-x64.exe",

    [switch]$Clean
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

$rootDir = Split-Path -Parent $PSScriptRoot
Set-Location $rootDir

# 0. Clean old build artifacts if requested
if ($Clean) {
    Write-Info "Cleaning previous build artifacts in build/ and dist/ ..."
    if (Test-Path "$rootDir\build") { Remove-Item -Recurse -Force "$rootDir\build" }
    if (Test-Path "$rootDir\dist") { Remove-Item -Recurse -Force "$rootDir\dist" }
}

# 1. Synchronize version
Write-Info "Synchronizing version numbers from version.template ..."
$versionArgs = @("$rootDir\development\update_version.py")
if ($Version) {
    $versionArgs += @("--version", $Version)
}

python @versionArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to synchronize version."
    exit 1
}

$currentVersion = (Get-Content "$rootDir\version.txt").Trim()
$shortVersion = (Get-Content "$rootDir\short_version.txt").Trim()
Write-Info "Building version: $currentVersion (Short: $shortVersion)"

# 2. Build standalone agent binary
if ($Target -eq "Agent" -or $Target -eq "All") {
    Write-Info "=== Building standalone shango-agent executable ==="
    $buildArgs = @("$rootDir\development\build_agent.py")
    if ($Version) { $buildArgs += @("--version", $Version) }
    if ($SkipSign) { $buildArgs += @("--skip-sign") }
    if ($CopyTo) { $buildArgs += @("--copy-to", $CopyTo) }

    python @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Agent build failed."
        exit 1
    }

    $distExe = "$rootDir\dist\shango-agent.exe"
    $distZip = "$rootDir\dist\shango-agent-windows-x64.zip"
    if (Test-Path $distExe) {
        Write-Host ""
        Write-Info "=== Artifact Verification ($distExe) ==="
        (Get-Item $distExe).VersionInfo | Format-List FileDescription, FileVersion, ProductName, ProductVersion, LegalCopyright, CompanyName
        Get-AuthenticodeSignature $distExe | Format-List Status, StatusMessage, SignatureType, SignerCertificate, TimeStamperCertificate
        
        $hashExe = (Get-FileHash -Path $distExe -Algorithm SHA256).Hash
        Write-Info "Binary SHA256 Checksum: $hashExe"
    }
    if (Test-Path $distZip) {
        $hashZip = (Get-FileHash -Path $distZip -Algorithm SHA256).Hash
        Write-Info "Zip Package ($distZip) SHA256 Checksum: $hashZip"
    }
}

# 3. Build Docker container for bot (if requested)
if ($Target -eq "Bot" -or $Target -eq "All") {
    Write-Info "=== Building Docker image for shango-bot ==="
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        Write-Warn "Docker is not available. Skipping container build."
    } else {
        $timestamp = (Get-Date -Format "yyyyMMddHHmmss")
        $imgLatest = "lhotakj/shango-bot:latest"
        $imgShort = "lhotakj/shango-bot:$shortVersion"
        $imgFull = "lhotakj/shango-bot:$currentVersion"
        $imgTs = "lhotakj/shango-bot:$timestamp"

        docker build -t $imgLatest -t $imgShort -t $imgFull -t $imgTs -f Dockerfile .
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Docker image built: $imgFull"
        } else {
            Write-Err "Docker build failed."
            exit 1
        }
    }
}

Write-Info "[+] Build complete for version $currentVersion!"
