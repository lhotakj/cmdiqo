<#
.SYNOPSIS
    Installs the ShangoHub Slack LAN Agent as an automatic background service on Windows.

.DESCRIPTION
    Configures the agent to start automatically on Windows boot with SYSTEM/Administrator
    privileges. Supports both Windows Task Scheduler (native, zero dependencies) and NSSM.

.PARAMETER HubUrl
    The WebSocket URL of the ShangoHub server (e.g. ws://192.168.1.10:8765).

.PARAMETER Token
    The shared secret AGENT_TOKEN configured on the hub.

.PARAMETER Name
    The agent name used as target in Slack commands (default: hostname).

.PARAMETER InstallDir
    Target directory for the binary (default: C:\Program Files\ShangoHub\Agent).

.EXAMPLE
    .\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "my-secret-token" -Name "WIN-PC"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$HubUrl,

    [Parameter(Mandatory = $false)]
    [string]$Token,

    [Parameter(Mandatory = $false)]
    [string]$Name = $env:COMPUTERNAME,

    [Parameter(Mandatory = $false)]
    [string]$InstallDir = "C:\Program Files\ShangoHub\Agent",

    [Parameter(Mandatory = $false)]
    [string]$ConfigDir = "C:\ProgramData\ShangoHub\Agent"
)

# Ensure running with Administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "[ERROR] This installer must be run as Administrator! Please re-open PowerShell as Administrator."
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== Installing ShangoHub Slack LAN Agent on Windows ===" -ForegroundColor Cyan

# 1. Resolve or prompt for parameters if missing
if (-not $HubUrl) {
    $HubUrl = Read-Host "Enter Hub WebSocket URL (e.g. ws://192.168.1.10:8765)"
}
if (-not $Token) {
    $Token = Read-Host "Enter AGENT_TOKEN shared secret"
}

# 2. Setup Directories
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

# 3. Save Configuration JSON
$ConfigFile = Join-Path $ConfigDir "config.json"
$ConfigData = @{
    hub_url = $HubUrl
    token   = $Token
    name    = $Name
    reconnect_delay = 5.0
} | ConvertTo-Json -Depth 3

Set-Content -Path $ConfigFile -Value $ConfigData -Encoding UTF8
Write-Host "[+] Saved configuration to $ConfigFile" -ForegroundColor Green

# 4. Locate or copy binary / script
$ExeDist = Join-Path $ScriptDir "..\dist\shango-agent.exe"
$ExeLocal = Join-Path $ScriptDir "shango-agent.exe"
$DestExe = Join-Path $InstallDir "shango-agent.exe"

if (Test-Path $ExeDist) {
    Copy-Item -Path $ExeDist -Destination $DestExe -Force
    Write-Host "[+] Installed executable to $DestExe" -ForegroundColor Green
    $RunCommand = "`"$DestExe`""
    $RunArgs = "--config `"$ConfigFile`""
} elseif (Test-Path $ExeLocal) {
    Copy-Item -Path $ExeLocal -Destination $DestExe -Force
    Write-Host "[+] Installed executable to $DestExe" -ForegroundColor Green
    $RunCommand = "`"$DestExe`""
    $RunArgs = "--config `"$ConfigFile`""
}

if (Test-Path $DestExe) {
    # Register digital certificate in Trusted Root and Trusted Publishers if signed
    $sig = Get-AuthenticodeSignature $DestExe
    if ($sig.SignerCertificate) {
        Write-Host "[*] Found Authenticode Signature: $($sig.SignerCertificate.Subject)" -ForegroundColor Cyan
        try {
            $rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store "Root", "LocalMachine"
            $rootStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $rootStore.Add($sig.SignerCertificate)
            $rootStore.Close()

            $pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store "TrustedPublisher", "LocalMachine"
            $pubStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $pubStore.Add($sig.SignerCertificate)
            $pubStore.Close()
            Write-Host "[+] Registered code-signing certificate in LocalMachine Root and TrustedPublisher stores." -ForegroundColor Green
        } catch {
            Write-Verbose "Could not automatically import certificate into system store: $_"
        }
    }
} else {
    # Fallback to Python runtime
    Write-Host "[*] No pre-built shango-agent.exe found. Using system Python..." -ForegroundColor Yellow
    Copy-Item -Path (Join-Path $ScriptDir "agent.py") -Destination (Join-Path $InstallDir "agent.py") -Force
    $PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $PythonExe) {
        Write-Error "[ERROR] Neither shango-agent.exe nor python.exe was found in PATH!"
        exit 1
    }
    $RunCommand = "`"$PythonExe`""
    $AgentPy = Join-Path $InstallDir "agent.py"
    $RunArgs = "`"$AgentPy`" --config `"$ConfigFile`""
}

# 5. Register Automatic Startup Service via Task Scheduler (runs as SYSTEM at boot)
$TaskName = "Shango-Agent"
Write-Host "[+] Registering Windows Service / Startup Task: $TaskName..." -ForegroundColor Cyan

# Unregister existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute $RunCommand -Argument $RunArgs
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "Shango-Agent reverse connection service" | Out-Null

# 6. Start the service
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2
$Task = Get-ScheduledTask -TaskName $TaskName

Write-Host "=== Installation Complete ===" -ForegroundColor Green
Write-Host "Service '$TaskName' is installed and status is: $($Task.State)" -ForegroundColor Cyan
Write-Host "To view task status: Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "To stop service:     Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "To start service:    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To uninstall:        Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
