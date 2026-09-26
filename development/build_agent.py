#!/usr/bin/env python3
"""Cross-platform build script to compile cmdiqo-agent into standalone binaries."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
AGENT_DIR = ROOT / "agent"
AGENT_SCRIPT = AGENT_DIR / "agent.py"
REQUIREMENTS = AGENT_DIR / "requirements.txt"


def run_cmd(cmd: list[str], check: bool = True) -> None:
    print(f"[build] Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=check)


def sign_windows_binary(exe_path: Path) -> None:
    """Digitally sign the Windows executable using Authenticode and export certificate."""
    cer_dist = exe_path.parent / "cmdiqo-agent.cer"
    cer_root = ROOT / "cmdiqo-agent.cer"
    ps_cmd = (
        "$cert = $null; "
        "if ($env:SIGNING_CERT_BASE64) { "
        "  Write-Host '[*] Using code signing certificate from environment...'; "
        "  $pfxBytes = [Convert]::FromBase64String($env:SIGNING_CERT_BASE64); "
        "  $pfxPath = Join-Path ([IO.Path]::GetTempPath()) 'signing_cert.pfx'; "
        "  [IO.File]::WriteAllBytes($pfxPath, $pfxBytes); "
        "  $secPwd = ConvertTo-SecureString $env:SIGNING_CERT_PASSWORD -AsPlainText -Force; "
        "  $imported = Import-PfxCertificate -FilePath $pfxPath -CertStoreLocation 'Cert:\\CurrentUser\\My' -Password $secPwd; "
        "  $cert = $imported | Select-Object -First 1; "
        "  Remove-Item -Force $pfxPath -ErrorAction SilentlyContinue; "
        "} else { "
        "  Write-Host '[*] Checking for existing CodeSigning certificate in Cert:\\CurrentUser\\My...'; "
        "  $cert = Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | Select-Object -First 1; "
        "  if (-not $cert) { "
        "    Write-Host '[*] Creating self-signed Cmdiqo code signing certificate...'; "
        "    $cert = New-SelfSignedCertificate -Type CodeSigningCert "
        "      -Subject 'CN=CmdiqoHub, O=CmdiqoHub, OU=Verified Build' "
        "      -CertStoreLocation 'Cert:\\CurrentUser\\My' "
        "      -HashAlgorithm SHA256 "
        "      -NotAfter (Get-Date).AddYears(5); "
        "  }; "
        "}; "
        f"Export-Certificate -Cert $cert -FilePath '{cer_dist}' | Out-Null; "
        f"Export-Certificate -Cert $cert -FilePath '{cer_root}' | Out-Null; "
        "Write-Host '[+] Certificate exported to dist and workspace root'; "
        "$tsServers = @('http://timestamp.digicert.com', 'http://timestamp.sectigo.com'); "
        "$signed = $false; "
        "foreach ($ts in $tsServers) { "
        "  try { "
        "    Write-Host \"[*] Timestamping via $ts...\"; "
        f"    $sig = Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -TimestampServer $ts -HashAlgorithm SHA256; "
        "    if ($sig.SignatureType -eq 'Authenticode') { $signed = $true; break } "
        "  } catch {} "
        "}; "
        "if (-not $signed) { "
        "  Write-Host '[*] Timestamp failed, signing directly without timestamp...'; "
        f"  Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -HashAlgorithm SHA256 | Out-Null; "
        "}; "
        f"Get-AuthenticodeSignature '{exe_path}' | Format-List Status, StatusMessage, SignatureType, SignerCertificate, TimeStamperCertificate"
    )
    print("\n[*] Digitally signing Windows executable...")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            cwd=str(ROOT),
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[-] Code signing skipped: {exc}")


import zipfile


def package_agent_zip(system: str, dist_path: Path) -> Path:
    """Pack binary along with install and uninstall scripts into a zip archive and staging directory."""
    dist_dir = dist_path.parent
    pkg_dir = dist_dir / "package"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    if system == "windows":
        zip_name = "cmdiqo-agent-windows-x64.zip"
        files_to_pack = [
            (dist_path, "cmdiqo-agent.exe"),
            (AGENT_DIR / "install-service.ps1", "install-service.ps1"),
            (AGENT_DIR / "uninstall-service.ps1", "uninstall-service.ps1"),
            (AGENT_DIR / "agent.env.example", "agent.env.example"),
            (AGENT_DIR / "agent.json.example", "agent.json.example"),
        ]
        cer_path = dist_dir / "cmdiqo-agent.cer"
        if cer_path.exists():
            files_to_pack.append((cer_path, "cmdiqo-agent.cer"))
        ico_file = ROOT / "development" / "cmdiqo.ico"
        if ico_file.exists():
            files_to_pack.append((ico_file, "cmdiqo.ico"))
    else:
        zip_name = "cmdiqo-agent-linux-x64.zip"
        if dist_path.exists():
            try:
                os.chmod(dist_path, 0o755)
            except OSError:
                pass
        files_to_pack = [
            (dist_path, "cmdiqo-agent"),
            (AGENT_DIR / "install-service.sh", "install-service.sh"),
            (AGENT_DIR / "uninstall-service.sh", "uninstall-service.sh"),
            (AGENT_DIR / "cmdiqo-agent.service", "cmdiqo-agent.service"),
            (AGENT_DIR / "agent.env.example", "agent.env.example"),
            (AGENT_DIR / "agent.json.example", "agent.json.example"),
        ]

    # Populate dist/package/ for GitHub Actions artifact staging (best-effort)
    try:
        pkg_dir.mkdir(parents=True, exist_ok=True)
        for src_path, arc_name in files_to_pack:
            if src_path.exists():
                shutil.copy2(src_path, pkg_dir / arc_name)
    except Exception:
        pass

    zip_path = dist_dir / zip_name
    print(f"\n[*] Packaging zip archive: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src_path, arc_name in files_to_pack:
            if src_path.exists():
                zf.write(src_path, arcname=arc_name)
                print(f"  + Added: {arc_name}")
            else:
                print(f"  - Warning: Missing file {src_path} (skipped)")

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"[+] Zip package created successfully: {zip_path} ({zip_size_mb:.1f} MB)")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-platform build script for cmdiqo-agent"
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
    print(f"=== Building cmdiqo-agent for {system} ({machine}) ===")

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
    bin_name = "cmdiqo-agent"

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
        "--hidden-import",
        "service_windows",
        "--name",
        bin_name,
    ]
    # 2.5 Ensure icon exists or convert from .design logo
    icon_file = ROOT / "development" / "cmdiqo.ico"
    design_logo = ROOT / ".design" / "icon.png"
    if not icon_file.exists() and design_logo.exists():
        try:
            from PIL import Image

            img = Image.open(design_logo)
            img.save(
                icon_file,
                format="ICO",
                sizes=[
                    (16, 16),
                    (24, 24),
                    (32, 32),
                    (48, 48),
                    (64, 64),
                    (128, 128),
                    (256, 256),
                ],
            )
            print(f"[build] Generated icon file: {icon_file}")
        except Exception as exc:  # noqa: BLE001
            print(f"[build] Could not generate icon file: {exc}")

    if system != "windows":
        pyinstaller_cmd.append("--strip")

    if system in ("windows", "darwin") and icon_file.exists():
        pyinstaller_cmd.extend(["--icon", str(icon_file)])

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

        zip_path = package_agent_zip(system, dist_path)

        if args.copy_to:
            dest = Path(args.copy_to)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dist_path, dest)
            print(f"[+] Copied binary to {dest}")
            if dest.suffix == ".exe" or dest.name == "cmdiqo-agent":
                copy_zip = dest.parent / zip_path.name
                shutil.copy2(zip_path, copy_zip)
                print(f"[+] Copied zip package to {copy_zip}")
    else:
        print("\n[-] Build finished but output binary was not found in dist/")


if __name__ == "__main__":
    main()
