"""Backend module for Cmdiqo Agent cross-platform installer/uninstaller.

Handles version detection, upgrade logic, Windows Service / systemd management,
configuration persistence, and zip payload extraction.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

log = logging.getLogger("cmdiqo-installer")

IS_WINDOWS = platform.system().lower() == "windows"
IS_LINUX = platform.system().lower() == "linux"

CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
ServiceStatus = Literal["running", "stopped", "not_installed"]


def run_cmd_silent(cmd: list[str] | str, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess command silently on Windows with NO console window flashing."""
    if IS_WINDOWS:
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        if "startupinfo" not in kwargs:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= STARTF_USESHOWWINDOW
            si.wShowWindow = SW_HIDE
            kwargs["startupinfo"] = si
    return subprocess.run(cmd, **kwargs)


@dataclass
class PlatformPaths:
    install_dir: Path
    binary_path: Path
    config_dir: Path
    config_file: Path
    log_dir: Path
    service_name: str
    service_file: Optional[Path] = None
    setup_path: Optional[Path] = None
    desktop_file: Optional[Path] = None
    start_menu_file: Optional[Path] = None

    def __post_init__(self):
        if self.setup_path is None:
            self.setup_path = self.install_dir / (
                "cmdiqo-agent-windows-x64-setup.exe"
                if IS_WINDOWS
                else "cmdiqo-agent-linux-x64-setup"
            )
        if self.desktop_file is None and IS_LINUX:
            self.desktop_file = Path("/usr/share/applications/cmdiqo-agent.desktop")
        if self.start_menu_file is None and IS_WINDOWS:
            prog_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
            self.start_menu_file = prog_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Cmdiqo Remote Agent.lnk"


def get_platform_paths() -> PlatformPaths:
    """Return standard installation and configuration paths for the current OS."""
    if IS_WINDOWS:
        prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        prog_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        install_dir = prog_files / "Cmdiqo" / "Agent"
        config_dir = prog_data / "Cmdiqo" / "Agent"
        start_menu_file = prog_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Cmdiqo Remote Agent.lnk"
        return PlatformPaths(
            install_dir=install_dir,
            binary_path=install_dir / "cmdiqo-agent.exe",
            setup_path=install_dir / "cmdiqo-agent-windows-x64-setup.exe",
            config_dir=config_dir,
            config_file=config_dir / "config.json",
            log_dir=config_dir / "logs",
            service_name="CmdiqoAgent",
            service_file=None,
            desktop_file=None,
            start_menu_file=start_menu_file,
        )
    else:
        # Linux standard paths
        install_dir = Path("/usr/local/bin")
        config_dir = Path("/etc/cmdiqo-agent")
        return PlatformPaths(
            install_dir=install_dir,
            binary_path=install_dir / "cmdiqo-agent",
            setup_path=install_dir / "cmdiqo-agent-linux-x64-setup",
            config_dir=config_dir,
            config_file=config_dir / "agent.env",
            log_dir=Path("/var/log/cmdiqo-agent"),
            service_name="cmdiqo-agent.service",
            service_file=Path("/etc/systemd/system/cmdiqo-agent.service"),
            desktop_file=Path("/usr/share/applications/cmdiqo-agent.desktop"),
        )


def is_admin() -> bool:
    """Check if the current process is running with administrative / root privileges."""
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False


def request_elevation() -> bool:
    """Re-launch the installer executable or script with Administrator/root privileges."""
    if is_admin():
        return True

    if IS_WINDOWS:
        exe = sys.executable
        # If running via python script: exe is python.exe, pass script as first arg
        if getattr(sys, "frozen", False):
            cmd_args = " ".join([f'"{a}"' for a in sys.argv[1:]])
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, cmd_args, None, 1)
        else:
            script = sys.argv[0]
            cmd_args = f'"{script}" ' + " ".join([f'"{a}"' for a in sys.argv[1:]])
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, cmd_args, None, 1)
        return ret > 32
    else:
        # Linux: If running in an interactive terminal, re-exec via sudo so user can enter password
        args = sys.argv if getattr(sys, "frozen", False) else [sys.executable] + sys.argv
        if sys.stdin.isatty() and shutil.which("sudo"):
            try:
                os.execvp("sudo", ["sudo"] + args)
            except Exception:
                pass
        # If in a desktop GUI environment without terminal, try GUI polkit dialog
        elif bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and shutil.which("pkexec"):
            try:
                subprocess.Popen(["pkexec"] + args)
                return True
            except Exception:
                pass
        return False


def parse_version_tuple(v_str: str) -> tuple:
    """Parse version string into comparable tuple (major, minor, patch, is_release, extra)."""
    if not v_str:
        return (0, 0, 0, 0, "")
    v_clean = v_str.strip().lstrip("vV")
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-]?(.*))?$", v_clean)
    if not m:
        return (0, 0, 0, 0, v_clean)
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    extra = (m.group(4) or "").strip()
    is_release = 0 if "dev" in extra.lower() else 1
    return (major, minor, patch, is_release, extra)


