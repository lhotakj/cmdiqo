<#
.SYNOPSIS
Prepares Docker builder images for native installer compilation using the multi-stage Dockerfile.
All toolchains, tools, and vcpkg dependencies are built directly inside the container.
No checkouts or package installations are performed on the host.

.PARAMETER Platform
Target builder platform: 'Windows', 'Linux', or 'All' (default: Windows).

.PARAMETER DockerUser
Docker Hub username/organization (defaults to $env:DOCKERHUB_USERNAME or 'lhotakj').

.PARAMETER Push
Push built builder images to Docker Hub.

.PARAMETER RebuildImage
Force rebuilding the image with base image refresh (--pull).

.EXAMPLE
.\setup-env.ps1

.EXAMPLE
.\setup-env.ps1 -Platform Windows

.EXAMPLE
.\setup-env.ps1 -Platform Linux

.EXAMPLE
.\setup-env.ps1 -Platform All -Push
#>
[CmdletBinding()]
param(
    [ValidateSet("Windows", "Linux", "All")]
    [string]$Platform = "Windows",
    [switch]$RebuildImage,
    [switch]$Push,
    [string]$DockerUser = $(if ($env:DOCKERHUB_USERNAME) { $env:DOCKERHUB_USERNAME } else { "lhotakj" })
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Dockerfile = Join-Path $Root "Dockerfile"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required. Install and start Docker Desktop, then rerun this script."
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop was found but its daemon is unavailable. Start Docker Desktop, then rerun this command."
}

function Get-BuilderInputHash {
    param([string]$TargetFile)

    $manifest = Join-Path $Root "vcpkg.json"
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $triplets = Get-ChildItem -Path (Join-Path $Root "triplets") -File -Recurse |
            Sort-Object FullName |
            Select-Object -ExpandProperty FullName
        foreach ($Path in @($TargetFile, $manifest) + @($triplets)) {
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

function Test-DockerImage {
    param(
        [string]$Name,
        [string]$TargetFile
    )

    $expectedHash = Get-BuilderInputHash $TargetFile
    $actualHash = & docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' $Name 2>$null
    return $LASTEXITCODE -eq 0 -and $actualHash -eq $expectedHash
}

function Build-DockerBuilder {
    param(
        [string]$TargetStage,
        [string]$PlatformLabel,
        [string]$LocalTag,
        [string]$RemoteTag,
        [string]$AltRemoteTag = ""
    )

    if (-not (Test-Path $Dockerfile)) {
        throw "Dockerfile was not found at $Dockerfile"
    }

    $isImageValid = (Test-DockerImage $LocalTag $Dockerfile) -or (Test-DockerImage $RemoteTag $Dockerfile)
    if ($RebuildImage -or -not $isImageValid) {
        Write-Host "=== Building $PlatformLabel Docker builder image (target: $TargetStage) ==="
        $BuildArguments = @("build", "--tag", $LocalTag, "--tag", $RemoteTag, "--file", $Dockerfile, "--target", $TargetStage)
        if ($AltRemoteTag) {
            $BuildArguments += @("--tag", $AltRemoteTag)
        }
        if ($RebuildImage) {
            $BuildArguments += "--pull"
        }
        $BuilderInputHash = Get-BuilderInputHash $Dockerfile
        $BuildArguments += @("--build-arg", "BUILDER_INPUT_SHA=$BuilderInputHash", $Root)
        & docker @BuildArguments
        if ($LASTEXITCODE -ne 0) {
            throw "$PlatformLabel Docker image build failed."
        }
    } else {
        & docker tag $LocalTag $RemoteTag 2>$null
        & docker tag $RemoteTag $LocalTag 2>$null
        if ($AltRemoteTag) {
            & docker tag $RemoteTag $AltRemoteTag 2>$null
        }
    }

    if ($Push) {
        Write-Host "=== Pushing $PlatformLabel Docker builder image to Docker Hub ($RemoteTag) ==="
        & docker push $RemoteTag
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to push $RemoteTag to Docker Hub."
        }
        if ($AltRemoteTag) {
            & docker push $AltRemoteTag
        }
    }

    Write-Host "[+] $PlatformLabel Docker builder is ready: $RemoteTag"
}

if ($Platform -eq "Linux" -or $Platform -eq "All") {
    Build-DockerBuilder -TargetStage "linux" -PlatformLabel "Linux" -LocalTag "cmdiqo-installer-builder:ubuntu-22.04" -RemoteTag "${DockerUser}/cmdiqo-installer-builder:ubuntu-22.04" -AltRemoteTag "${DockerUser}/cmdiqo-installer-builder:linux"
}

if ($Platform -eq "Windows" -or $Platform -eq "All") {
    Build-DockerBuilder -TargetStage "windows" -PlatformLabel "Windows (MinGW-w64)" -LocalTag "cmdiqo-installer-builder:windows-mingw" -RemoteTag "${DockerUser}/cmdiqo-installer-builder:windows-mingw" -AltRemoteTag "${DockerUser}/cmdiqo-installer-builder:windows"
}

if ($Platform -eq "All") {
    Build-DockerBuilder -TargetStage "all" -PlatformLabel "Unified Multi-Toolchain" -LocalTag "cmdiqo-installer-builder:all" -RemoteTag "${DockerUser}/cmdiqo-installer-builder:all" -AltRemoteTag "${DockerUser}/cmdiqo-installer-builder:ubuntu-22.04-all"
}

Write-Host ""
Write-Host "Docker builder environment is ready. Build from the repository root with:"
Write-Host "  Set-Location $(Split-Path -Parent $Root)"
if ($Platform -eq "Linux") {
    Write-Host "  .\build.ps1 -Installer -Platform Linux"
} elseif ($Platform -eq "All") {
    Write-Host "  .\build.ps1 -Installer -Platform All"
} else {
    Write-Host "  .\build.ps1 -Installer"
}
