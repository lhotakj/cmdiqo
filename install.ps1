<#
.SYNOPSIS
Cmdiqo Agent Universal Bootstrap Installer for Windows

.DESCRIPTION
Downloads and executes the native Windows Cmdiqo Agent setup wizard or CLI installer.

.PARAMETER HubUrl
WebSocket Hub server URL (e.g. ws://192.168.1.10:8765)

.PARAMETER Token
Authentication agent token

.PARAMETER Name
Agent host display name

.PARAMETER Upgrade
Upgrade binary and restart service, keeping existing configuration

.PARAMETER UnInstall
Uninstall the service and binary

.PARAMETER Purge
Purge configuration and logs on uninstall

.PARAMETER Version
Release version tag to download (defaults to "latest")

.PARAMETER Repo
GitHub repository owner/name (defaults to "lhotakj/cmdiqo")

.EXAMPLE
# Interactive GUI Setup:
irm https://raw.githubusercontent.com/lhotakj/cmdiqo/main/install.ps1 | iex

.EXAMPLE
# Headless / Automated Service Installation via Piped One-Liner:
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/lhotakj/cmdiqo/main/install.ps1))) -HubUrl "ws://192.168.1.10:8765" -Token "SECRET_TOKEN" -Name "MY-PC"
#>
[CmdletBinding()]
param(
    [string]$HubUrl = "",
    [string]$Token = "",
    [string]$Name = "",
    [switch]$Upgrade,
    [switch]$UnInstall,
    [switch]$Purge,
    [string]$Version = "latest",
    [string]$Repo = "lhotakj/cmdiqo",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

# Ensure TLS 1.2+ is active on older Windows PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$binaryName = "cmdiqo-agent-windows-x64-setup.exe"
if ($Version -eq "latest") {
    $downloadUrl = "https://github.com/$Repo/releases/latest/download/$binaryName"
} else {
    $tag = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }
    $downloadUrl = "https://github.com/$Repo/releases/download/$tag/$binaryName"
}

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) "cmdiqo-setup-$PID"
[void](New-Item -ItemType Directory -Force -Path $tempDir)
$tempExe = Join-Path $tempDir $binaryName

try {
    Write-Host "=== Cmdiqo Agent Bootstrap Installer for Windows ===" -ForegroundColor Cyan
    Write-Host "[+] Downloading native setup from $downloadUrl..." -ForegroundColor Green
    Invoke-WebRequest -Uri $downloadUrl -OutFile $tempExe -UseBasicParsing

    $exeArgs = @()
    if ($UnInstall) {
        $exeArgs += "--uninstall"
        if ($Purge) { $exeArgs += "--purge" }
    } elseif ($Upgrade) {
        $exeArgs += "--upgrade"
    } elseif ($HubUrl -or $Token -or $Name) {
        $exeArgs += "--install"
        if ($HubUrl) { $exeArgs += @("--hub-url", $HubUrl) }
        if ($Token) { $exeArgs += @("--token", $Token) }
        if ($Name) { $exeArgs += @("--name", $Name) }
    }

    if ($RemainingArgs) {
        $exeArgs += $RemainingArgs
    }

    if ($exeArgs.Count -gt 0) {
        Write-Host "[+] Running CLI setup: $binaryName $($exeArgs -join ' ')..." -ForegroundColor Green
        $proc = Start-Process -FilePath $tempExe -ArgumentList $exeArgs -PassThru -Wait -NoNewWindow
        exit $proc.ExitCode
    } else {
        Write-Host "[+] Launching interactive setup wizard..." -ForegroundColor Green
        $proc = Start-Process -FilePath $tempExe -PassThru -Wait
        exit $proc.ExitCode
    }
} finally {
    if (Test-Path $tempDir) {
        Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
    }
}
