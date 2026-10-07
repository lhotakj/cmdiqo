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

    agent_exe = dist_dir / "cmdiqo-agent.exe"
    agent_exe.write_bytes(b"windows binary content")

    summary = generate_summary(
        version="0.10.0",
        dist_dir=dist_dir,
        docker_user="lhotakj",
        commit_message="feat: awesome new features\nSecond line details",
    )

    assert "## Cmdiqo Release v0.10.0" in summary
    assert "Cmdiqo v0.10.0" in summary
    assert "feat: awesome new features" in summary
    assert "cmdiqo-agent" in summary
    assert "cmdiqo-agent.exe" in summary
    assert "docker pull lhotakj/cmdiqo:0.10.0" in summary
    assert "docker pull lhotakj/cmdiqo:latest" in summary
    assert "sha256sum" in summary


def test_generate_summary_without_artifacts(tmp_path: Path):
    empty_dist = tmp_path / "empty_dist"
    empty_dist.mkdir()

    summary = generate_summary(
        version="v1.2.3",
        dist_dir=empty_dist,
    )

    assert "## Cmdiqo Release v1.2.3" in summary
    assert "(No artifacts found in dist/)" in summary
