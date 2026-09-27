"""Unit tests for Cmdiqo agent installer backend."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch
import zipfile

import pytest

from installer.backend import (
    PlatformPaths,
    check_hub_connection,
    compare_versions,
    create_windows_shortcut,
    extract_and_install_binary,
    get_bundled_version,
    get_existing_config,
    get_installed_version,
    get_platform_paths,
    get_service_status,
    install_or_upgrade,
    parse_version_tuple,
    perform_uninstall,
    register_service,
    restart_service,
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


def test_stop_service_linux_does_not_match_setup_executable(tmp_path):
    from installer import backend

    paths = PlatformPaths(
        install_dir=tmp_path / "bin",
        binary_path=tmp_path / "bin" / "cmdiqo-agent",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "agent.env",
        log_dir=tmp_path / "logs",
        service_name="cmdiqo-agent.service",
    )
    completed = MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(backend, "IS_WINDOWS", False):
        with patch.object(backend, "IS_LINUX", True):
            with patch.object(backend, "run_cmd_silent", return_value=completed) as run_command:
                with patch.object(backend, "get_service_status", return_value="stopped"):
                    assert backend.stop_service(paths) is True

    assert run_command.call_args_list[0].args[0] == ["systemctl", "stop", "cmdiqo-agent.service"]
    assert run_command.call_args_list[1].args[0] == [
        "pkill",
        "-9",
        "-f",
        "--",
        rf"^{backend.re.escape(str(paths.binary_path))}(?:\s|$)",
    ]
    assert "cmdiqo-agent-linux-x64-setup" not in run_command.call_args_list[1].args[0][-1]


def test_find_bundled_zip_selects_linux_payload_when_both_platform_zips_exist(tmp_path):
    from installer import backend

    meipass = tmp_path / "payload"
    meipass.mkdir()
    windows_zip = meipass / "cmdiqo-agent-windows-x64.zip"
    linux_zip = meipass / "cmdiqo-agent-linux-x64.zip"
    windows_zip.touch()
    linux_zip.touch()

    with patch.object(backend, "IS_WINDOWS", False):
        with patch.object(backend.sys, "frozen", True, create=True):
            with patch.object(backend.sys, "_MEIPASS", str(meipass), create=True):
                assert backend.find_bundled_zip() == linux_zip


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


def test_windows_install_removes_legacy_self_signed_certificate_trust(tmp_path):
    payload = tmp_path / "cmdiqo-agent-windows-x64.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("cmdiqo-agent.exe", b"agent")
        archive.writestr("cmdiqo-agent.cer", b"certificate")

    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    commands: list[list[str]] = []

    def record_command(command, **_kwargs):
        commands.append(command)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("installer.backend.IS_WINDOWS", True):
        with patch("installer.backend.find_bundled_zip", return_value=payload):
            with patch("installer.backend.run_cmd_silent", side_effect=record_command):
                assert extract_and_install_binary(fake_paths) is True

    thumbprint = hashlib.sha1(b"certificate").hexdigest().upper()
    assert ["certutil.exe", "-delstore", "-user", "Root", thumbprint] in commands
    assert ["certutil.exe", "-delstore", "-user", "TrustedPublisher", thumbprint] in commands
    assert all("-addstore" not in command for command in commands)


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
                set_values = {
                    call.args[1]: call.args[4]
                    for call in mock_winreg.SetValueEx.call_args_list
                }
                assert set_values["UninstallString"] == (
                    f'"{fake_paths.setup_path}" --uninstall --purge'
                )
                assert set_values["QuietUninstallString"] == (
                    f'"{fake_paths.setup_path}" --uninstall --quiet --purge'
                )

            # Test unregistration
            rem_ok = remove_windows_app_entry(fake_paths)
            assert rem_ok is True
            assert mock_winreg.DeleteKeyEx.called or mock_winreg.DeleteKey.called
            assert not fake_paths.start_menu_file.exists()


def test_register_service_windows_reports_create_failure(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    reports: list[str] = []

    with patch("installer.backend.IS_WINDOWS", True):
        with patch(
            "installer.backend.run_cmd_silent",
            side_effect=[
                MagicMock(returncode=1060, stdout="FAILED 1060", stderr=""),
                MagicMock(returncode=5, stdout="", stderr="Access is denied."),
            ],
        ):
            assert register_service(fake_paths, reports.append) is False

    assert any("sc.exe create failed with exit code 5" in report for report in reports)
    assert any("Access is denied." in report for report in reports)


def test_register_service_windows_creates_service(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )

    with patch("installer.backend.IS_WINDOWS", True):
        with patch(
            "installer.backend.run_cmd_silent",
            side_effect=[
                MagicMock(returncode=1060, stdout="FAILED 1060", stderr=""),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
            ],
        ) as mock_run:
            assert register_service(fake_paths) is True

    assert mock_run.call_args_list[1].args[0] == [
        "sc.exe",
        "create",
        "CmdiqoAgent",
        "binPath=",
        f'"{fake_paths.binary_path}" --service --config "{fake_paths.config_file}"',
        "start=",
        "auto",
        "DisplayName=",
        "Cmdiqo LAN Remote Agent",
    ]
    assert mock_run.call_args_list[3].args[0] == [
        "sc.exe",
        "failure",
        "CmdiqoAgent",
        "reset=",
        "86400",
        "actions=",
        "restart/5000/restart/10000/restart/30000",
    ]


def test_register_service_windows_updates_service(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )

    with patch("installer.backend.IS_WINDOWS", True):
        with patch(
            "installer.backend.run_cmd_silent",
            side_effect=[
                MagicMock(returncode=0, stdout="SERVICE_NAME: CmdiqoAgent", stderr=""),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
                MagicMock(returncode=0, stdout="SUCCESS", stderr=""),
            ],
        ) as mock_run:
            assert register_service(fake_paths) is True

    assert mock_run.call_args_list[1].args[0] == [
        "sc.exe",
        "config",
        "CmdiqoAgent",
        "binPath=",
        f'"{fake_paths.binary_path}" --service --config "{fake_paths.config_file}"',
        "start=",
        "auto",
        "DisplayName=",
        "Cmdiqo LAN Remote Agent",
    ]


def test_install_or_upgrade_fails_when_service_does_not_start(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    reports: list[str] = []

    with patch("installer.backend.get_platform_paths", return_value=fake_paths):
        with patch("installer.backend.stop_service"):
            with patch("installer.backend.save_config"):
                with patch("installer.backend.extract_and_install_binary", return_value=True):
                    with patch("installer.backend.register_service", return_value=True):
                        with patch("installer.backend.copy_setup_executable"):
                            with patch("installer.backend.register_windows_app_entry"):
                                with patch("installer.backend.start_service", return_value=False):
                                    assert (
                                        install_or_upgrade(
                                            {"hub_url": "ws://hub", "token": "secret"},
                                            reports.append,
                                        )
                                        is False
                                    )

    assert any("failed to start" in report for report in reports)


def test_restart_service_stops_running_windows_service(tmp_path):
    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )

    with patch("installer.backend.IS_WINDOWS", True):
        with patch("installer.backend.get_service_status", return_value="running"):
            with patch("installer.backend.stop_service", return_value=True) as mock_stop:
                with patch("installer.backend.start_service", return_value=True) as mock_start:
                    assert restart_service(fake_paths) is True

    mock_stop.assert_called_once_with(fake_paths)
    mock_start.assert_called_once_with(fake_paths)


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
    from installer.gui import HTML_TEMPLATE, WebViewApi, get_brand_icon_data_uri

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
    assert "setTimeout(() => syncWithBackend(true), 250)" in HTML_TEMPLATE
    assert "btn_start_service" in HTML_TEMPLATE
    assert "btn_restart_service" in HTML_TEMPLATE
    assert "btn_stop_service" in HTML_TEMPLATE
    assert ".service-status" in HTML_TEMPLATE
    assert "background: var(--card-bg);" in HTML_TEMPLATE
    assert 'id="view_loading"' in HTML_TEMPLATE
    assert 'id="loading_message"' in HTML_TEMPLATE
    assert 'id="loading_detail"' in HTML_TEMPLATE
    assert "@keyframes spin" in HTML_TEMPLATE
    assert "justify-content: center;" in HTML_TEMPLATE
    assert "setTimeout(syncWithBackend, 100)" in HTML_TEMPLATE
    assert "Starting the Windows browser bridge..." in HTML_TEMPLATE
    assert "startupStatusMessages" in HTML_TEMPLATE
    assert "function pollServiceStatus()" in HTML_TEMPLATE
    assert "setTimeout(pollServiceStatus, 1000)" in HTML_TEMPLATE
    assert "renderServiceStatus(status)" in HTML_TEMPLATE
    assert "mark_ui_ready" in HTML_TEMPLATE
    assert 'class="banner-heading"' in HTML_TEMPLATE
    assert ".primary-actions, .service-controls-actions" in HTML_TEMPLATE
    assert "function manageService(action)" in HTML_TEMPLATE
    assert "__BRAND_ICON_DATA_URI__" in HTML_TEMPLATE
    assert 'class="service-controls-title">Service controls' in HTML_TEMPLATE
    assert 'id="view_service_controls"' in HTML_TEMPLATE
    assert "viewId === 'view_same'" in HTML_TEMPLATE
    assert "const hasConfiguration" in HTML_TEMPLATE
    assert 'id="btn_cancel" onclick="returnToMainScreen()">Back</button>' in HTML_TEMPLATE
    assert get_brand_icon_data_uri().startswith("data:image/x-icon;base64,")

    # Non-destructive loader give-up: the page must stop polling and show an error instead
    # of destroying the WebView window (which previously crashed the frozen installer).
    assert "startupStartedAt > 25000" in HTML_TEMPLATE
    assert 'id="loading_spinner"' in HTML_TEMPLATE
    assert "The installer could not start its embedded browser." in HTML_TEMPLATE

    # 3. Verify placeholder exists for optional state injection.
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


def test_webview_api_defers_state_checks_until_requested(tmp_path):
    from installer.gui import WebViewApi

    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    with patch("installer.gui.get_platform_paths", return_value=fake_paths):
        with patch("installer.gui.get_bundled_version", return_value="0.9.0"):
            with patch("installer.gui.get_installed_version", return_value="0.9.0") as installed:
                with patch("installer.gui.get_existing_config", return_value={}) as config:
                    with patch("installer.gui.get_service_status", return_value="running") as status:
                        api = WebViewApi(None)

                        installed.assert_not_called()
                        config.assert_not_called()
                        status.assert_not_called()

                        state = api.get_state()

    assert state["installed_version"] == "0.9.0"
    assert state["service_status"] == "running"


def test_webview_api_returns_live_service_status(tmp_path):
    from installer.gui import WebViewApi

    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    with patch("installer.gui.get_platform_paths", return_value=fake_paths):
        with patch("installer.gui.get_bundled_version", return_value="0.9.0"):
            with patch("installer.gui.get_service_status", return_value="stopped") as status:
                api = WebViewApi(None)
                assert api.get_current_service_status() == "stopped"

    status.assert_called_once_with(fake_paths)


def test_webview_api_keeps_native_window_reference_private(tmp_path):
    """pywebview introspects public js_api attributes; a public window reference would make
    it recursively walk window.native.browser.webview... on the WebView2 backend, flooding
    the log with recursion/COM errors. The reference must stay underscore-prefixed."""
    from installer.gui import WebViewApi

    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    sentinel = object()
    with patch("installer.gui.get_platform_paths", return_value=fake_paths):
        with patch("installer.gui.get_bundled_version", return_value="0.9.0"):
            api = WebViewApi(sentinel)

    assert api._window is sentinel
    public_attrs = [name for name in vars(api) if not name.startswith("_")]
    assert "window" not in public_attrs


def test_webview_api_tracks_initial_render_readiness(tmp_path):
    from installer.gui import WebViewApi

    fake_paths = PlatformPaths(
        install_dir=tmp_path / "install",
        binary_path=tmp_path / "install" / "cmdiqo-agent.exe",
        config_dir=tmp_path / "config",
        config_file=tmp_path / "config" / "config.json",
        log_dir=tmp_path / "logs",
        service_name="CmdiqoAgent",
    )
    with patch("installer.gui.get_platform_paths", return_value=fake_paths):
        with patch("installer.gui.get_bundled_version", return_value="0.9.0"):
            api = WebViewApi(None)
            assert api.ui_ready.is_set() is False
            api.mark_ui_ready()
            assert api.ui_ready.is_set() is True


def test_run_webview_gui_returns_false_quietly_when_module_missing():
    """A missing pywebview module must use the normal GUI fallback without an error."""
    import sys as _sys
    from installer import gui

    with patch.dict(_sys.modules, {"webview": None}):
        with patch.object(gui.log, "error") as log_error:
            with patch.object(gui.log, "info") as log_info:
                result = gui.run_webview_gui()

    assert result is False
    log_error.assert_not_called()
    assert any("pywebview is not available" in str(call.args) for call in log_info.call_args_list)


def test_linux_webview_runtime_is_skipped_when_webkit2gtk_is_unavailable():
    import sys as _sys
    from installer import gui

    with patch.dict(_sys.modules, {"gi": None}):
        with patch.object(gui, "linux_webview_install_hint", return_value="install-webkit") as hint:
            with patch.object(gui.log, "warning") as log_warning:
                assert gui.linux_webview_runtime_available() is False

    hint.assert_called_once()
    log_warning.assert_called_once_with(
        "WebKit2GTK is not available; skipping the WebView UI. Error: %s. "
        "GI typelib search paths: %s. Install it with: %s",
        ANY,
        [],
        "install-webkit",
    )


def test_configure_linux_typelib_search_paths_prepends_system_directories():
    from installer import gui

    repository = MagicMock()
    repository.get_search_path.return_value = ["/bundled/typelibs"]

    with patch.object(gui.Path, "is_dir", return_value=True):
        search_paths = gui.configure_linux_typelib_search_paths(repository)

    assert search_paths == ["/bundled/typelibs"]
    assert repository.prepend_search_path.call_args_list == [
        call(str(gui.Path("/usr/lib64/girepository-1.0"))),
        call(str(gui.Path("/usr/lib/girepository-1.0"))),
    ]


def test_linux_webview_runtime_uses_girepository_api():
    import sys as _sys
    from installer import gui

    repository = MagicMock()
    gi_repository = ModuleType("gi.repository")
    gi_repository.GIRepository = SimpleNamespace(
        Repository=SimpleNamespace(get_default=MagicMock(return_value=repository))
    )
    gi_repository.WebKit2 = object()
    gi = MagicMock()

    with patch.dict(_sys.modules, {"gi": gi, "gi.repository": gi_repository}):
        with patch.object(gui, "configure_linux_typelib_search_paths", return_value=["/usr/lib64/girepository-1.0"]):
            assert gui.linux_webview_runtime_available() is True

    gi_repository.GIRepository.Repository.get_default.assert_called_once()
    assert gi.require_version.call_args_list == [
        call("GIRepository", "3.0"),
        call("WebKit2", "4.1"),
    ]


def test_linux_webview_runtime_falls_back_to_girepository_2_when_needed():
    import sys as _sys
    from installer import gui

    repository = MagicMock()
    gi_repository = ModuleType("gi.repository")
    gi_repository.GIRepository = SimpleNamespace(
        Repository=SimpleNamespace(get_default=MagicMock(return_value=repository))
    )
    gi_repository.WebKit2 = object()
    gi = MagicMock()
    gi.require_version.side_effect = [ValueError("GIRepository 3.0 unavailable"), None, None]

    with patch.dict(_sys.modules, {"gi": gi, "gi.repository": gi_repository}):
        with patch.object(gui, "configure_linux_typelib_search_paths", return_value=[]):
            assert gui.linux_webview_runtime_available() is True

    assert gi.require_version.call_args_list == [
        call("GIRepository", "3.0"),
        call("GIRepository", "2.0"),
        call("WebKit2", "4.1"),
    ]


def test_run_webview_gui_skips_webview_when_linux_runtime_is_unavailable():
    import sys as _sys
    from installer import gui

    webview = MagicMock()
    with patch.object(gui, "IS_WINDOWS", False):
        with patch.object(gui, "linux_webview_runtime_available", return_value=False):
            with patch.dict(_sys.modules, {"webview": webview}):
                assert gui.run_webview_gui() is False

    webview.create_window.assert_not_called()


@pytest.mark.parametrize(
    ("os_release", "expected"),
    [
        ('ID="opensuse-tumbleweed"\nID_LIKE="suse opensuse"\n', "sudo zypper install typelib-1_0-WebKit2-4_1"),
        ('ID=ubuntu\nVERSION_ID="22.04"\n', "sudo apt install gir1.2-webkit2-4.0"),
        ('ID=fedora\nVERSION_ID="40"\n', "sudo dnf install webkit2gtk4.1"),
        ('ID=rocky\nID_LIKE="rhel fedora"\n', "sudo dnf install webkit2gtk3"),
    ],
)
def test_linux_webview_install_hint_uses_distribution_package(os_release, expected):
    from installer import gui

    with patch.object(gui.Path, "read_text", return_value=os_release):
        assert gui.linux_webview_install_hint() == expected


def test_linux_gui_environment_guidance_preserves_desktop_variables():
    from installer import gui

    guidance = gui.linux_gui_environment_guidance()

    assert "without sudo" in guidance
    assert 'DISPLAY="$DISPLAY"' in guidance
    assert 'XAUTHORITY="$XAUTHORITY"' in guidance
    assert 'WAYLAND_DISPLAY="$WAYLAND_DISPLAY"' in guidance
    assert 'XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"' in guidance
    assert 'DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"' in guidance


def test_main_uses_native_error_instead_of_cli_when_windows_webview_fails():
    from installer import gui

    with patch.object(gui, "is_admin", return_value=True):
        with patch.object(gui, "IS_WINDOWS", True):
            with patch.object(gui, "HAS_TKINTER", False):
                with patch.object(gui, "has_interactive_stdin", return_value=False):
                    with patch.object(gui, "run_webview_gui", return_value=False):
                        with patch.object(gui, "show_gui_startup_failure") as show_failure:
                            with patch.object(gui, "run_cli_installer") as run_cli:
                                with patch.object(gui.sys, "argv", ["gui.py"]):
                                    gui.main()

    show_failure.assert_called_once()
    run_cli.assert_not_called()


def test_main_does_not_run_requested_cli_without_interactive_stdin():
    from installer import gui

    with patch.object(gui, "is_admin", return_value=True):
        with patch.object(gui, "has_interactive_stdin", return_value=False):
            with patch.object(gui, "show_gui_startup_failure") as show_failure:
                with patch.object(gui.sys, "argv", ["gui.py", "--cli"]):
                    with pytest.raises(SystemExit, match="1"):
                        gui.main()

    show_failure.assert_called_once()


def test_request_elevation_requests_sudo_from_interactive_linux_terminal():
    from installer import backend

    with patch.object(backend, "IS_WINDOWS", False):
        with patch.object(backend, "is_admin", return_value=False):
            with patch.object(backend.sys.stdin, "isatty", return_value=True):
                with patch.dict(backend.os.environ, {}, clear=True):
                    with patch.object(backend.shutil, "which", return_value="/usr/bin/sudo"):
                        with patch.object(backend.sys, "argv", ["cmdiqo-agent-linux-x64-setup"]):
                            with patch.object(backend.os, "execvp", side_effect=OSError("failed")) as execvp:
                                assert backend.request_elevation() is False

    execvp.assert_called_once_with(
        "/usr/bin/sudo",
        [
            "sudo",
            "--",
            backend.sys.executable,
            "cmdiqo-agent-linux-x64-setup",
        ],
    )


def test_request_elevation_uses_pkexec_from_linux_desktop():
    from installer import backend

    with patch.object(backend, "IS_WINDOWS", False):
        with patch.object(backend, "is_admin", return_value=False):
            with patch.object(backend.sys.stdin, "isatty", return_value=False):
                with patch.dict(backend.os.environ, {"DISPLAY": ":0"}, clear=True):
                    with patch.object(backend.shutil, "which", return_value="/usr/bin/pkexec"):
                        with patch.object(backend.sys, "argv", ["cmdiqo-agent-linux-x64-setup"]):
                            with patch.object(backend.subprocess, "Popen") as popen:
                                assert backend.request_elevation() is True

    popen.assert_called_once_with(
        [
            "/usr/bin/pkexec",
            backend.sys.executable,
            "cmdiqo-agent-linux-x64-setup",
        ]
    )


def test_request_elevation_prefers_pkexec_over_sudo_in_gui_session():
    from installer import backend

    with patch.object(backend, "IS_WINDOWS", False):
        with patch.object(backend, "is_admin", return_value=False):
            with patch.object(backend.sys.stdin, "isatty", return_value=True):
                with patch.dict(backend.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
                    with patch.object(backend.shutil, "which", return_value="/usr/bin/pkexec"):
                        with patch.object(backend.sys, "argv", ["cmdiqo-agent-linux-x64-setup"]):
                            with patch.object(backend.subprocess, "Popen") as popen:
                                with patch.object(backend.os, "execvp") as execvp:
                                    assert backend.request_elevation() is True

    popen.assert_called_once_with(
        [
            "/usr/bin/pkexec",
            backend.sys.executable,
            "cmdiqo-agent-linux-x64-setup",
        ]
    )
    execvp.assert_not_called()
