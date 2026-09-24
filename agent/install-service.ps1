<#
.SYNOPSIS
    Installs the Shango Slack LAN Agent as a native Windows Service (Python & PS1 only).

.DESCRIPTION
    Configures shango-agent as a native Windows Service managed directly by the
    Windows Service Control Manager (SCM). Uses Python's native Win32 service
    dispatcher (zero external dependencies, zero third-party wrappers).
    
    Architecture:
      - Binary Directory: C:\Program Files\Shango\Agent\ (shango-agent.exe or agent.py)
      - Secrets & Config: C:\ProgramData\Shango\Agent\config.json (Full edit access for Admins & Users)
      - Service Logs:     C:\ProgramData\Shango\Agent\logs\service.log (auto stdout/stderr capture)

.PARAMETER HubUrl
    The WebSocket URL of the Shango hub (e.g. ws://192.168.1.10:8765).

.PARAMETER Token
    The shared secret AGENT_TOKEN configured on the hub.

.PARAMETER Name
    The agent name used as target in Slack commands (default: hostname).

.PARAMETER BinaryPath
    Optional explicit path to pre-built shango-agent.exe or shango-agent-windows-x64.exe.

.PARAMETER InstallDir
    Target directory for service binaries (default: C:\Program Files\Shango\Agent).

.PARAMETER ConfigDir
    Directory for secrets and config (default: C:\ProgramData\Shango\Agent).

.PARAMETER Uninstall
    If specified, uninstalls and deletes the Windows Service completely.

.EXAMPLE
    .\install-service.ps1 -HubUrl "ws://192.168.1.10:8765" -Token "my-secret-token" -Name "WIN-PC"
    .\install-service.ps1 -Uninstall
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
    [string]$BinaryPath,

    [Parameter(Mandatory = $false)]
    [string]$InstallDir = "C:\Program Files\Shango\Agent",

    [Parameter(Mandatory = $false)]
    [string]$ConfigDir = "C:\ProgramData\Shango\Agent",

    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# 0. Ensure running with Administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "[ERROR] This script must be run as Administrator! Please re-open PowerShell as Administrator."
    exit 1
}

$ServiceName = "ShangoAgent"
$DisplayName = "Shango LAN Remote Agent"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ==============================================================================
# UNINSTALL WORKFLOW
# ==============================================================================
if ($Uninstall) {
    Write-Host "`n=== Uninstalling Shango Windows Service ($ServiceName) ===" -ForegroundColor Yellow

    # Stop service if running
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "[*] Stopping service $ServiceName..." -ForegroundColor Cyan
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    # Delete service from SCM
    Write-Host "[*] Deleting service from Service Control Manager..." -ForegroundColor Cyan
    & sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 1

    # Clean up legacy Scheduled Task if previously installed
    $legacyTask = Get-ScheduledTask -TaskName "Shango-Agent" -ErrorAction SilentlyContinue
    if ($legacyTask) {
        Write-Host "[*] Removing legacy Scheduled Task 'Shango-Agent'..." -ForegroundColor Cyan
        Unregister-ScheduledTask -TaskName "Shango-Agent" -Confirm:$false -ErrorAction SilentlyContinue
    }

    # Terminate any lingering processes
    Get-Process -Name "shango-agent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    # Remove binary files
    if (Test-Path $InstallDir) {
        Write-Host "[*] Removing binaries from $InstallDir..." -ForegroundColor Cyan
        Remove-Item -Recurse -Force -Path $InstallDir -ErrorAction SilentlyContinue
    }

    Write-Host "[+] Service $ServiceName successfully uninstalled!" -ForegroundColor Green
    Write-Host "[i] Note: Configuration and logs in $ConfigDir were preserved." -ForegroundColor DarkGray
    Write-Host "    (Delete $ConfigDir manually if you wish to remove secrets)." -ForegroundColor DarkGray
    exit 0
}

# ==============================================================================
# INSTALL WORKFLOW
# ==============================================================================
Write-Host "`n=== Installing Shango Slack LAN Agent as a Real Windows Service ===" -ForegroundColor Cyan

# 1. Resolve configuration parameters
$ConfigFile = Join-Path $ConfigDir "config.json"
$ExistingConfig = $null
if (Test-Path $ConfigFile) {
    try {
        $ExistingConfig = Get-Content -Path $ConfigFile -Raw | ConvertFrom-Json
    } catch {}
}

if (-not $HubUrl) {
    $defaultHub = if ($ExistingConfig.hub_url) { $ExistingConfig.hub_url } else { "ws://192.168.1.10:8765" }
    $HubUrl = Read-Host "Enter Hub WebSocket URL [Default: $defaultHub]"
    if (-not $HubUrl) { $HubUrl = $defaultHub }
}

if (-not $Token) {
    if ($ExistingConfig.token) {
        $Token = $ExistingConfig.token
        Write-Host "[*] Reusing existing AGENT_TOKEN from $ConfigFile" -ForegroundColor DarkGray
    } else {
        $Token = Read-Host "Enter AGENT_TOKEN shared secret"
    }
}

