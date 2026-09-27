<#
.SYNOPSIS
Prepares the native Windows environment or the optional Linux Docker builder.

.EXAMPLE
.\setup-env.ps1

.EXAMPLE
.\setup-env.ps1 -Platform Linux
#>
[CmdletBinding()]
param(
    [ValidateSet("Windows", "Linux")]
    [string]$Platform = "Windows",
    [ValidateSet("x64-windows")]
    [string]$Triplet = "x64-windows",
    [switch]$RebuildImage
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Vcpkg = Join-Path $Root ".vcpkg"
$Image = "cmdiqo-installer-builder:ubuntu-22.04"

function Test-DockerImage {
    param(
        [string]$Name,
        [string]$Dockerfile
    )

    $expectedHash = Get-BuilderInputHash $Dockerfile
    $actualHash = & docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' $Name 2>$null
    return $LASTEXITCODE -eq 0 -and $actualHash -eq $expectedHash
}

function Get-BuilderInputHash {
    param([string]$Dockerfile)

    $manifest = Join-Path $Root "vcpkg.json"
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($Path in @($Dockerfile, $manifest)) {
            $bytes = [System.IO.File]::ReadAllBytes($Path)
            [void]$sha256.TransformBlock($bytes, 0, $bytes.Length, $bytes, 0)
        }
        [void]$sha256.TransformFinalBlock([byte[]]::new(0), 0, 0)
        return ([System.BitConverter]::ToString($sha256.Hash) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Test-SupportedCmake {
    $versionOutput = & cmake --version
    if ($LASTEXITCODE -ne 0 -or $versionOutput.Count -eq 0) {
        return $false
    }
    if ($versionOutput[0] -notmatch '(\d+\.\d+\.\d+)') {
        return $false
    }
    return [version]$Matches[1] -ge [version]"3.24.0"
}

if ($Platform -eq "Linux") {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker Desktop is required for the Linux builder. Install and start Docker Desktop, then rerun: .\setup-env.ps1 -Platform Linux"
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop was found but its daemon is unavailable. Start Docker Desktop, then rerun this command."
    }

    $Dockerfile = Join-Path $Root "Dockerfile.linux"
    if (-not (Test-Path $Dockerfile)) {
        throw "Linux Dockerfile was not found at $Dockerfile"
    }
    if ($RebuildImage -or -not (Test-DockerImage $Image $Dockerfile)) {
        $BuildArguments = @("build", "--tag", $Image, "--file", $Dockerfile)
        if ($RebuildImage) {
            $BuildArguments += "--pull"
        }
        $BuilderInputHash = Get-BuilderInputHash $Dockerfile
        $BuildArguments += @("--build-arg", "BUILDER_INPUT_SHA=$BuilderInputHash", $Root)
        & docker @BuildArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Docker image build failed."
        }
    }
    Write-Host "Linux Docker builder is ready. Build from the repository root with: .\build.ps1 -Installer -Platform Linux"
    return
}

if ($RebuildImage) {
    throw "-RebuildImage is only valid with -Platform Linux."
}

foreach ($Tool in @("git", "cmake")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "$Tool is required. Install Git for Windows and CMake 3.24 or later, then run this script again."
    }
}
if (-not (Test-SupportedCmake)) {
    throw "CMake 3.24 or later is required. Upgrade CMake, restart the terminal, then run this script again."
}

if (-not (Get-Command ninja -ErrorAction SilentlyContinue)) {
    throw "Ninja is required. Install it with winget install Ninja-build.Ninja, restart the terminal, then rerun this script."
}

if (-not (Test-Path $Vcpkg)) {
    Write-Host "Cloning vcpkg into $Vcpkg"
    git clone https://github.com/microsoft/vcpkg.git $Vcpkg
}

$VcpkgExe = Join-Path $Vcpkg "vcpkg.exe"
if (-not (Test-Path $VcpkgExe)) {
    & (Join-Path $Vcpkg "bootstrap-vcpkg.bat") -disableMetrics
}

$env:VCPKG_ROOT = $Vcpkg
Write-Host "Installing native installer dependencies for $Triplet"
& $VcpkgExe install "--x-manifest-root=$Root" "--triplet=$Triplet"

Write-Host ""
Write-Host "Environment is ready. Build from the repository root with:"
Write-Host "  Set-Location $(Split-Path -Parent $Root)"
Write-Host "  .\build.ps1 -Installer"
