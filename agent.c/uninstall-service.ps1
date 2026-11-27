<#
.SYNOPSIS
    Uninstalls the Cmdiqo Slack LAN Agent Windows Service completely.

.DESCRIPTION
    Stops the CmdiqoAgent Windows Service, deletes it from the Windows Service
    Control Manager (SCM), terminates running agent processes, and removes
    installed binaries. Optionally purges secrets and configuration.

.PARAMETER PurgeConfig
    If specified, deletes C:\ProgramData\Cmdiqo\Agent (including secrets and logs).
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

$ServiceName = "CmdiqoAgent"
$InstallDir = "C:\Program Files\Cmdiqo\Agent"
$ConfigDir = "C:\ProgramData\Cmdiqo\Agent"
$DefaultServiceSddl = "D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)"

function Invoke-CmdiqoSystemScCommand {
    param([Parameter(Mandatory = $true)][string]$CmdArguments)
    $taskName = "CmdiqoSvcHelper-$PID-$(Get-Random)"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $CmdArguments"
    $principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline) {
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if (-not $task -or $task.State -ne 'Running') {
                break
            }
            Start-Sleep -Milliseconds 200
        }
    } finally {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

Write-Host "`n=== Uninstalling Cmdiqo Windows Service ($ServiceName) ===" -ForegroundColor Yellow

# 1. Stop service if running
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "[*] Stopping service $ServiceName..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    $svc.Refresh()
    if ($svc.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Stopped) {
        Invoke-CmdiqoSystemScCommand "sc.exe control $ServiceName 135 || sc.exe stop $ServiceName"
        try { $svc.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(15)) } catch {}
    } else {
        Start-Sleep -Seconds 1
    }
}

# 2. Delete service from SCM
Write-Host "[*] Deleting service from Windows Service Control Manager..." -ForegroundColor Cyan
& sc.exe delete $ServiceName | Out-Null
if ($LASTEXITCODE -ne 0 -and (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Invoke-CmdiqoSystemScCommand "sc.exe sdset $ServiceName `"$DefaultServiceSddl`" && sc.exe delete $ServiceName"
}
Start-Sleep -Seconds 1

# 3. Clean up legacy Scheduled Task if previously installed
$legacyTask = Get-ScheduledTask -TaskName "Cmdiqo-Agent" -ErrorAction SilentlyContinue
if ($legacyTask) {
    Write-Host "[*] Removing legacy Scheduled Task 'Cmdiqo-Agent'..." -ForegroundColor Cyan
    Unregister-ScheduledTask -TaskName "Cmdiqo-Agent" -Confirm:$false -ErrorAction SilentlyContinue
}

# 4. Terminate any lingering processes
Get-Process -Name "cmdiqo-agent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

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

Write-Host "`n[+] Cmdiqo Windows Service ($ServiceName) successfully uninstalled!" -ForegroundColor Green