if (-not $Token) {
    Write-Error "[ERROR] AGENT_TOKEN cannot be empty!"
    exit 1
}

# 2. Setup Directories
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir "logs") | Out-Null

# 3. Save Configuration JSON and Secure ACLs (Secrets Protection)
$ConfigData = @{
    hub_url         = $HubUrl
    token           = $Token
    name            = $Name
    reconnect_delay = 5.0
} | ConvertTo-Json -Depth 3

[IO.File]::WriteAllText($ConfigFile, $ConfigData, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[+] Saved configuration to $ConfigFile" -ForegroundColor Green

# Configure config.json permissions so administrators and users can view and edit freely:
try {
    & icacls.exe $ConfigDir /reset /T | Out-Null
    & icacls.exe $ConfigDir /grant:r "BUILTIN\Administrators:(OI)(CI)(F)" "NT AUTHORITY\SYSTEM:(OI)(CI)(F)" "$($env:USERDOMAIN)\$($env:USERNAME):(OI)(CI)(F)" "BUILTIN\Users:(OI)(CI)(M)" /T | Out-Null
    Write-Host "[+] Configured $ConfigFile permissions (Full access for Administrators, SYSTEM, and Users)" -ForegroundColor Green
} catch {
    try {
        $acl = Get-Acl -Path $ConfigFile
        $acl.SetAccessRuleProtection($false, $true)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule("BUILTIN\Administrators", "FullControl", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule("NT AUTHORITY\SYSTEM", "FullControl", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule("$($env:USERDOMAIN)\$($env:USERNAME)", "FullControl", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule("BUILTIN\Users", "Modify", "Allow")))
        Set-Acl -Path $ConfigFile -AclObject $acl
        Write-Host "[+] Configured $ConfigFile permissions via Set-Acl" -ForegroundColor Green
    } catch {
        Write-Verbose "Could not configure file ACLs: $_"
    }
}

# 4. Locate or copy shango-agent binary / python scripts
$DestExe = Join-Path $InstallDir "shango-agent.exe"

$latestDownload = Get-ChildItem -Path "$env:USERPROFILE\Downloads" -Filter "*shango-agent*.exe" -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName

$candidateExes = @(
    $BinaryPath,
    (Join-Path $ScriptDir "..\dist\shango-agent.exe"),
    (Join-Path $ScriptDir "shango-agent-windows-x64.exe"),
    (Join-Path $ScriptDir "shango-agent.exe"),
    $latestDownload,
    (Join-Path $ScriptDir "..\shango-agent-windows-x64.exe"),
    (Join-Path $ScriptDir "..\dist\shango-agent-windows-x64.exe")
) | Where-Object { $_ -and (Test-Path $_) }

$SourceExe = $null
foreach ($cand in $candidateExes) {
    try {
        $help = & $cand --help 2>&1 | Out-String
        if ($help -match "--service") {
            $SourceExe = $cand
            break
        }
    } catch {}
}

if ($SourceExe) {
    Copy-Item -Path $SourceExe -Destination $DestExe -Force
    Unblock-File -Path $DestExe -ErrorAction SilentlyContinue
    Write-Host "[+] Installed agent binary from $SourceExe to $DestExe" -ForegroundColor Green
} else {
    Write-Host "[*] Pre-built binary does not have --service support yet." -ForegroundColor Yellow
    Write-Host "[*] Falling back to native Python Win32 service dispatcher..." -ForegroundColor Yellow
    if (Test-Path $DestExe) {
        Remove-Item -Force $DestExe -ErrorAction SilentlyContinue
    }
    Copy-Item -Path (Join-Path $ScriptDir "agent.py") -Destination (Join-Path $InstallDir "agent.py") -Force
    Copy-Item -Path (Join-Path $ScriptDir "service_windows.py") -Destination (Join-Path $InstallDir "service_windows.py") -Force -ErrorAction SilentlyContinue
    $versionPy = Join-Path $ScriptDir "_version.py"
    if (Test-Path $versionPy) {
        Copy-Item -Path $versionPy -Destination (Join-Path $InstallDir "_version.py") -Force -ErrorAction SilentlyContinue
    }
}

# 5. Trust embedded Authenticode certificate if binary is present
if (Test-Path $DestExe) {
    Unblock-File -Path $DestExe -ErrorAction SilentlyContinue
    $sig = Get-AuthenticodeSignature $DestExe
    if ($sig.SignerCertificate) {
        Write-Host "[*] Registering Authenticode certificate: $($sig.SignerCertificate.Subject)" -ForegroundColor Cyan
        try {
            $rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store "Root", "LocalMachine"
            $rootStore.Open("ReadWrite")
            $rootStore.Add($sig.SignerCertificate)
            $rootStore.Close()

            $pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store "TrustedPublisher", "LocalMachine"
            $pubStore.Open("ReadWrite")
            $pubStore.Add($sig.SignerCertificate)
            $pubStore.Close()
            Write-Host "[+] Registered certificate in LocalMachine Root and TrustedPublisher stores." -ForegroundColor Green
        } catch {
            Write-Verbose "Could not import certificate into system store: $_"
        }
    }
}

# 6. Clean up any existing legacy Scheduled Task
$legacyTask = Get-ScheduledTask -TaskName "Shango-Agent" -ErrorAction SilentlyContinue
if ($legacyTask) {
    Write-Host "[*] Cleaning up legacy Scheduled Task 'Shango-Agent'..." -ForegroundColor DarkGray
    Unregister-ScheduledTask -TaskName "Shango-Agent" -Confirm:$false -ErrorAction SilentlyContinue
}

# 7. Determine execution command line
if (Test-Path $DestExe) {
    $BinPathValue = "`"$DestExe`" --service --config `"$ConfigFile`""
} else {
    $PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $PythonExe) {
        Write-Error "[ERROR] Neither shango-agent.exe nor python.exe was found in PATH!"
        exit 1
    }
    $AgentPy = Join-Path $InstallDir "agent.py"
    $BinPathValue = "`"$PythonExe`" `"$AgentPy`" --service --config `"$ConfigFile`""
}

# 8. Upgrade or Register Windows Service in SCM
$existingSvc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$serviceConfigured = $false

if ($existingSvc) {
    Write-Host "[*] Updating existing service '$ServiceName' in-place..." -ForegroundColor Cyan
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    $configResult = & sc.exe config $ServiceName binPath= $BinPathValue start= auto DisplayName= $DisplayName 2>&1
    if ($LASTEXITCODE -eq 0) {
        $serviceConfigured = $true
        Write-Host "[+] Service configuration updated in-place." -ForegroundColor Green
    } else {
        if ($configResult -match "marked for deletion") {
            Write-Host "[*] Service was previously marked for deletion." -ForegroundColor Yellow
            $mmc = Get-Process mmc -ErrorAction SilentlyContinue
            if ($mmc) {
                Write-Warning "Detected open 'services.msc' (Services window). Windows holds a lock on deleted services while services.msc is open!"
                Write-Warning "Please CLOSE 'services.msc' now so Windows can finish removing the old service."
            }
            for ($i = 0; $i -lt 15; $i++) {
                Start-Sleep -Seconds 1
                if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
                    break
                }
            }
        }
    }
}

