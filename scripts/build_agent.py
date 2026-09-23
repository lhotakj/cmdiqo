"""Cross-platform build script to compile shango-agent into standalone binaries."""

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
    cer_path = exe_path.parent / "shango-agent.cer"
    ps_cmd = (
        "$cert = Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | Select-Object -First 1; "
        "if (-not $cert) { "
        "$cert = New-SelfSignedCertificate -Type CodeSigningCert "
        "-Subject 'CN=ShangoHub, O=ShangoHub, OU=Verified Build' "
        "-CertStoreLocation 'Cert:\\CurrentUser\\My' "
        "-HashAlgorithm SHA256 "
        "-NotAfter (Get-Date).AddYears(5) "
        "}; "
        f"Export-Certificate -Cert $cert -FilePath '{cer_path}' | Out-Null; "
        f"$sig = Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -TimestampServer 'http://timestamp.digicert.com' -HashAlgorithm SHA256; "
        "if ($sig.SignatureType -ne 'Authenticode') { "
        f"Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert -HashAlgorithm SHA256 | Out-Null "
        "}; "
        f"Get-AuthenticodeSignature '{exe_path}' | Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate"
    )
    print("\n[*] Digitally signing Windows executable...")
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, check=False)
        if "SignerCertificate" in res.stdout:
            print(f"[+] Successfully signed {exe_path.name}")
            print(f"[+] Public certificate exported to {cer_path}")
        else:
            print(f"[-] Code signing warning: {res.stderr or res.stdout}")
    except Exception as exc:  # noqa: BLE001
        print(f"[-] Code signing skipped: {exc}")


def main() -> None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    print(f"=== Building shango-agent for {system} ({machine}) ===")

    # 1. Ensure PyInstaller is installed
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] PyInstaller not detected. Installing pyinstaller and dependencies...")
        run_cmd([sys.executable, "-m", "pip", "install", "pyinstaller", "-r", str(REQUIREMENTS)])

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
        print(f"\n[+] Success! Built standalone binary: {dist_path} ({size_mb:.1f} MB)")
        if system == "windows":
            sign_windows_binary(dist_path)
    else:
        print("\n[-] Build finished but output binary was not found in dist/")


if __name__ == "__main__":
    main()

