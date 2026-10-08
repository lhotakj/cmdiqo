"""Unit tests for development/generate_release_summary.py."""

from __future__ import annotations

import hashlib
from pathlib import Path

from development.generate_release_summary import (
    file_sha256,
    generate_summary,
    human_size,
)


def test_human_size():
    assert human_size(500) == "500 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1536) == "1.5 KB"
    assert human_size(1048576) == "1.0 MB"
    assert human_size(1073741824) == "1.0 GB"


def test_file_sha256(tmp_path: Path):
    test_file = tmp_path / "sample.bin"
    content = b"hello cmdiqo release"
    test_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()
    assert file_sha256(test_file) == expected_hash


def test_generate_summary_with_artifacts(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    agent_bin = dist_dir / "cmdiqo-agent"
    agent_bin.write_bytes(b"linux binary content")

    agent_arm64 = dist_dir / "cmdiqo-agent-linux-arm64"
    agent_arm64.write_bytes(b"linux arm64 binary content")

    agent_exe = dist_dir / "cmdiqo-agent.exe"
    agent_exe.write_bytes(b"windows binary content")

    arm64_zip = dist_dir / "cmdiqo-agent-linux-arm64.zip"
    arm64_zip.write_bytes(b"linux arm64 package zip")

    arm64_setup = dist_dir / "cmdiqo-agent-linux-arm64-setup"
    arm64_setup.write_bytes(b"linux arm64 cli setup binary")

    armv6_bin = dist_dir / "cmdiqo-agent-linux-armv6"
    armv6_bin.write_bytes(b"linux armv6 binary content")

    armv6_zip = dist_dir / "cmdiqo-agent-linux-armv6.zip"
    armv6_zip.write_bytes(b"linux armv6 package zip")

    armv6_setup = dist_dir / "cmdiqo-agent-linux-armv6-setup"
    armv6_setup.write_bytes(b"linux armv6 cli setup binary")

    summary = generate_summary(
        version="0.10.0",
        dist_dir=dist_dir,
        docker_user="lhotakj",
        commit_message="feat: awesome new features\nSecond line details",
        github_repo="lhotakj/cmdiqo",
    )

    assert "## Cmdiqo Release v0.10.0" in summary
    assert "Cmdiqo v0.10.0" in summary
    assert "feat: awesome new features" in summary
    assert "cmdiqo-agent" in summary
    assert "cmdiqo-agent-linux-arm64" in summary
    assert "cmdiqo-agent-linux-armv6" in summary
    assert "cmdiqo-agent.exe" in summary
    assert "cmdiqo-agent-linux-arm64.zip" in summary
    assert "cmdiqo-agent-linux-armv6.zip" in summary
    assert "cmdiqo-agent-linux-arm64-setup" in summary
    assert "cmdiqo-agent-linux-armv6-setup" in summary
    assert "ARM64" in summary
    assert "ARMv6" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-arm64" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-armv6" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent.exe" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-arm64.zip" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-armv6.zip" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-arm64-setup" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-armv6-setup" in summary
    assert "docker pull lhotakj/cmdiqo:0.10.0" in summary
    assert "docker pull lhotakj/cmdiqo:latest" in summary
    assert "sha256sum cmdiqo-agent-linux-x64.zip" in summary
    assert "sha256sum cmdiqo-agent-linux-arm64.zip" in summary
    assert "sha256sum cmdiqo-agent-linux-armv6.zip" in summary
    # Verify platform icons
    assert '<img src="https://raw.githubusercontent.com/lhotakj/cmdiqo/main/img/linux-bw.png" width="20" height="20" alt="Linux (Daemon)" align="middle">' in summary
    assert '<img src="https://raw.githubusercontent.com/lhotakj/cmdiqo/main/img/arm-bw.png" width="20" height="20" alt="Linux ARM64 (Daemon)" align="middle">' in summary
    assert '<img src="https://raw.githubusercontent.com/lhotakj/cmdiqo/main/img/arm-bw.png" width="20" height="20" alt="Linux ARMv6 (Daemon)" align="middle">' in summary
    assert '<img src="https://raw.githubusercontent.com/lhotakj/cmdiqo/main/img/win-bw.png" width="20" height="20" alt="Windows (Service)" align="middle">' in summary


def test_generate_summary_custom_repo(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "cmdiqo-agent-linux-arm64").write_bytes(b"arm64 binary")

    summary = generate_summary(
        version="v2.1.0",
        dist_dir=dist_dir,
        github_repo="custom-org/custom-repo",
    )

    assert "https://github.com/custom-org/custom-repo/releases/download/v2.1.0/cmdiqo-agent-linux-arm64" in summary
    assert '<img src="https://raw.githubusercontent.com/custom-org/custom-repo/main/img/arm-bw.png"' in summary


def test_generate_summary_custom_icons(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "cmdiqo-agent.exe").write_bytes(b"windows binary")

    summary = generate_summary(
        version="v1.0.0",
        dist_dir=dist_dir,
        icon_base_url="https://assets.cmdiqo.com/img/",
        icon_size=32,
    )

    assert '<img src="https://assets.cmdiqo.com/img/win-bw.png" width="32" height="32" alt="Windows (Service)" align="middle">' in summary


def test_ensure_icons_resized(tmp_path: Path):
    from PIL import Image
    from development.generate_release_summary import ensure_icons_resized

    img_dir = tmp_path / "img"
    img_dir.mkdir()
    for name in ("linux-bw.png", "arm-bw.png", "win-bw.png"):
        large_im = Image.new("RGBA", (64, 64), color=(255, 0, 0, 255))
        large_im.save(img_dir / name)

    ensure_icons_resized(img_dir, target_size=(32, 32))

    for name in ("linux-bw.png", "arm-bw.png", "win-bw.png"):
        with Image.open(img_dir / name) as im:
            assert im.size == (32, 32)


def test_generate_summary_without_artifacts(tmp_path: Path):
    empty_dist = tmp_path / "empty_dist"
    empty_dist.mkdir()

    summary = generate_summary(
        version="v1.2.3",
        dist_dir=empty_dist,
    )

    assert "## Cmdiqo Release v1.2.3" in summary
    assert "(No artifacts found in dist/)" in summary