if (-not $serviceConfigured) {
    Write-Host "[*] Registering '$ServiceName' with Windows Service Control Manager..." -ForegroundColor Cyan
    try {
        New-Service -Name $ServiceName `
            -BinaryPathName $BinPathValue `
            -DisplayName $DisplayName `
            -StartupType Automatic `
            -Description "Shango Slack LAN Remote Agent reverse WebSocket connection service" | Out-Null
    } catch {
        if ($_ -match "marked for deletion") {
            Write-Error "[ERROR] Service '$ServiceName' is marked for deletion because 'services.msc' (Services window) is currently open.`n`n--> PLEASE CLOSE 'services.msc' (Services window) and re-run .\install-service.ps1"
            exit 1
        } else {
            throw $_
        }
    }
}

# Configure failure actions: restart service after 5s, 10s, 30s on crash
& sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null

# 9. Start the service
Write-Host "[*] Starting $ServiceName service..." -ForegroundColor Cyan
Start-Service -Name $ServiceName
Start-Sleep -Seconds 2

$svcStatus = Get-Service -Name $ServiceName
$LogFile = Join-Path $ConfigDir "logs\service.log"

Write-Host "`n" + ("=" * 70) -ForegroundColor Green
Write-Host "  SHANGO WINDOWS SERVICE INSTALLED SUCCESSFULLY!" -ForegroundColor Green
Write-Host ("=" * 70) -ForegroundColor Green
Write-Host "Service Name:    $ServiceName" -ForegroundColor White
Write-Host "Display Name:    $DisplayName" -ForegroundColor White
Write-Host "Status:          $($svcStatus.Status)" -ForegroundColor Cyan
Write-Host "Startup Type:    $($svcStatus.StartType)" -ForegroundColor White
Write-Host "Binary Command:  $BinPathValue" -ForegroundColor White
Write-Host "Configuration:   $ConfigFile (Protected ACLs)" -ForegroundColor White
Write-Host "Service Logs:    $LogFile" -ForegroundColor White
Write-Host ("=" * 70) -ForegroundColor Green

Write-Host "`nWindows Service Management Commands:" -ForegroundColor Yellow
Write-Host "  Get-Service $ServiceName             # Check service status" -ForegroundColor White
Write-Host "  Restart-Service $ServiceName         # Restart service" -ForegroundColor White
Write-Host "  Stop-Service $ServiceName            # Stop service" -ForegroundColor White
Write-Host "  Start-Service $ServiceName           # Start service" -ForegroundColor White
Write-Host "  Get-Content `"$LogFile`" -Tail 20 -Wait  # View live logs" -ForegroundColor White
Write-Host "  .\uninstall-service.ps1              # Completely uninstall" -ForegroundColor White
