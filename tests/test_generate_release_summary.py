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
    assert "cmdiqo-agent.exe" in summary
    assert "cmdiqo-agent-linux-arm64.zip" in summary
    assert "cmdiqo-agent-linux-arm64-setup" in summary
    assert "ARM64" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-arm64" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent.exe" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-arm64.zip" in summary
    assert "https://github.com/lhotakj/cmdiqo/releases/download/v0.10.0/cmdiqo-agent-linux-arm64-setup" in summary
    assert "docker pull lhotakj/cmdiqo:0.10.0" in summary
    assert "docker pull lhotakj/cmdiqo:latest" in summary
    assert "sha256sum cmdiqo-agent-linux-x64.zip" in summary
    assert "sha256sum cmdiqo-agent-linux-arm64.zip" in summary


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


def test_generate_summary_without_artifacts(tmp_path: Path):
    empty_dist = tmp_path / "empty_dist"
    empty_dist.mkdir()

    summary = generate_summary(
        version="v1.2.3",
        dist_dir=empty_dist,
    )

    assert "## Cmdiqo Release v1.2.3" in summary
    assert "(No artifacts found in dist/)" in summary

