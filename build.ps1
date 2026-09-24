# PowerShell build wrapper for Shango
# Forwards execution to development/build.ps1

[CmdletBinding()]
param(
    [ValidateSet("Agent", "Bot", "All")]
    [string]$Target = "Agent",

    [string]$Version,

    [switch]$SkipSign,

    [string]$CopyTo = "C:\tmp\shango-agent-windows-x64.exe",

    [switch]$Clean
)

$targetScript = Join-Path $PSScriptRoot "development\build.ps1"
if (-not (Test-Path $targetScript)) {
    Write-Error "Could not locate build script at $targetScript"
    exit 1
}

& $targetScript @PSBoundParameters
exit $LASTEXITCODE