def compare_versions(ver_a: str, ver_b: str) -> int:
    """Compare two version strings.

    Returns:
        -1 if ver_a < ver_b (ver_a is older than ver_b)
         0 if ver_a == ver_b (same version)
         1 if ver_a > ver_b (ver_a is newer than ver_b)
    """
    pa = parse_version_tuple(ver_a)
    pb = parse_version_tuple(ver_b)
    if pa < pb:
        return -1
    elif pa > pb:
        return 1
    return 0


_cached_installed_version: Optional[str] = None


def get_installed_version(paths: Optional[PlatformPaths] = None, force_refresh: bool = False) -> Optional[str]:
    """Detect version of currently installed cmdiqo-agent instantaneously.

    Uses high-speed zero-overhead checks (cache, version.txt, registry) before
    falling back to binary invocation to ensure sub-millisecond UI launch without
    flashing any CMD console window.
    """
    global _cached_installed_version
    if not force_refresh and _cached_installed_version is not None:
        return _cached_installed_version

    if paths is None:
        paths = get_platform_paths()

    if not paths.binary_path.exists():
        _cached_installed_version = None
        return None

    # 1. Fast check: install_dir / version.txt (0.1ms)
    v_file = paths.install_dir / "version.txt"
    if v_file.exists():
        try:
            raw = v_file.read_text(encoding="utf-8").strip()
            m = re.match(r"^(\d+\.\d+(?:\.\d+)?(?:[.-]?\S*)?)", raw)
            if m:
                _cached_installed_version = m.group(1).rstrip(".")
                return _cached_installed_version
        except Exception:
            pass

    # 2. Fast check on Windows: Registry DisplayVersion (0.1ms)
    if IS_WINDOWS:
        try:
            import winreg

            for access_flag in (getattr(winreg, "KEY_WOW64_64KEY", 0x0100), getattr(winreg, "KEY_WOW64_32KEY", 0x0200)):
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CmdiqoAgent",
                        0,
                        winreg.KEY_READ | access_flag,
                    ) as key:
                        val, _ = winreg.QueryValueEx(key, "DisplayVersion")
                        if val:
                            _cached_installed_version = str(val).strip()
                            return _cached_installed_version
                except FileNotFoundError:
                    continue
        except Exception:
            pass

    # 3. Fallback: Run binary silently with CREATE_NO_WINDOW
    try:
        res = run_cmd_silent(
            [str(paths.binary_path), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = (res.stdout + "\n" + res.stderr).strip()
        m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:[.-]?\S*)?)", output)
        if m:
            ver = m.group(1).rstrip(".")
            _cached_installed_version = ver
            try:
                (paths.install_dir / "version.txt").write_text(ver, encoding="utf-8")
            except Exception:
                pass
            return ver
    except Exception as exc:
        log.warning("Could not execute %s --version: %s", paths.binary_path, exc)

    return None


def get_bundled_version() -> str:
    """Retrieve version bundled in this installer package."""
    # Check embedded version.txt in executable dir, _internal, _MEIPASS, or source dirs
    search_dirs = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        search_dirs.append(exe_dir)
        search_dirs.append(exe_dir / "_internal")
        if hasattr(sys, "_MEIPASS"):
            search_dirs.append(Path(sys._MEIPASS))
    search_dirs.extend([
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ])

    for d in search_dirs:
        v_file = d / "version.txt"
        if v_file.exists():
            try:
                raw = v_file.read_text(encoding="utf-8").strip()
                m = re.match(r"^(\d+\.\d+(?:\.\d+)?(?:[.-]?\S*)?)", raw)
                if m:
                    return m.group(1)
            except Exception:
                pass

        # Also check agent/_version.py
        mod_version = d / "agent" / "_version.py"
        if mod_version.exists():
            try:
                content = mod_version.read_text(encoding="utf-8")
                m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
                if m:
                    return m.group(1)
            except Exception:
                pass

    return "0.9.0"


