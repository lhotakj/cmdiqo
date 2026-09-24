<#
.SYNOPSIS
    Uninstalls the Shango Slack LAN Agent Windows Service completely.

.DESCRIPTION
    Stops the ShangoAgent Windows Service, deletes it from the Windows Service
    Control Manager (SCM), terminates running agent processes, and removes
    installed binaries. Optionally purges secrets and configuration.

.PARAMETER PurgeConfig
    If specified, deletes C:\ProgramData\Shango\Agent (including secrets and logs).
    Default is to keep configuration for upgrades.

.EXAMPLE
    .\uninstall-service.ps1
    .\uninstall-service.ps1 -PurgeConfig
#>

[CmdletBinding()]
param(
    [switch]$PurgeConfig
)

$ErrorActionPreference = "Stop"

# 0. Check for Administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "[ERROR] This script must be run as Administrator! Please re-open PowerShell as Administrator."
    exit 1
}

$ServiceName = "ShangoAgent"
$InstallDir = "C:\Program Files\Shango\Agent"
$ConfigDir = "C:\ProgramData\Shango\Agent"

Write-Host "`n=== Uninstalling Shango Windows Service ($ServiceName) ===" -ForegroundColor Yellow

# 1. Stop service if running
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "[*] Stopping service $ServiceName..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# 2. Delete service from SCM
Write-Host "[*] Deleting service from Windows Service Control Manager..." -ForegroundColor Cyan
& sc.exe delete $ServiceName | Out-Null
Start-Sleep -Seconds 1

# 3. Clean up legacy Scheduled Task if previously installed
$legacyTask = Get-ScheduledTask -TaskName "Shango-Agent" -ErrorAction SilentlyContinue
if ($legacyTask) {
    Write-Host "[*] Removing legacy Scheduled Task 'Shango-Agent'..." -ForegroundColor Cyan
    Unregister-ScheduledTask -TaskName "Shango-Agent" -Confirm:$false -ErrorAction SilentlyContinue
}

# 4. Terminate any lingering processes
Get-Process -Name "shango-agent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 5. Remove binary files
if (Test-Path $InstallDir) {
    Write-Host "[*] Removing binaries from $InstallDir..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force -Path $InstallDir -ErrorAction SilentlyContinue
}

# 6. Purge config if requested
if ($PurgeConfig) {
    if (Test-Path $ConfigDir) {
        Write-Host "[*] Purging configuration and secrets from $ConfigDir..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force -Path $ConfigDir -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "[i] Note: Configuration and logs in $ConfigDir were preserved." -ForegroundColor DarkGray
    Write-Host "    (Run with -PurgeConfig if you wish to delete config and secrets)." -ForegroundColor DarkGray
}

Write-Host "`n[+] Shango Windows Service ($ServiceName) successfully uninstalled!" -ForegroundColor Green
