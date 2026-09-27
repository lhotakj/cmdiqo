"""Unit tests for Cmdiqo agent installer backend."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from installer.backend import (
    PlatformPaths,
    check_hub_connection,
    compare_versions,
    create_windows_shortcut,
    get_bundled_version,
    get_existing_config,
    get_installed_version,
    get_platform_paths,
    get_service_status,
    parse_version_tuple,
    perform_uninstall,
    register_linux_desktop_entry,
    register_windows_app_entry,
    remove_linux_desktop_entry,
    remove_windows_app_entry,
    save_config,
)


def test_parse_version_tuple():
    assert parse_version_tuple("0.9.0") == (0, 9, 0, 1, "")
    assert parse_version_tuple("v1.2.3") == (1, 2, 3, 1, "")
    assert parse_version_tuple("0.9.dev6A9C0B2D") == (0, 9, 0, 0, "dev6A9C0B2D")
    assert parse_version_tuple("2.0") == (2, 0, 0, 1, "")


def test_compare_versions():
    # ver_a < ver_b -> -1 (older -> upgrade needed)
    assert compare_versions("0.8.0", "0.9.0") == -1
    assert compare_versions("0.9.0", "1.0.0") == -1
    assert compare_versions("0.9.dev123", "0.9.0") == -1

    # ver_a == ver_b -> 0 (same version already installed)
    assert compare_versions("0.9.0", "0.9.0") == 0
    assert compare_versions("v0.9.0", "0.9.0") == 0
    assert compare_versions("1.2.3", "v1.2.3") == 0

    # ver_a > ver_b -> 1 (newer version already installed)
    assert compare_versions("1.0.0", "0.9.0") == 1
    assert compare_versions("0.9.1", "0.9.0") == 1
    assert compare_versions("0.9.0", "0.9.dev123") == 1


def test_get_platform_paths():
    paths = get_platform_paths()
    assert isinstance(paths, PlatformPaths)
    assert paths.install_dir is not None
    assert paths.binary_path is not None
    assert paths.config_dir is not None
    assert paths.config_file is not None
    assert paths.service_name in ("CmdiqoAgent", "cmdiqo-agent.service")
    expected_setup = (
        "cmdiqo-agent-windows-x64-setup.exe"
        if paths.service_name == "CmdiqoAgent"
        else "cmdiqo-agent-linux-x64-setup"
    )
    assert paths.setup_path.name == expected_setup


def test_get_service_status_not_installed(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path,
        binary_path=tmp_path / "missing-agent",
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )
    assert get_service_status(fake_paths) == "not_installed"


def test_get_service_status_windows_states(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path,
        binary_path=tmp_path / "cmdiqo-agent.exe",
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )
    with patch("installer.backend.IS_WINDOWS", True):
        with patch(
            "installer.backend.run_cmd_silent",
            return_value=MagicMock(returncode=0, stdout="STATE              : 4  RUNNING", stderr=""),
        ):
            assert get_service_status(fake_paths) == "running"
        with patch(
            "installer.backend.run_cmd_silent",
            return_value=MagicMock(returncode=0, stdout="STATE              : 1  STOPPED", stderr=""),
        ):
            assert get_service_status(fake_paths) == "stopped"
        with patch(
            "installer.backend.run_cmd_silent",
            return_value=MagicMock(returncode=1060, stdout="FAILED 1060", stderr=""),
        ):
            assert get_service_status(fake_paths) == "not_installed"


def test_get_bundled_version():
    ver = get_bundled_version()
    assert isinstance(ver, str)
    assert len(ver) > 0


def test_get_installed_version_nonexistent(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path,
        binary_path=tmp_path / "nonexistent-agent.exe",
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )
    assert get_installed_version(fake_paths, force_refresh=True) is None


def test_get_installed_version_fast_file(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path,
        binary_path=tmp_path / "cmdiqo-agent.exe",
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )
    fake_paths.binary_path.write_text("binary placeholder")
    (fake_paths.install_dir / "version.txt").write_text("1.5.0", encoding="utf-8")
    ver = get_installed_version(fake_paths, force_refresh=True)
    assert ver == "1.5.0"


def test_get_existing_config_defaults(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path,
        binary_path=tmp_path / "agent",
        config_dir=tmp_path,
        config_file=tmp_path / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )
    cfg = get_existing_config(fake_paths)
    assert "hub_url" in cfg
    assert "token" in cfg
    assert "name" in cfg
    assert "agent_connection_notify" in cfg


def test_save_and_load_config(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "bin",
        binary_path=tmp_path / "bin" / "cmdiqo-agent",
        config_dir=tmp_path / "etc",
        config_file=tmp_path / "etc" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )
    test_cfg = {
        "hub_url": "ws://10.0.0.5:8765",
        "token": "secret-token-123",
        "name": "MY-TEST-AGENT",
        "agent_connection_notify": True,
    }
    save_config(test_cfg, fake_paths)

    assert fake_paths.config_file.exists()
    loaded = get_existing_config(fake_paths)
    assert loaded["hub_url"] == "ws://10.0.0.5:8765"
    assert loaded["token"] == "secret-token-123"
    assert loaded["name"] == "MY-TEST-AGENT"
    assert loaded["agent_connection_notify"] is True


def test_check_hub_connection_invalid_url():
    ok, msg = check_hub_connection("invalid-url-without-ws")
    assert ok is False
    assert "Invalid WebSocket URL" in msg


def test_perform_uninstall_mock(tmp_path):
    fake_bin = tmp_path / "cmdiqo-agent"
    fake_bin.write_text("dummy binary")
    fake_paths = PlatformPaths(
        install_dir=tmp_path,
        binary_path=fake_bin,
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="TestAgent",
    )

    with patch("installer.backend.get_platform_paths", return_value=fake_paths):
        with patch("installer.backend.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            res = perform_uninstall(purge_config=False)
            assert res is True
            assert mock_run.called


def test_register_and_remove_windows_app_entry(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        setup_path=tmp_path / "install" / "cmdiqo-agent-windows-x64-setup.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
        start_menu_file=tmp_path / "shortcut.lnk",
    )
    fake_paths.install_dir.mkdir(parents=True, exist_ok=True)
    fake_paths.setup_path.write_text("dummy setup")
    fake_paths.start_menu_file.write_text("dummy shortcut")

    with patch("installer.backend.IS_WINDOWS", True):
        mock_winreg = MagicMock()
        mock_key = MagicMock()
        mock_winreg.CreateKeyEx.return_value.__enter__.return_value = mock_key
        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            with patch("installer.backend.create_windows_shortcut", return_value=True):
                reg_ok = register_windows_app_entry(fake_paths, "0.9.0")
                assert reg_ok is True
                assert mock_winreg.SetValueEx.called

            # Test unregistration
            rem_ok = remove_windows_app_entry(fake_paths)
            assert rem_ok is True
            assert mock_winreg.DeleteKeyEx.called or mock_winreg.DeleteKey.called
            assert not fake_paths.start_menu_file.exists()


def test_register_and_remove_linux_desktop_entry(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "bin",
        binary_path=tmp_path / "bin" / "cmdiqo-agent",
        config_dir=tmp_path / "etc",
        config_file=tmp_path / "etc" / "agent.env",
        log_dir=tmp_path / "logs",
        service_name="cmdiqo-agent.service",
        desktop_file=tmp_path / "cmdiqo-agent.desktop",
    )

    with patch("installer.backend.IS_LINUX", True):
        reg_ok = register_linux_desktop_entry(fake_paths, "0.9.0")
        assert reg_ok is True
        assert fake_paths.desktop_file.exists()
        content = fake_paths.desktop_file.read_text(encoding="utf-8")
        assert "Cmdiqo Remote Agent" in content

        rem_ok = remove_linux_desktop_entry(fake_paths)
        assert rem_ok is True
        assert not fake_paths.desktop_file.exists()


def test_gui_html_template_and_state_injection():
    from installer.gui import HTML_TEMPLATE, WebViewApi

    # 1. Verify Detecting... is completely absent from HTML template
    assert "Detecting..." not in HTML_TEMPLATE
    assert 'id="installed_badge" class="badge hidden"' in HTML_TEMPLATE

    # 2. Verify form inputs exist in the template
    assert 'id="hub_url"' in HTML_TEMPLATE
    assert 'id="token"' in HTML_TEMPLATE
    assert 'id="name"' in HTML_TEMPLATE
    assert 'id="notify"' in HTML_TEMPLATE
    assert 'id="install_dir"' in HTML_TEMPLATE
    assert 'id="btn_toggle_token"' in HTML_TEMPLATE

    # 3. Verify placeholder exists for state injection
    assert "__INITIAL_STATE_JSON__" in HTML_TEMPLATE

    # 4. Verify WebViewApi generates valid JSON-serializable state
    api = WebViewApi(None)
    state = api.get_state()
    assert "bundled_version" in state
    assert "installed_version" in state
    assert "service_status" in state
    assert "config" in state
    assert "paths" in state

    state_json = json.dumps(state)
    rendered = HTML_TEMPLATE.replace("__INITIAL_STATE_JSON__", state_json)
    assert "__INITIAL_STATE_JSON__" not in rendered
    assert state_json in rendered
