# PowerShell script to create a persistent Code Signing Certificate for CmdiqoHub.
# Generates a PFX certificate and public CER, trusts it on your local machine,
# and outputs the Base64 and password ready to paste into GitHub Actions Secrets.
#
# Usage:
#   .\development\generate_signing_cert.ps1
#   .\development\generate_signing_cert.ps1 -Password "MySecretPass123!"

[CmdletBinding()]
param(
    [string]$Subject = "CN=CmdiqoHub, O=CmdiqoHub, OU=Verified Build",
    [string]$Password,
    [string]$OutDir = "$PSScriptRoot\..\certs"
)

$ErrorActionPreference = "Stop"

Write-Host "`n=== CmdiqoHub Code Signing Certificate Generator ===" -ForegroundColor Cyan

if (-not $Password) {
    # Generate random 16-character alphanumeric password
    $chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!#$%"
    $Password = -join ((1..16) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
}

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}

$pfxFile = Join-Path $OutDir "cmdiqo-signing-cert.pfx"
$cerFile = Join-Path $OutDir "cmdiqo-signing-cert.cer"

Write-Host "[*] Creating Code-Signing Certificate: $Subject" -ForegroundColor Green
$cert = New-SelfSignedCertificate -Type CodeSigningCert `
    -Subject $Subject `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears(5)

Write-Host "[+] Certificate generated. Thumbprint: $($cert.Thumbprint)" -ForegroundColor Green

# Export CER
Export-Certificate -Cert $cert -FilePath $cerFile | Out-Null
Write-Host "[+] Public certificate exported: $cerFile" -ForegroundColor Green

# Export PFX
$secPwd = ConvertTo-SecureString $Password -AsPlainText -Force
Export-PfxCertificate -Cert $cert -FilePath $pfxFile -Password $secPwd | Out-Null
Write-Host "[+] Private key PFX exported: $pfxFile" -ForegroundColor Green

# Register in CurrentUser Root and TrustedPublisher stores
Write-Host "[*] Registering certificate in CurrentUser Root and TrustedPublisher stores..." -ForegroundColor Cyan
try {
    $rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store "Root", "CurrentUser"
    $rootStore.Open("ReadWrite")
    $rootStore.Add($cert)
    $rootStore.Close()

    $pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store "TrustedPublisher", "CurrentUser"
    $pubStore.Open("ReadWrite")
    $pubStore.Add($cert)
    $pubStore.Close()
    Write-Host "[+] Certificate successfully trusted on this machine!" -ForegroundColor Green
} catch {
    Write-Warning "Failed to automatically add to CurrentUser stores: $_"
}

# Encode to Base64
$pfxBytes = [IO.File]::ReadAllBytes($pfxFile)
$pfxBase64 = [Convert]::ToBase64String($pfxBytes)

Write-Host "`n" + ("=" * 72) -ForegroundColor Yellow
Write-Host "               GITHUB ACTIONS SECRETS CONFIGURATION" -ForegroundColor Yellow
Write-Host ("=" * 72) -ForegroundColor Yellow
Write-Host "To make ALL future GitHub Actions builds trusted automatically, add these" -ForegroundColor White
Write-Host "two secrets to your GitHub repository (Settings -> Secrets -> Actions):`n" -ForegroundColor DarkGray

Write-Host "1. Secret Name:  WINDOWS_SIGNING_CERT_PASSWORD" -ForegroundColor Cyan
Write-Host "   Secret Value: $Password`n" -ForegroundColor White

Write-Host "2. Secret Name:  WINDOWS_SIGNING_CERT_BASE64" -ForegroundColor Cyan
Write-Host "   Secret Value: (copy string below)" -ForegroundColor Cyan
Write-Host $pfxBase64 -ForegroundColor Gray
Write-Host ("=" * 72) -ForegroundColor Yellow
Write-Host "`n[+] Saved local backup to: $pfxFile (never commit this file to git)" -ForegroundColor DarkGray
