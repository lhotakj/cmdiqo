#!/usr/bin/env python3
"""Cross-platform build script to compile cmdiqo-agent into standalone binaries."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
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


def _legacy_sign_windows_binary(exe_path: Path) -> None:
    """Digitally sign a Windows executable and export its public certificate."""
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
        "$tsServers = @('http://timestamp.sectigo.com'); "
        "$signed = $false; "
        "foreach ($ts in $tsServers) { "
        "  try { "
        "    Write-Host \"[*] Timestamping via $ts...\"; "
        f"    $sig = Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -TimestampServer $ts -HashAlgorithm SHA256; "
        "    if ($sig.SignatureType -eq 'Authenticode') { $signed = $true; break } "
        "  } catch { Write-Host \"[!] Timestamp server failed: $($_.Exception.Message)\" } "
        "}; "
        "if (-not $signed) { "
        "  Write-Host '[*] Timestamp failed, signing directly without timestamp...'; "
        f"  Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -HashAlgorithm SHA256 | Out-Null; "
        "}; "
        f"$verification = Get-AuthenticodeSignature '{exe_path}'; "
        "$verification | Format-List Status, StatusMessage, SignatureType, SignerCertificate, TimeStamperCertificate; "
        "if ($verification.SignatureType -ne 'Authenticode') { throw 'Authenticode signing did not produce a signature.' }"
    )
    print("\n[*] Digitally signing Windows executable...")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        cwd=str(ROOT),
        check=True,
    )


def sign_windows_binary(exe_path: Path) -> None:
    """Sign a Windows executable using Windows PowerShell's certificate provider."""
    if os.name != "nt":
        return

    cer_dist = exe_path.parent / "cmdiqo-agent.cer"
    cer_root = ROOT / "cmdiqo-agent.cer"
    ps_executable = Path(os.environ["WINDIR"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not ps_executable.is_file():
        raise RuntimeError(f"Windows PowerShell was not found at {ps_executable}")

    exe = str(exe_path).replace("'", "''")
    dist_cer = str(cer_dist).replace("'", "''")
    root_cer = str(cer_root).replace("'", "''")
    ps_cmd = (
        "$ErrorActionPreference = 'Stop'; "
        "Import-Module Microsoft.PowerShell.Security; "
        "$store = 'Cert:\\CurrentUser\\My'; "
        "$cert = $null; "
        "if ($env:SIGNING_CERT_BASE64) { "
        "  $pfxPath = Join-Path ([IO.Path]::GetTempPath()) 'cmdiqo-signing.pfx'; "
        "  try { "
        "    [IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($env:SIGNING_CERT_BASE64)); "
        "    $password = ConvertTo-SecureString $env:SIGNING_CERT_PASSWORD -AsPlainText -Force; "
        "    $cert = Import-PfxCertificate -FilePath $pfxPath -CertStoreLocation $store -Password $password | Select-Object -First 1; "
        "  } finally { Remove-Item -Force $pfxPath -ErrorAction SilentlyContinue } "
        "} else { "
        "  $cert = Get-ChildItem -Path $store | Where-Object { "
        "    $_.HasPrivateKey -and $_.EnhancedKeyUsageList.ObjectId.Value -contains '1.3.6.1.5.5.7.3.3' "
        "  } | Select-Object -First 1; "
        "  if (-not $cert) { "
        "    $cert = New-SelfSignedCertificate -Type CodeSigningCert "
        "      -Subject 'CN=CmdiqoHub, O=CmdiqoHub, OU=Verified Build' "
        "      -CertStoreLocation $store -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(5); "
        "  } "
        "}; "
        "if (-not $cert -or -not $cert.HasPrivateKey) { throw 'No code-signing certificate with a private key is available.' }; "
        f"Export-Certificate -Cert $cert -FilePath '{dist_cer}' -Force | Out-Null; "
        f"Export-Certificate -Cert $cert -FilePath '{root_cer}' -Force | Out-Null; "
        "$signed = $false; "
        "foreach ($timestampServer in @('http://timestamp.sectigo.com')) { "
        "  try { "
        f"    $signature = Set-AuthenticodeSignature -FilePath '{exe}' -Certificate $cert -TimestampServer $timestampServer -HashAlgorithm SHA256; "
        "    if ($signature.SignatureType -eq 'Authenticode') { $signed = $true; break } "
        "  } catch { Write-Warning \"Timestamp server failed: $($_.Exception.Message)\" } "
        "}; "
        "if (-not $signed) { "
        f"  $signature = Set-AuthenticodeSignature -FilePath '{exe}' -Certificate $cert -HashAlgorithm SHA256; "
        "}; "
        "if ($signature.SignatureType -ne 'Authenticode') { throw 'Authenticode signing did not produce a signature.' }; "
        f"Get-AuthenticodeSignature -FilePath '{exe}' | Format-List Status, StatusMessage, SignatureType, SignerCertificate, TimeStamperCertificate"
    )
    print("\n[*] Digitally signing Windows executable...")
    subprocess.run(
        [str(ps_executable), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
        cwd=str(ROOT),
        check=True,
    )


def package_agent_zip(system: str, dist_path: Path) -> Path:
    """Pack binary along with install and uninstall scripts into a zip archive and staging directory."""
    dist_dir = dist_path.parent
    pkg_dir = dist_dir / "package"
    if pkg_dir.exists():
        if pkg_dir.is_dir():
            shutil.rmtree(pkg_dir)
        else:
            pkg_dir.unlink()
    pkg_dir.mkdir(parents=True)

    if system == "windows":
        zip_name = "cmdiqo-agent-windows-x64.zip"
        files_to_pack = [
            (dist_path, "cmdiqo-agent.exe"),
            (AGENT_DIR / "install-service.ps1", "install.ps1"),
            (AGENT_DIR / "uninstall-service.ps1", "uninstall.ps1"),
            (AGENT_DIR / "agent.env.example", "agent.env.example"),
            (AGENT_DIR / "agent.json.example", "agent.json.example"),
        ]
        cer_path = dist_dir / "cmdiqo-agent.cer"
        if cer_path.exists():
            files_to_pack.append((cer_path, "cmdiqo-agent.cer"))
    else:
        zip_name = "cmdiqo-agent-linux-x64.zip"
        if dist_path.exists():
            try:
                os.chmod(dist_path, 0o755)
            except OSError:
                pass
        files_to_pack = [
            (dist_path, "cmdiqo-agent"),
            (AGENT_DIR / "install-service.sh", "install.sh"),
            (AGENT_DIR / "uninstall-service.sh", "uninstall.sh"),
            (AGENT_DIR / "cmdiqo-agent.service", "cmdiqo-agent.service"),
            (AGENT_DIR / "agent.env.example", "agent.env.example"),
            (AGENT_DIR / "agent.json.example", "agent.json.example"),
        ]

    files_to_pack.append((dist_dir / "build-version.txt", "version.txt"))

    for src_path, arc_name in files_to_pack:
        if not src_path.is_file():
            raise FileNotFoundError(f"Required package file is missing: {src_path}")
        staged_path = pkg_dir / arc_name
        shutil.copy2(src_path, staged_path)
        if system != "windows" and arc_name in {
            "cmdiqo-agent",
            "install.sh",
            "uninstall.sh",
        }:
            os.chmod(staged_path, 0o755)

    zip_path = dist_dir / zip_name
    print(f"\n[*] Packaging zip archive: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src_path, arc_name in files_to_pack:
            zip_info = zipfile.ZipInfo.from_file(src_path, arcname=arc_name)
            if system != "windows" and arc_name in {
                "cmdiqo-agent",
                "install.sh",
                "uninstall.sh",
            }:
                zip_info.create_system = 3
                zip_info.external_attr = (stat.S_IFREG | 0o755) << 16
            zf.writestr(zip_info, src_path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
            print(f"  + Added: {arc_name}")

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
        "--noconfirm",
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

        (dist_path.parent / "build-version.txt").write_text(
            current_version, encoding="utf-8"
        )
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
