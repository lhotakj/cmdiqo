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

    # 3. Build single-file executable
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--name",
        bin_name,
        str(AGENT_SCRIPT),
    ]

    run_cmd(pyinstaller_cmd)

    dist_path = ROOT / "dist" / (f"{bin_name}.exe" if system == "windows" else bin_name)
    if dist_path.exists():
        size_mb = dist_path.stat().st_size / (1024 * 1024)
        print(f"\n[+] Success! Built standalone binary: {dist_path} ({size_mb:.1f} MB)")
    else:
        print("\n[-] Build finished but output binary was not found in dist/")


if __name__ == "__main__":
    main()
