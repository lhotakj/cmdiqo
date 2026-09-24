#!/usr/bin/env python3
"""Cross-platform build script to compile shango-agent into standalone binaries."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"
AGENT_SCRIPT = AGENT_DIR / "agent.py"
REQUIREMENTS = AGENT_DIR / "requirements.txt"


def run_cmd(cmd: list[str], check: bool = True) -> None:
    print(f"[build] Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=check)


def sign_windows_binary(exe_path: Path) -> None:
    """Digitally sign the Windows executable using Authenticode and export certificate."""
    cer_dist = exe_path.parent / "shango-agent.cer"
    cer_root = ROOT / "shango-agent.cer"
    ps_cmd = (
        "$cert = $null; "
        "if ($env:SIGNING_CERT_BASE64) { "
        "  Write-Host '[*] Using production code signing certificate from environment...'; "
        "  $pfxBytes = [Convert]::FromBase64String($env:SIGNING_CERT_BASE64); "
        "  $pfxPath = Join-Path ([IO.Path]::GetTempPath()) 'signing_cert.pfx'; "
        "  [IO.File]::WriteAllBytes($pfxPath, $pfxBytes); "
        "  $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2("
        "    $pfxPath, "
        "    $env:SIGNING_CERT_PASSWORD, "
        "    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable"
        "  ); "
        "} else { "
        "  Write-Host '[*] Checking for existing CodeSigning certificate in Cert:\\CurrentUser\\My...'; "
        "  $cert = Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | Select-Object -First 1; "
        "  if (-not $cert) { "
        "    Write-Host '[*] Creating self-signed Shango code signing certificate...'; "
        "    $cert = New-SelfSignedCertificate -Type CodeSigningCert "
        "      -Subject 'CN=Shango, O=Shango, OU=Verified Build' "
        "      -CertStoreLocation 'Cert:\\CurrentUser\\My' "
        "      -HashAlgorithm SHA256 "
        "      -NotAfter (Get-Date).AddYears(5); "
        "  }; "
        "}; "
        f"Export-Certificate -Cert $cert -FilePath '{cer_dist}' | Out-Null; "
        f"Export-Certificate -Cert $cert -FilePath '{cer_root}' | Out-Null; "
        "$pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store('TrustedPublisher', 'CurrentUser'); "
        "$pubStore.Open('ReadWrite'); "
        "$pubStore.Add($cert); "
        "$pubStore.Close(); "
        "$tsServers = @('http://timestamp.digicert.com', 'http://timestamp.sectigo.com'); "
        "$signed = $false; "
        "foreach ($ts in $tsServers) { "
        "  try { "
        f"    $sig = Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -TimestampServer $ts -HashAlgorithm SHA256; "
        "    if ($sig.SignatureType -eq 'Authenticode') { $signed = $true; break } "
        "  } catch {} "
        "}; "
        "if (-not $signed) { "
        f"  Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -HashAlgorithm SHA256 | Out-Null; "
        "}; "
        f"Get-AuthenticodeSignature '{exe_path}' | Format-List Status, StatusMessage, SignatureType, SignerCertificate, TimeStamperCertificate"
    )
    print("\n[*] Digitally signing Windows executable...")
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if "SignerCertificate" in res.stdout:
            print(f"[+] Successfully signed {exe_path.name}")
            print(f"[+] Public certificate exported to {cer_dist} and {cer_root}")
            print("[+] Certificate registered in Cert:\\CurrentUser\\TrustedPublisher")
        else:
            print(f"[-] Code signing warning: {res.stderr or res.stdout}")
    except Exception as exc:  # noqa: BLE001
        print(f"[-] Code signing skipped: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-platform build script for shango-agent"
    )
    parser.add_argument(
        "-v", "--version", help="Explicit version override (e.g. 1.0.1)"
    )
    parser.add_argument(
        "--dev", action="store_true", help="Force development build with hex timestamp"
    )
    parser.add_argument("--copy-to", help="Optional path to copy the built binary to")
    parser.add_argument(
        "--skip-sign",
        action="store_true",
        help="Skip Authenticode digital signing on Windows",
    )
    args = parser.parse_args()

    system = platform.system().lower()
    machine = platform.machine().lower()
    print(f"=== Building shango-agent for {system} ({machine}) ===")

    # 0. Sync version across version.txt, version_info.txt, and Python modules
    sys.path.insert(0, str(ROOT))
    from development.update_version import update_versions

    current_version = update_versions(override_version=args.version)

    # 1. Ensure PyInstaller is installed
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "[build] PyInstaller not detected. Installing pyinstaller and dependencies..."
        )
        run_cmd(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "pyinstaller",
                "-r",
                str(REQUIREMENTS),
            ]
        )

    # 2. Determine target binary name
    bin_name = "shango-agent"

    # 3. Build single-file executable with maximum compression
    version_file = AGENT_DIR / "version_info.txt"
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--optimize",
        "2",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "pydoc",
        "--exclude-module",
        "test",
        "--exclude-module",
        "sqlite3",
        "--paths",
        str(AGENT_DIR),
        "--hidden-import",
        "_version",
        "--name",
        bin_name,
    ]
    if system != "windows":
        pyinstaller_cmd.append("--strip")

    if system == "windows" and version_file.exists():
        pyinstaller_cmd.extend(["--version-file", str(version_file)])

    pyinstaller_cmd.append(str(AGENT_SCRIPT))

    run_cmd(pyinstaller_cmd)

    dist_path = ROOT / "dist" / (f"{bin_name}.exe" if system == "windows" else bin_name)
    if dist_path.exists():
        size_mb = dist_path.stat().st_size / (1024 * 1024)
        print(
            f"\n[+] Success! Built standalone binary: {dist_path} (v{current_version}, {size_mb:.1f} MB)"
        )
        if system == "windows" and not args.skip_sign:
            sign_windows_binary(dist_path)

        if args.copy_to:
            dest = Path(args.copy_to)
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy2(dist_path, dest)
            print(f"[+] Copied binary to {dest}")
    else:
        print("\n[-] Build finished but output binary was not found in dist/")


if __name__ == "__main__":
    main()
