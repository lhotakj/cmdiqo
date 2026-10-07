#!/usr/bin/env python3
"""Generate structured release summary for GitHub Releases and GitHub Actions Step Summary.

Outputs formatted Markdown with artifact table, SHA256 checksums, Docker image tags,
and commit highlights.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"


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
) -> str:
    """Generate Markdown release summary."""
    dist = dist_dir or DIST_DIR
    tag = tag or f"v{version.lstrip('vV')}"
    clean_version = version.lstrip("vV")

    artifact_specs = [
        ("cmdiqo-agent", "Linux (Daemon)", "x86_64", "Standalone service daemon executable"),
        ("cmdiqo-agent.exe", "Windows (Service)", "x86_64", "Standalone Windows service executable (signed)"),
        ("cmdiqo-agent-linux-x64.zip", "Linux (Package)", "x86_64", "Full daemon package + systemd installer"),
        ("cmdiqo-agent-windows-x64.zip", "Windows (Package)", "x86_64", "Full service package + PowerShell installer"),
        ("cmdiqo-agent-linux-x64-setup", "Linux (GUI Setup)", "x86_64", "Native C++ GTK3 interactive installer wizard"),
        ("cmdiqo-agent-windows-x64-setup.exe", "Windows (GUI Setup)", "x86_64", "Native C++ Win32 interactive installer wizard"),
        ("cmdiqo-agent.cer", "Windows (Certificate)", "All", "Authenticode digital signing public root certificate"),
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
    for filename, platform, arch, desc in artifact_specs:
        file_path = dist / filename
        if file_path.is_file():
            found_any = True
            size_str = human_size(file_path.stat().st_size)
            sha = file_sha256(file_path)
            lines.append(
                f"| `{filename}` | {platform} | {arch} | {size_str} | `{sha[:16]}...{sha[-8:]}` | {desc} |"
            )

    if not found_any:
        lines.append("| *(No artifacts found in dist/)* | - | - | - | - | - |")

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
        "sha256sum cmdiqo-agent-linux-x64.zip",
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
    parser.add_argument("--commit-message", default="", help="Commit message for highlights")
    parser.add_argument("-o", "--output", help="Output file path (e.g. dist/release-notes.md)")
    parser.add_argument("--summary-file", help="Path to GITHUB_STEP_SUMMARY file")

    args = parser.parse_args()
    summary = generate_summary(
        version=args.version,
        dist_dir=Path(args.dist),
        docker_user=args.docker_user,
        commit_message=args.commit_message,
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
