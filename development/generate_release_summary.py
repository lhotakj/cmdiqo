#!/usr/bin/env python3
"""Generate structured release summary for GitHub Releases and GitHub Actions Step Summary.

Outputs formatted Markdown with artifact table, SHA256 checksums, Docker image tags,
and commit highlights.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
IMG_DIR = ROOT / "img"


def ensure_icons_resized(img_dir: Path | None = None, target_size: tuple[int, int] = (32, 32)) -> None:
    """Ensure source platform icons in img/ are resized to target_size (e.g. 32x32)."""
    target = img_dir or IMG_DIR
    if not target.is_dir():
        return
    try:
        from PIL import Image
    except ImportError:
        return

    for icon_name in ("linux-bw.png", "arm-bw.png", "win-bw.png"):
        icon_path = target / icon_name
        if icon_path.is_file():
            try:
                with Image.open(icon_path) as im:
                    if im.size != target_size:
                        resized = im.convert("RGBA").resize(target_size, Image.Resampling.LANCZOS)
                        resized.save(icon_path)
            except (OSError, ValueError) as exc:
                logger.debug("Failed to resize icon %s: %s", icon_path, exc)


def human_size(num_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024.0 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} GB"


def file_sha256(path: Path) -> str:
    """Compute SHA256 checksum for a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_summary(
    version: str,
    dist_dir: Path | None = None,
    docker_user: str = "lhotakj",
    commit_message: str = "",
    tag: str | None = None,
    github_repo: str | None = None,
    icon_base_url: str | None = None,
    icon_size: int = 20,
) -> str:
    """Generate Markdown release summary."""
    ensure_icons_resized()
    dist = dist_dir or DIST_DIR
    tag = tag or f"v{version.lstrip('vV')}"
    clean_version = version.lstrip("vV")
    repo = github_repo or os.environ.get("GITHUB_REPOSITORY", "lhotakj/cmdiqo")
    icons_url = (icon_base_url or f"https://raw.githubusercontent.com/{repo}/main/img").rstrip("/")

    artifact_specs = [
        ("cmdiqo-agent", "linux-bw.png", "Linux (Daemon)", "x86_64", "Standalone service daemon executable"),
        ("cmdiqo-agent-linux-arm64", "arm-bw.png", "Linux ARM64 (Daemon)", "ARM64", "Standalone service daemon executable (aarch64)"),
        ("cmdiqo-agent-linux-armv6", "arm-bw.png", "Linux ARMv6 (Daemon)", "ARMv6", "Standalone service daemon executable (armv6l / RPi Zero W / Pi 1)"),
        ("cmdiqo-agent.exe", "win-bw.png", "Windows (Service)", "x86_64", "Standalone Windows service executable (signed)"),
        ("cmdiqo-agent-linux-x64.zip", "linux-bw.png", "Linux (Package)", "x86_64", "Full daemon package + systemd installer"),
        ("cmdiqo-agent-linux-arm64.zip", "arm-bw.png", "Linux ARM64 (Package)", "ARM64", "Full daemon package + systemd installer (aarch64)"),
        ("cmdiqo-agent-linux-armv6.zip", "arm-bw.png", "Linux ARMv6 (Package)", "ARMv6", "Full daemon package + systemd installer (armv6l / RPi Zero W / Pi 1)"),
        ("cmdiqo-agent-windows-x64.zip", "win-bw.png", "Windows (Package)", "x86_64", "Full service package + PowerShell installer"),
        ("cmdiqo-agent-linux-x64-setup", "linux-bw.png", "Linux (GUI Setup)", "x86_64", "Native C++ GTK3 interactive installer wizard"),
        ("cmdiqo-agent-linux-x64-setup-cli", "linux-bw.png", "Linux (CLI Setup)", "x86_64", "Native C++ headless CLI installer wizard (x86_64)"),
        ("cmdiqo-agent-linux-arm64-setup", "arm-bw.png", "Linux ARM64 (CLI Setup)", "ARM64", "Native C++ headless CLI installer wizard (aarch64)"),
        ("cmdiqo-agent-linux-armv6-setup", "arm-bw.png", "Linux ARMv6 (CLI Setup)", "ARMv6", "Native C++ headless CLI installer wizard (armv6l / RPi Zero W / Pi 1)"),
        ("cmdiqo-agent-windows-x64-setup.exe", "win-bw.png", "Windows (GUI Setup)", "x86_64", "Native C++ Win32 interactive installer wizard"),
        ("cmdiqo-agent.cer", "win-bw.png", "Windows (Certificate)", "All", "Authenticode digital signing public root certificate"),
    ]

    lines: list[str] = [
        f"## Cmdiqo Release {tag}",
        "",
        f"Automated release build for **Cmdiqo v{clean_version}**.",
        "",
    ]

    if commit_message:
        # First line of commit message as highlight
        headline = commit_message.strip().split("\n")[0]
        lines.extend([
            f"> **Release Note:** {headline}",
            "",
        ])

    lines.extend([
        "### 📦 Native Agent & Installer Packages",
        "",
        "| Artifact | Target Platform | Arch | Size | SHA256 Checksum | Description |",
        "| :--- | :--- | :--- | :---: | :--- | :--- |",
    ])

    found_any = False
    for filename, icon_file, platform, arch, desc in artifact_specs:
        file_path = dist / filename
        if file_path.is_file():
            found_any = True
            size_str = human_size(file_path.stat().st_size)
            sha = file_sha256(file_path)
            download_url = f"https://github.com/{repo}/releases/download/{tag}/{filename}"
            icon_tag = f'<img src="{icons_url}/{icon_file}" width="{icon_size}" height="{icon_size}" alt="{platform}" align="middle"> '
            lines.append(
                f"| [`{filename}`]({download_url}) | {icon_tag}{platform} | {arch} | {size_str} | `{sha[:16]}...{sha[-8:]}` | {desc} |"
            )

    if not found_any:
        lines.append("| *(No artifacts found in dist/)* | - | - | - | - | - |")

    lines.extend([
        "",
        "### ⚡ Quick One-Liner Installation (Piped)",
        "",
        "Deploy and register Cmdiqo Agent directly from your terminal using a single piped command:",
        "",
        "#### Linux (Ubuntu / Debian / Fedora / RHEL / OCI / AWS / Raspberry Pi):",
        "```bash",
        "# Auto-detects architecture (x86_64, aarch64, armv6l) and installs systemd service:",
        f"curl -fsSL https://raw.githubusercontent.com/{repo}/main/install.sh | sudo bash -s -- --hub-url \"ws://<HUB_IP>:8765\" --token \"<AGENT_TOKEN>\" --name \"<HOST_NAME>\"",
        "```",
        "",
        "#### Windows 10 / 11 / Server (PowerShell as Administrator):",
        "```powershell",
        "# Headless service installation:",
        f'& ([scriptblock]::Create((irm https://raw.githubusercontent.com/{repo}/main/install.ps1))) -HubUrl "ws://<HUB_IP>:8765" -Token "<AGENT_TOKEN>" -Name "<HOST_NAME>"',
        "",
        "# Or run interactively to launch the GUI setup wizard:",
        f"irm https://raw.githubusercontent.com/{repo}/main/install.ps1 | iex",
        "```",
    ])

    lines.extend([
        "",
        "### 🐳 Docker Hub Containers",
        "",
        "The Cmdiqo Hub (Slack bot) container is published with multi-architecture support (`linux/amd64`, `linux/arm64`):",
        "",
        "```bash",
        "# Pull version tag",
        f"docker pull {docker_user}/cmdiqo:{clean_version}",
        "",
        "# Pull latest stable",
        f"docker pull {docker_user}/cmdiqo:latest",
        "```",
        "",
        "### 🔐 Checksum Verification",
        "",
        "To verify integrity of any downloaded package on Linux or macOS:",
        "```bash",
        "# Linux x86_64",
        "sha256sum cmdiqo-agent-linux-x64.zip",
        "",
        "# Linux ARM64",
        "sha256sum cmdiqo-agent-linux-arm64.zip",
        "",
        "# Linux ARMv6 (RPi Zero W / Pi 1)",
        "sha256sum cmdiqo-agent-linux-armv6.zip",
        "```",
        "On Windows PowerShell:",
        "```powershell",
        "Get-FileHash .\\cmdiqo-agent-windows-x64.zip -Algorithm SHA256",
        "```",
        "",
        "---",
        "*(Built automatically via unified build pipeline using `./build.sh` on Linux runner)*",
    ])

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate release summary markdown.")
    parser.add_argument("-v", "--version", required=True, help="Release version (e.g. 0.10.0)")
    parser.add_argument("--dist", default="dist", help="Path to dist directory")
    parser.add_argument("--docker-user", default="lhotakj", help="Docker Hub organization/user")
    parser.add_argument(
        "--repo",
        "--github-repo",
        dest="github_repo",
        default=os.environ.get("GITHUB_REPOSITORY", "lhotakj/cmdiqo"),
        help="GitHub repository (owner/repo) for release download links",
    )
    parser.add_argument(
        "--icon-base-url",
        help="Base URL for platform icons (defaults to raw.githubusercontent.com/{repo}/main/img)",
    )
    parser.add_argument(
        "--icon-size",
        type=int,
        default=20,
        help="Display size (width and height in px) for platform icons (default: 20)",
    )
    parser.add_argument("--commit-message", default="", help="Commit message for highlights")
    parser.add_argument("-o", "--output", help="Output file path (e.g. dist/release-notes.md)")
    parser.add_argument("--summary-file", help="Path to GITHUB_STEP_SUMMARY file")

    args = parser.parse_args()
    summary = generate_summary(
        version=args.version,
        dist_dir=Path(args.dist),
        docker_user=args.docker_user,
        commit_message=args.commit_message,
        github_repo=args.github_repo,
        icon_base_url=args.icon_base_url,
        icon_size=args.icon_size,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(summary, encoding="utf-8")
        print(f"[INFO] Release summary written to: {out_path}")

    step_summary_path = args.summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        with open(step_summary_path, "a", encoding="utf-8") as f:
            f.write(summary + "\n")
        print(f"[INFO] Release summary appended to step summary: {step_summary_path}")

    if not args.output and not step_summary_path:
        print(summary)


if __name__ == "__main__":
    main()
