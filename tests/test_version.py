"""Unit tests for development/update_version.py version resolution and generation."""

import os
from unittest.mock import patch

from development.update_version import (
    generate_version_info_txt,
    get_base_version,
    update_versions,
)


def test_get_base_version():
    base = get_base_version()
    assert base == "1.0.0"


def test_update_versions_local_hex_timestamp(tmp_path):
    # Fixed timestamp: 1788611373 -> hex 6A9C0B2D
    ts = 1788611373
    with patch.dict(os.environ, {}, clear=True):
        version = update_versions(timestamp=ts)
        assert version == "1.0.dev6A9C0B2D"


def test_update_versions_github_actions():
    with patch.dict(os.environ, {"GITHUB_RUN_NUMBER": "123"}, clear=True):
        version = update_versions()
        assert version == "1.0.123"


def test_update_versions_override():
    version = update_versions(override_version="2.5.9")
    assert version == "2.5.9"


def test_generate_version_info_txt():
    content = generate_version_info_txt("1.0.dev6A9C0B2D", (1, 0, 2861, 0))
    assert "filevers=(1, 0, 2861, 0)" in content
    assert "StringStruct('FileVersion', '1.0.dev6A9C0B2D')" in content
    assert "StringStruct('ProductVersion', '1.0.dev6A9C0B2D')" in content
    assert "StringStruct('ProductName', 'Shango Agent')" in content
