# PowerShell environment setup script for Shango
[CmdletBinding()]
param()

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

# 1. Locate Python
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $pythonCmd) {
    Write-Err "Python was not found in PATH. Please install Python 3.11+."
    exit 1
}

$pyVersion = & $pythonCmd.Source --version 2>&1
Write-Info "Using $pyVersion ($($pythonCmd.Source))"

# 2. Create virtual environment if missing
$venvPath = Join-Path $rootDir ".venv"
$venvActivate = Join-Path $venvPath "Scripts\Activate.ps1"

if (-not (Test-Path $venvPath)) {
    Write-Info "Creating virtual environment at $venvPath ..."
    & $pythonCmd.Source -m venv $venvPath
}

# 3. Activate virtual environment
if (Test-Path $venvActivate) {
    Write-Info "Activating virtual environment ..."
    & $venvActivate
} else {
    Write-Err "Virtual environment activation script missing at $venvActivate"
    exit 1
}

# 4. Install dependencies
Write-Info "Upgrading pip ..."
python -m pip install --upgrade pip -q

if (Test-Path "$rootDir\requirements.txt") {
    Write-Info "Installing dependencies from requirements.txt ..."
    pip install -q -r "$rootDir\requirements.txt"
}
if (Test-Path "$rootDir\requirements-dev.txt") {
    Write-Info "Installing development tools from requirements-dev.txt ..."
    pip install -q -r "$rootDir\requirements-dev.txt"
}
if (Test-Path "$rootDir\agent\requirements.txt") {
    Write-Info "Installing agent dependencies from agent\requirements.txt ..."
    pip install -q -r "$rootDir\agent\requirements.txt"
}

# 5. Load environment variables from .env or .secrets.*
if (Test-Path "$rootDir\.env") {
    Write-Info "Loading environment variables from .env ..."
    Get-Content "$rootDir\.env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $name = $parts[0].Trim()
            $val = $parts[1].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($name, $val, "Process")
        }
    }
}

Write-Info "Environment ready! To activate in any shell: .venv\Scripts\Activate.ps1"