def get_service_status(paths: Optional[PlatformPaths] = None) -> ServiceStatus:
    """Return the installed agent service's current state without changing it."""
    if paths is None:
        paths = get_platform_paths()

    if IS_WINDOWS:
        try:
            result = run_cmd_silent(
                ["sc.exe", "query", paths.service_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            log.warning("Could not query Windows service %s: %s", paths.service_name, exc)
            return "not_installed"
        output = f"{result.stdout}\n{result.stderr}".upper()
        if result.returncode != 0 or "FAILED 1060" in output:
            return "not_installed"
        if "RUNNING" in output:
            return "running"
        return "stopped"

    if IS_LINUX:
        if not paths.binary_path.exists() and not (
            paths.service_file and paths.service_file.exists()
        ):
            return "not_installed"
        try:
            result = run_cmd_silent(
                ["systemctl", "is-active", paths.service_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            log.warning("Could not query Linux service %s: %s", paths.service_name, exc)
            return "stopped"
        return "running" if result.stdout.strip() == "active" else "stopped"

    return "not_installed"


def find_bundled_zip() -> Optional[Path]:
    """Find the bundled agent zip payload."""
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([
            exe_dir / "agent-payload.zip",
            exe_dir / "cmdiqo-agent-windows-x64.zip",
            exe_dir / "cmdiqo-agent-linux-x64.zip",
            exe_dir / "_internal" / "agent-payload.zip",
            exe_dir / "_internal" / "cmdiqo-agent-windows-x64.zip",
            exe_dir / "_internal" / "cmdiqo-agent-linux-x64.zip",
        ])
        if hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)
            candidates.extend([
                meipass / "agent-payload.zip",
                meipass / "cmdiqo-agent-windows-x64.zip",
                meipass / "cmdiqo-agent-linux-x64.zip",
            ])

    root = Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"
    candidates.extend([
        dist_dir / "cmdiqo-agent-windows-x64.zip",
        dist_dir / "cmdiqo-agent-linux-x64.zip",
        dist_dir / "agent-payload.zip",
        dist_dir / "cmdiqo-agent-windows-x64-setup" / "cmdiqo-agent-windows-x64.zip",
        dist_dir / "cmdiqo-agent-linux-x64-setup" / "cmdiqo-agent-linux-x64.zip",
        dist_dir / "cmdiqo-agent-windows-x64-setup" / "_internal" / "cmdiqo-agent-windows-x64.zip",
        dist_dir / "cmdiqo-agent-linux-x64-setup" / "_internal" / "cmdiqo-agent-linux-x64.zip",
        root / "agent-payload.zip",
    ])

    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def get_existing_config(paths: Optional[PlatformPaths] = None) -> dict:
    """Load existing configuration from config.json or agent.env if present."""
    if paths is None:
        paths = get_platform_paths()

    cfg: dict = {
        "hub_url": "ws://192.168.1.10:8765",
        "token": "",
        "name": socket.gethostname(),
        "agent_connection_notify": False,
    }

    # 1. Check config.json (standard on Windows and modern Linux)
    json_path = paths.config_dir / "config.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if v is not None})
                return cfg
        except Exception as exc:
            log.warning("Could not read JSON config: %s", exc)

    # 2. Check agent.env (legacy Linux)
    env_path = paths.config_dir / "agent.env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip().upper()
                    v = v.strip().strip('"').strip("'")
                    if k == "HUB_URL":
                        cfg["hub_url"] = v
                    elif k in ("AGENT_TOKEN", "TOKEN"):
                        cfg["token"] = v
                    elif k in ("AGENT_NAME", "NAME"):
                        cfg["name"] = v
                    elif k in ("AGENT_CONNECTION_NOTIFY", "AGENT_NOTIFICATION_NOTIFY"):
                        cfg["agent_connection_notify"] = v.lower() in ("true", "1", "yes", "y")
        except Exception as exc:
            log.warning("Could not read ENV config: %s", exc)

    return cfg


def save_config(cfg: dict, paths: Optional[PlatformPaths] = None) -> None:
    """Save configuration to platform config directory and configure secure ACLs."""
    if paths is None:
        paths = get_platform_paths()

    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)

    # Save JSON config
    json_path = paths.config_dir / "config.json"
    json_data = {
        "hub_url": cfg.get("hub_url", "").strip(),
        "token": cfg.get("token", "").strip(),
        "name": cfg.get("name", socket.gethostname()).strip(),
        "reconnect_delay": 5.0,
        "agent_connection_notify": bool(cfg.get("agent_connection_notify", False)),
    }
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

    # On Linux, also write agent.env for systemd EnvironmentFile compatibility
    if IS_LINUX:
        env_path = paths.config_dir / "agent.env"
        env_content = (
            f"HUB_URL={json_data['hub_url']}\n"
            f"AGENT_TOKEN={json_data['token']}\n"
            f"AGENT_NAME={json_data['name']}\n"
            f"AGENT_CONNECTION_NOTIFY={'true' if json_data['agent_connection_notify'] else 'false'}\n"
        )
        env_path.write_text(env_content, encoding="utf-8")
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass

    # Windows ACLs configuration
    if IS_WINDOWS:
        try:
            run_cmd_silent(
                [
                    "icacls.exe",
                    str(paths.config_dir),
                    "/grant:r",
                    "BUILTIN\\Administrators:(OI)(CI)(F)",
                    "NT AUTHORITY\\SYSTEM:(OI)(CI)(F)",
                    "BUILTIN\\Users:(OI)(CI)(M)",
                    "/T",
                ],
                capture_output=True,
                check=False,
            )
        except Exception as exc:
            log.warning("icacls error: %s", exc)


def check_hub_connection(hub_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Test raw TCP / WebSocket connectivity to the configured Hub URL."""
    try:
        url = hub_url.strip()
        # Parse ws://host:port or wss://host:port
        m = re.match(r"^wss?://([^:/]+)(?::(\d+))?", url)
        if not m:
            return False, "Invalid WebSocket URL format (must be ws://host:port or wss://host:port)"
        host = m.group(1)
        port = int(m.group(2)) if m.group(2) else (443 if url.startswith("wss://") else 80)

        # Quick TCP socket connect test
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True, f"Successfully reached Hub host {host}:{port}!"
    except socket.timeout:
        return False, f"Connection timed out after {timeout:.1f}s connecting to Hub"
    except Exception as exc:
        return False, f"Could not connect to Hub: {exc}"


def stop_service(paths: Optional[PlatformPaths] = None) -> bool:
    """Stop the running service on Windows or Linux and ensure process has exited."""
    if paths is None:
        paths = get_platform_paths()

    if IS_WINDOWS:
        try:
            result = run_cmd_silent(
                ["sc.exe", "stop", paths.service_name],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode not in (0, 1062):
                log.error(
                    "Could not stop Windows service %s: %s",
                    paths.service_name,
                    result.stderr.strip() or result.stdout.strip(),
                )
                return False
            time.sleep(1)
        except OSError as exc:
            log.error("Could not stop Windows service %s: %s", paths.service_name, exc)
            return False
        # Kill any lingering cmdiqo-agent.exe process to release file locks
        try:
            run_cmd_silent(
                ["taskkill.exe", "/F", "/IM", "cmdiqo-agent.exe", "/T"],
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            log.warning("Could not terminate lingering agent process: %s", exc)
        return get_service_status(paths) == "stopped"
    elif IS_LINUX:
        try:
            result = run_cmd_silent(
                ["systemctl", "stop", paths.service_name],
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            log.error("Could not stop Linux service %s: %s", paths.service_name, exc)
            return False
        try:
            run_cmd_silent(
                ["pkill", "-9", "-f", "cmdiqo-agent"],
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            log.warning("Could not terminate lingering agent process: %s", exc)
        return result.returncode == 0 and get_service_status(paths) == "stopped"
    return False


def start_service(paths: Optional[PlatformPaths] = None) -> bool:
    """Start the service on Windows or Linux."""
    if paths is None:
        paths = get_platform_paths()

    if IS_WINDOWS:
        res = run_cmd_silent(["sc.exe", "start", paths.service_name], capture_output=True, text=True, check=False)
        return "RUNNING" in res.stdout or "START_PENDING" in res.stdout or res.returncode == 0
    elif IS_LINUX:
        res = run_cmd_silent(["systemctl", "restart", paths.service_name], capture_output=True, text=True, check=False)
        return res.returncode == 0
    return False


def restart_service(paths: Optional[PlatformPaths] = None) -> bool:
    """Restart the installed service and report whether it accepted the start request."""
    if paths is None:
        paths = get_platform_paths()

    if IS_WINDOWS:
        if get_service_status(paths) == "running" and not stop_service(paths):
            return False
        return start_service(paths)
    if IS_LINUX:
        return start_service(paths)
    return False


def extract_and_install_binary(
    paths: Optional[PlatformPaths] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Extract agent binary and auxiliary files from bundled zip and install to target location."""
    if paths is None:
        paths = get_platform_paths()

    def report(msg: str):
        if progress_callback:
            progress_callback(msg)
        log.info(msg)

    paths.install_dir.mkdir(parents=True, exist_ok=True)
    zip_path = find_bundled_zip()

    if not zip_path:
        report("[-] No bundled zip payload found. Checking for pre-compiled binary in dist/...")
        root = Path(__file__).resolve().parent.parent
        dist_bin = root / "dist" / ("cmdiqo-agent.exe" if IS_WINDOWS else "cmdiqo-agent")
        if dist_bin.exists():
            report(f"[+] Found binary at {dist_bin}. Copying...")
            shutil.copy2(dist_bin, paths.binary_path)
            if not IS_WINDOWS:
                os.chmod(paths.binary_path, 0o755)
            # Write version.txt for instantaneous future detection
            bundled_ver = get_bundled_version()
            try:
                (paths.install_dir / "version.txt").write_text(bundled_ver, encoding="utf-8")
            except Exception:
                pass
            return True
        report("[-] Error: No executable binary found to install.")
        return False

    report(f"[*] Extracting package from {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for item in zf.namelist():
            # Match binary
            if (IS_WINDOWS and item == "cmdiqo-agent.exe") or (not IS_WINDOWS and item == "cmdiqo-agent"):
                report(f"[+] Installing {item} -> {paths.binary_path}...")
                with zf.open(item) as src, open(paths.binary_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if not IS_WINDOWS:
                    os.chmod(paths.binary_path, 0o755)

            # Match certificate on Windows
            elif IS_WINDOWS and item.endswith(".cer"):
                cer_dest = paths.install_dir / item
                with zf.open(item) as src, open(cer_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                # Register certificate into Trusted Root and Trusted Publisher stores
                try:
                    run_cmd_silent(["certutil.exe", "-addstore", "-user", "Root", str(cer_dest)], capture_output=True, check=False)
                    run_cmd_silent(["certutil.exe", "-addstore", "-user", "TrustedPublisher", str(cer_dest)], capture_output=True, check=False)
                    report(f"[+] Trusted Authenticode certificate {item}")
                except Exception:
                    pass

            # Match application icon on Windows
            elif IS_WINDOWS and item == "cmdiqo.ico":
                ico_dest = paths.install_dir / item
                with zf.open(item) as src, open(ico_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                report(f"[+] Installed application icon: {ico_dest}")

            # Match Linux systemd unit
            elif IS_LINUX and item == "cmdiqo-agent.service" and paths.service_file:
                with zf.open(item) as src, open(paths.service_file, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                report(f"[+] Installed systemd unit to {paths.service_file}")

    # Write version.txt for instantaneous future detection
    bundled_ver = get_bundled_version()
    try:
        (paths.install_dir / "version.txt").write_text(bundled_ver, encoding="utf-8")
    except Exception:
        pass

    return paths.binary_path.exists()


def register_service(
    paths: Optional[PlatformPaths] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Register cmdiqo-agent as a native Windows service or systemd service."""
    if paths is None:
        paths = get_platform_paths()

    def report(msg: str):
        if progress_callback:
            progress_callback(msg)
        log.info(msg)

    if IS_WINDOWS:
        bin_cmd = f'"{paths.binary_path}" --service --config "{paths.config_file}"'

        def run_sc(action: str, command: list[str], required: bool = False) -> bool:
            result = run_cmd_silent(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return True

            output = "\n".join(
                part.strip()
                for part in (result.stdout or "", result.stderr or "")
                if part.strip()
            )
            report(
                f"[-] sc.exe {action} failed with exit code {result.returncode}: "
                f"{output or 'no output'}"
            )
            return not required

        # Check if service already exists
        chk = run_cmd_silent(
            ["sc.exe", "query", paths.service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if "FAILED 1060" not in chk.stdout and chk.returncode == 0:
            report(f"[*] Updating existing Windows Service '{paths.service_name}'...")
            if not run_sc(
                "config",
                [
                    "sc.exe",
                    "config",
                    paths.service_name,
                    "binPath=",
                    bin_cmd,
                    "start=",
                    "auto",
                    "DisplayName=",
                    "Cmdiqo LAN Remote Agent",
                ],
                required=True,
            ):
                return False
        else:
            report(f"[*] Registering Windows Service '{paths.service_name}' with SCM...")
            if not run_sc(
                "create",
                [
                    "sc.exe",
                    "create",
                    paths.service_name,
                    "binPath=",
                    bin_cmd,
                    "start=",
                    "auto",
                    "DisplayName=",
                    "Cmdiqo LAN Remote Agent",
                ],
                required=True,
            ):
                return False

        # Set descriptive summary in Windows Services (services.msc)
        run_sc(
            "description",
            [
                "sc.exe",
                "description",
                paths.service_name,
                "Cmdiqo Slack LAN Remote Agent reverse WebSocket connection service",
            ],
        )

        # Configure automatic restart on failure
        run_sc(
            "failure",
            [
                "sc.exe",
                "failure",
                paths.service_name,
                "reset=",
                "86400",
                "actions=",
                "restart/5000/restart/10000/restart/30000",
            ],
        )
        return True

    elif IS_LINUX:
        # Default systemd unit if not copied from zip
        if paths.service_file and not paths.service_file.exists():
            service_content = (
                "[Unit]\n"
                "Description=Cmdiqo LAN Remote Agent\n"
                "After=network-online.target\n"
                "Wants=network-online.target\n\n"
                "[Service]\n"
                "Type=simple\n"
                f"ExecStart={paths.binary_path} --config {paths.config_dir}/config.json\n"
                f"EnvironmentFile=-{paths.config_dir}/agent.env\n"
                "Restart=always\n"
                "RestartSec=5\n\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n"
            )
            paths.service_file.parent.mkdir(parents=True, exist_ok=True)
            paths.service_file.write_text(service_content, encoding="utf-8")

        report("[*] Reloading systemd and enabling service...")
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False)
        subprocess.run(["systemctl", "enable", paths.service_name], capture_output=True, check=False)
        return True

    return False


def copy_setup_executable(
    paths: PlatformPaths,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Stage the setup executable itself into paths.setup_path for Windows 11 Installed Apps uninstall support."""
    def report(msg: str):
        if progress_callback:
            progress_callback(msg)
        log.info(msg)

    src_exe: Optional[Path] = None
    if getattr(sys, "frozen", False):
        src_exe = Path(sys.executable)
    else:
        root = Path(__file__).resolve().parent.parent
        dist_setup = root / "dist" / (
            "cmdiqo-agent-windows-x64-setup.exe"
            if IS_WINDOWS
            else "cmdiqo-agent-linux-x64-setup"
        )
        if dist_setup.exists():
            src_exe = dist_setup
        else:
            dist_setup_dir = root / "dist" / (
                "cmdiqo-agent-windows-x64-setup"
                if IS_WINDOWS
                else "cmdiqo-agent-linux-x64-setup"
            ) / (
                "cmdiqo-agent-windows-x64-setup.exe"
                if IS_WINDOWS
                else "cmdiqo-agent-linux-x64-setup"
            )
            if dist_setup_dir.exists():
                src_exe = dist_setup_dir

    if src_exe and src_exe.exists():
        try:
            # Check if running in onedir mode (has _internal or sibling python dlls)
            is_onedir = (
                (src_exe.parent / "_internal").exists()
                or (src_exe.parent / "python3.dll").exists()
                or (src_exe.parent / "base_library.zip").exists()
                or src_exe.parent.name.endswith("-setup")
            )
            if is_onedir:
                dest_dir = paths.install_dir / "setup"
                dest_exe = dest_dir / src_exe.name
                if src_exe.parent.resolve() != dest_dir.resolve():
                    report(f"[*] Staging setup directory to {dest_dir}...")
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    for item in src_exe.parent.iterdir():
                        d_item = dest_dir / item.name
                        if item.is_dir():
                            if d_item.exists():
                                shutil.rmtree(d_item, ignore_errors=True)
                            shutil.copytree(item, d_item)
                        else:
                            shutil.copy2(item, d_item)
                    if not IS_WINDOWS:
                        os.chmod(dest_exe, 0o755)
                paths.setup_path = dest_exe
            else:
                if src_exe.resolve() != paths.setup_path.resolve():
                    report(f"[*] Staging setup executable to {paths.setup_path}...")
                    shutil.copy2(src_exe, paths.setup_path)
                    if not IS_WINDOWS:
                        os.chmod(paths.setup_path, 0o755)
        except Exception as exc:
            log.warning("Could not stage setup executable: %s", exc)


def create_windows_shortcut(
    target_exe: Path,
    shortcut_path: Path,
    description: str = "Cmdiqo Remote Agent Setup and Configuration",
    icon_path: Optional[Path] = None,
) -> bool:
    """Create Windows Start Menu .lnk shortcut using WScript.Shell COM object."""
    if not IS_WINDOWS:
        return False
    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        ps_target = str(target_exe).replace("'", "''")
        ps_shortcut = str(shortcut_path).replace("'", "''")
        ps_workdir = str(target_exe.parent).replace("'", "''")
        ps_desc = description.replace("'", "''")

        icon_line = ""
        if icon_path and icon_path.exists():
            ps_icon = str(icon_path).replace("'", "''")
            icon_line = f"$s.IconLocation = '{ps_icon}'; "

        ps_cmd = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{ps_shortcut}'); "
            f"$s.TargetPath = '{ps_target}'; "
            f"$s.WorkingDirectory = '{ps_workdir}'; "
            f"{icon_line}"
            f"$s.Description = '{ps_desc}'; "
            f"$s.Save()"
        )
        res = run_cmd_silent(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            check=False,
        )
        return res.returncode == 0
    except Exception as exc:
        log.warning("Could not create Windows Start Menu shortcut: %s", exc)
        return False


def register_windows_app_entry(paths: PlatformPaths, version: str) -> bool:
    """Register application in Windows 11 'Installed apps' / Control Panel Uninstall key and Start Menu."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CmdiqoAgent"
        access = winreg.KEY_ALL_ACCESS | getattr(winreg, "KEY_WOW64_64KEY", 0x0100)
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
            setup_exe = str(paths.setup_path)

            # Icon selection: prefer cmdiqo.ico, then setup executable
            ico_file = paths.install_dir / "cmdiqo.ico"
            if ico_file.exists():
                icon_str = str(ico_file)
            elif paths.setup_path.exists():
                icon_str = f"{paths.setup_path},0"
            else:
                icon_str = f"{paths.binary_path},0"

            # Compute estimated size in KB
            est_size_kb = 45000
            try:
                if paths.install_dir.exists():
                    total_bytes = sum(f.stat().st_size for f in paths.install_dir.glob("**/*") if f.is_file())
                    if total_bytes > 0:
                        est_size_kb = total_bytes // 1024
            except Exception:
                pass

            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Cmdiqo Remote Agent")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Cmdiqo")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(paths.install_dir))
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, icon_str)
            winreg.SetValueEx(
                key,
                "UninstallString",
                0,
                winreg.REG_SZ,
                f'"{setup_exe}" --uninstall --purge',
            )
            winreg.SetValueEx(
                key,
                "QuietUninstallString",
                0,
                winreg.REG_SZ,
                f'"{setup_exe}" --uninstall --quiet --purge',
            )
            winreg.SetValueEx(key, "ModifyPath", 0, winreg.REG_SZ, f'"{setup_exe}"')
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, est_size_kb)
            winreg.SetValueEx(key, "URLInfoAbout", 0, winreg.REG_SZ, "https://www.cmdiqo.dev/")
            winreg.SetValueEx(key, "HelpLink", 0, winreg.REG_SZ, "https://www.cmdiqo.dev/")
            winreg.SetValueEx(key, "InstallDate", 0, winreg.REG_SZ, time.strftime("%Y%m%d"))
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "Comments", 0, winreg.REG_SZ, "Cmdiqo LAN Remote Agent reverse WebSocket service")

        # Create Start Menu shortcut
        if paths.start_menu_file and paths.setup_path.exists():
            create_windows_shortcut(
                paths.setup_path,
                paths.start_menu_file,
                icon_path=ico_file if ico_file.exists() else None,
            )

        log.info("Registered Windows 'Installed apps' entry and Start Menu shortcut for CmdiqoAgent")
        return True
    except Exception as exc:
        log.warning("Could not register Windows Uninstall entry: %s", exc)
        return False


def remove_windows_app_entry(paths: Optional[PlatformPaths] = None) -> bool:
    """Remove application entry from Windows 11 'Installed apps' / Control Panel Uninstall key and Start Menu."""
    if not IS_WINDOWS:
        return False
    if paths is None:
        paths = get_platform_paths()

    # Remove Start Menu shortcut
    if paths.start_menu_file and paths.start_menu_file.exists():
        try:
            paths.start_menu_file.unlink(missing_ok=True)
            log.info("Removed Start Menu shortcut: %s", paths.start_menu_file)
        except Exception as exc:
            log.warning("Could not remove Start Menu shortcut: %s", exc)

    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CmdiqoAgent"
        for root_hkey in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for access_flag in (getattr(winreg, "KEY_WOW64_64KEY", 0x0100), getattr(winreg, "KEY_WOW64_32KEY", 0x0200)):
                try:
                    winreg.DeleteKeyEx(root_hkey, key_path, access_flag, 0)
                except (FileNotFoundError, OSError):
                    pass
                try:
                    winreg.DeleteKey(root_hkey, key_path)
                except (FileNotFoundError, OSError):
                    pass
        log.info("Removed Windows Uninstall entry for CmdiqoAgent from all registry hives")
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:
        log.warning("Could not delete Windows Uninstall registry entry: %s", exc)
        return False


def register_linux_desktop_entry(paths: PlatformPaths, version: str) -> bool:
    """Register desktop application entry on Linux systems."""
    if not IS_LINUX or not paths.desktop_file:
        return False
    try:
        content = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Cmdiqo Remote Agent\n"
            f"Comment=Cmdiqo LAN Remote Agent Service (v{version})\n"
            f"Exec={paths.binary_path} --version\n"
            "Icon=utilities-terminal\n"
            "Terminal=true\n"
            "Categories=System;Network;\n"
        )
        paths.desktop_file.parent.mkdir(parents=True, exist_ok=True)
        paths.desktop_file.write_text(content, encoding="utf-8")
        return True
    except Exception as exc:
        log.warning("Could not create Linux desktop entry: %s", exc)
        return False


def remove_linux_desktop_entry(paths: PlatformPaths) -> bool:
    """Remove desktop application entry on Linux."""
    if not IS_LINUX or not paths.desktop_file:
        return False
    try:
        paths.desktop_file.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def install_or_upgrade(
    config: dict,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Execute full install or upgrade workflow."""
    paths = get_platform_paths()

    def report(msg: str):
        if progress_callback:
            progress_callback(msg)
        log.info(msg)

    report("[*] Stopping existing service if running...")
    stop_service(paths)

    report("[*] Saving configuration...")
    save_config(config, paths)

    report("[*] Installing executable binary...")
    if not extract_and_install_binary(paths, progress_callback):
        report("[-] Error: Failed to extract or install binary.")
        return False

    report("[*] Registering system service...")
    if not register_service(paths, progress_callback):
        report("[-] Error: Failed to register service.")
        return False

    report("[*] Staging setup executable...")
    copy_setup_executable(paths, progress_callback)

    report("[*] Registering application in Windows 11 'Installed apps' / system desktop...")
    bundled_ver = get_bundled_version()
    if IS_WINDOWS:
        register_windows_app_entry(paths, bundled_ver)
    elif IS_LINUX:
        register_linux_desktop_entry(paths, bundled_ver)

    # Cache installed version
    global _cached_installed_version
    _cached_installed_version = bundled_ver

    report("[*] Starting service...")
    if start_service(paths):
        report("[+] Service successfully started and running!")
        return True
    else:
        report("[-] Service registered, but failed to start. Please check logs.")
        return False


def perform_uninstall(
    purge_config: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Completely uninstall cmdiqo-agent service and binaries."""
    paths = get_platform_paths()

    def report(msg: str):
        if progress_callback:
            progress_callback(msg)
        log.info(msg)

    report("[*] Stopping running service and terminating processes...")
    stop_service(paths)

    if IS_WINDOWS:
        report("[*] Removing entry from Windows 11 'Installed apps' and Start Menu...")
        remove_windows_app_entry(paths)

        report("[*] Deleting service from Service Control Manager...")
        run_cmd_silent(["sc.exe", "delete", paths.service_name], capture_output=True, check=False)
        time.sleep(1)

        # Check if the current running process is inside install_dir
        curr_exe = Path(sys.executable).resolve()
        is_running_from_install_dir = False
        try:
            is_running_from_install_dir = paths.install_dir.resolve() in curr_exe.parents
        except Exception:
            pass

        if paths.install_dir.exists():
            if is_running_from_install_dir:
                report(f"[*] Scheduling deferred directory cleanup of {paths.install_dir}...")
                for f in paths.install_dir.iterdir():
                    try:
                        if f.resolve() != curr_exe:
                            if f.is_dir():
                                shutil.rmtree(f, ignore_errors=True)
                            else:
                                f.unlink(missing_ok=True)
                    except Exception:
                        pass
                clean_cmd = (
                    f'cmd.exe /c timeout /t 2 /nobreak >nul & '
                    f'rmdir /s /q "{paths.install_dir}" & '
                    f'rmdir "{paths.install_dir.parent}" 2>nul'
                )
                try:
                    subprocess.Popen(
                        clean_cmd,
                        shell=True,
                        creationflags=0x00000008 | CREATE_NO_WINDOW,  # DETACHED_PROCESS | CREATE_NO_WINDOW
                    )
                except Exception:
                    pass
            else:
                report(f"[*] Removing installation directory {paths.install_dir}...")
                shutil.rmtree(paths.install_dir, ignore_errors=True)
                try:
                    if paths.install_dir.parent.exists() and not any(paths.install_dir.parent.iterdir()):
                        paths.install_dir.parent.rmdir()
                except Exception:
                    pass

        if purge_config and paths.config_dir.exists():
            report(f"[*] Purging configuration and logs in {paths.config_dir}...")
            shutil.rmtree(paths.config_dir, ignore_errors=True)
            try:
                if paths.config_dir.parent.exists() and not any(paths.config_dir.parent.iterdir()):
                    paths.config_dir.parent.rmdir()
            except Exception:
                pass

    elif IS_LINUX:
        report("[*] Stopping and disabling systemd service...")
        run_cmd_silent(["systemctl", "stop", paths.service_name], capture_output=True, check=False)
        run_cmd_silent(["systemctl", "disable", paths.service_name], capture_output=True, check=False)
        if paths.service_file and paths.service_file.exists():
            paths.service_file.unlink(missing_ok=True)
            run_cmd_silent(["systemctl", "daemon-reload"], capture_output=True, check=False)
            run_cmd_silent(["systemctl", "reset-failed"], capture_output=True, check=False)

        report("[*] Terminating any remaining agent processes...")
        run_cmd_silent(["pkill", "-9", "-f", "cmdiqo-agent"], capture_output=True, check=False)

        report("[*] Removing Linux desktop entry...")
        remove_linux_desktop_entry(paths)
        try:
            run_cmd_silent(["update-desktop-database", "/usr/share/applications"], capture_output=True, check=False)
        except Exception:
            pass

        if paths.binary_path.exists():
            report(f"[*] Removing binary at {paths.binary_path}...")
            paths.binary_path.unlink(missing_ok=True)

        if paths.setup_path and paths.setup_path.exists():
            report(f"[*] Removing setup executable at {paths.setup_path}...")
            paths.setup_path.unlink(missing_ok=True)
        if (paths.install_dir / "setup").exists():
            shutil.rmtree(paths.install_dir / "setup", ignore_errors=True)

        if purge_config and paths.config_dir.exists():
            report(f"[*] Purging configuration directory {paths.config_dir}...")
            shutil.rmtree(paths.config_dir, ignore_errors=True)

        if purge_config and paths.log_dir.exists():
            report(f"[*] Purging log directory {paths.log_dir}...")
            shutil.rmtree(paths.log_dir, ignore_errors=True)

    # Invalidate cached installed version
    global _cached_installed_version
    _cached_installed_version = None

    report("[+] cmdiqo-agent uninstallation complete.")
    return True
