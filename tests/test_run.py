"""Unit tests for root run.py process runner and supervisor."""

import os
from unittest.mock import MagicMock, patch

import pytest

import run


def test_load_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Test comment\nTEST_RUN_FOO=bar\nTEST_RUN_BAZ='qux'\nINVALID_LINE\n\n",
        encoding="utf-8",
    )
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TEST_RUN_FOO", None)
        os.environ.pop("TEST_RUN_BAZ", None)
        run.load_dotenv(env_file)
        assert os.environ.get("TEST_RUN_FOO") == "bar"
        assert os.environ.get("TEST_RUN_BAZ") == "qux"


def test_print_usage(capsys):
    run.print_usage()
    out = capsys.readouterr().out
    assert "Cmdiqo Process Runner" in out
    assert "python run.py hub" in out
    assert "python run.py agent" in out


def test_terminate_process_safely_already_dead():
    proc = MagicMock()
    proc.poll.return_value = 0
    run.terminate_process_safely(proc, "TestProc")
    proc.terminate.assert_not_called()


def test_terminate_process_safely_clean_exit():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 9999
    proc.wait.return_value = 0
    run.terminate_process_safely(proc, "TestProc")
    proc.terminate.assert_called_once()


def test_main_help(capsys):
    with patch("sys.argv", ["run.py", "--help"]), pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Cmdiqo Process Runner" in out


def test_main_unknown_command(capsys):
    with patch("sys.argv", ["run.py", "invalid-cmd"]), pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code in (1, 2)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "invalid-cmd" in combined


def test_main_diag(capsys):
    with patch("sys.argv", ["run.py", "diag"]), pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Cmdiqo Diagnostics" in out


@patch("run.run_single")
def test_main_hub(mock_run_single):
    mock_run_single.return_value = 0
    with (
        patch("sys.argv", ["run.py", "hub", "--extra"]),
        pytest.raises(SystemExit) as exc,
    ):
        run.main()
    assert exc.value.code == 0
    mock_run_single.assert_called_once()
    args, _ = mock_run_single.call_args
    assert "app.py" in str(args[0])
    assert args[1] == "Hub"
    assert args[2] == ["--extra"]


@patch("run.run_single")
def test_main_agent(mock_run_single):
    mock_run_single.return_value = 0
    with (
        patch("sys.argv", ["run.py", "agent", "--name", "test-pc"]),
        pytest.raises(SystemExit) as exc,
    ):
        run.main()
    assert exc.value.code == 0
    mock_run_single.assert_called_once()
    args, _ = mock_run_single.call_args
    assert "agent.py" in str(args[0])
    assert args[1] == "Agent"
    assert args[2] == ["--name", "test-pc"]


@patch("run.run_both")
def test_main_no_args_runs_both(mock_run_both):
    mock_run_both.return_value = 0
    with patch("sys.argv", ["run.py"]), pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0
    mock_run_both.assert_called_once_with([])


@patch("subprocess.Popen")
@patch("time.sleep")
def test_run_both_premature_hub_exit(mock_sleep, mock_popen):
    hub_mock = MagicMock()
    hub_mock.poll.return_value = 1
    hub_mock.returncode = 1
    mock_popen.return_value = hub_mock

    code = run.run_both([])
    assert code == 1


@patch("subprocess.Popen")
@patch("time.sleep")
def test_run_both_hub_port_env(mock_sleep, mock_popen):
    hub_mock = MagicMock()
    hub_mock.poll.return_value = 1
    hub_mock.returncode = 0
    mock_popen.return_value = hub_mock

    with patch.dict(os.environ, {"HUB_PORT": "9876"}, clear=False):
        os.environ.pop("HUB_URL", None)
        run.run_both([])
        assert mock_popen.call_count >= 1
        _, kwargs = mock_popen.call_args_list[0]
        assert kwargs["env"]["HUB_URL"] == "ws://127.0.0.1:9876"


@patch("run.run_single")
def test_main_sim(mock_run_single):
    mock_run_single.return_value = 0
    with (
        patch("sys.argv", ["run.py", "sim", "-c", "/help"]),
        pytest.raises(SystemExit) as exc,
    ):
        run.main()
    assert exc.value.code == 0
    mock_run_single.assert_called_once()
    args, _ = mock_run_single.call_args
    assert "slacksim.py" in str(args[0])
    assert args[1] == "SlackSim"
    assert args[2] == ["-c", "/help"]


@patch("run.run_single")
def test_main_slacksim_alias(mock_run_single):
    mock_run_single.return_value = 0
    with (
        patch("sys.argv", ["run.py", "slacksim"]),
        pytest.raises(SystemExit) as exc,
    ):
        run.main()
    assert exc.value.code == 0
    mock_run_single.assert_called_once()
    args, _ = mock_run_single.call_args
    assert "slacksim.py" in str(args[0])

