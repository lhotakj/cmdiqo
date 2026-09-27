"""Unit tests for development/update_version.py version resolution and generation."""

import os
import re
from unittest.mock import patch

from development.update_version import (
    AGENT_VERSION_INFO,
    generate_version_info_txt,
    get_base_version,
    update_versions,
)


def test_get_base_version():
    base = get_base_version()
    assert re.match(r"^\d+\.\d+(?:\.\d+)?", base)


def test_update_versions_local_hex_timestamp(tmp_path):
    # Fixed timestamp: 1788611373 -> hex 6A9C0B2D
    ts = 1788611373
    base = get_base_version()
    short = ".".join(base.split(".")[:2])
    with patch.dict(os.environ, {}, clear=True):
        version = update_versions(timestamp=ts)
        assert version == f"{short}.dev6A9C0B2D"


def test_update_versions_github_actions():
    base = get_base_version()
    short = ".".join(base.split(".")[:2])
    with patch.dict(os.environ, {"GITHUB_RUN_NUMBER": "123"}, clear=True):
        version = update_versions()
        assert version == f"{short}.123"


def test_update_versions_github_release_tag():
    with patch.dict(
        os.environ,
        {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.1.0"},
        clear=True,
    ):
        version = update_versions()
        assert version == "1.1.0"


def test_update_versions_override():
    version = update_versions(override_version="2.5.9")
    assert version == "2.5.9"


def test_update_versions_local_override_preserves_hex_timestamp():
    version = update_versions(override_version="2.5.dev6A9C0B2D")
    assert version == "2.5.dev6A9C0B2D"
    assert "filevers=(2, 5, 2861, 0)" in AGENT_VERSION_INFO.read_text(encoding="utf-8")


def test_generate_version_info_txt():
    content = generate_version_info_txt("1.0.dev6A9C0B2D", (1, 0, 2861, 0))
    assert "filevers=(1, 0, 2861, 0)" in content
    assert "StringStruct('FileVersion', '1.0.dev6A9C0B2D')" in content
    assert "StringStruct('ProductVersion', '1.0.dev6A9C0B2D')" in content
    assert "StringStruct('ProductName', 'Cmdiqo Agent')" in content


def test_bundled_version_modules_without_version_txt():
    """Verify bot, agent, and slacksim resolve version from bundled python modules without version.txt."""
    import agent.agent
    import bot.app
    import slacksim

    assert bot.app.get_version() != ""
    assert agent.agent.get_version() != ""
    assert slacksim.__version__ != ""

    assert bot.app.get_version() == bot.app.__version__
    assert agent.agent.get_version() == agent.agent.__version__
    assert slacksim.__version__ == slacksim._version.__version__
