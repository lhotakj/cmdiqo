"""Unit tests for ConsoleLineReader (history navigation, Esc clear, and line editing)."""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from slacksim.line_reader import ConsoleLineReader


def test_line_reader_initial_state():
    reader = ConsoleLineReader(["/help", "/lan devices list"])
    assert len(reader.history) == 2
    assert reader.history_index == 2
    assert reader.buffer == []
    assert reader.cursor_pos == 0
    assert reader.stashed_buffer == ""


def test_line_reader_type_and_enter():
    reader = ConsoleLineReader(history_file=None)
    reader.start_line()
    for ch in "status":
        done, line = reader.feed_key(ch)
        assert done is False
        assert line is None

    assert "".join(reader.buffer) == "status"
    assert reader.cursor_pos == 6

    done, line = reader.feed_key("ENTER")
    assert done is True
    assert line == "status"
    assert reader.history == ["status"]

    # Consecutive duplicate is not duplicated in history
    reader.start_line()
    for ch in "status":
        reader.feed_key(ch)
    done, line = reader.feed_key("ENTER")
    assert done is True
    assert line == "status"
    assert len(reader.history) == 1

    # Empty line is not added to history
    reader.start_line()
    done, line = reader.feed_key("ENTER")
    assert done is True
    assert line == ""
    assert len(reader.history) == 1


def test_line_reader_esc_deletes_message_content():
    """User requirement: if I press esc I want to delete the content of the message."""
    reader = ConsoleLineReader(["/help"])
    reader.start_line()

    for ch in "some long draft message that should be cleared":
        reader.feed_key(ch)

    assert len(reader.buffer) > 0
    assert reader.cursor_pos > 0

    # Press Esc -> buffer cleared and cursor reset
    done, line = reader.feed_key("ESC")
    assert done is False
    assert line is None
    assert reader.buffer == []
    assert reader.cursor_pos == 0

    # User can immediately type a fresh command after Esc
    for ch in "new command":
        reader.feed_key(ch)
    done, line = reader.feed_key("ENTER")
    assert done is True
    assert line == "new command"


def test_line_reader_up_and_down_history_navigation():
    """User requirement: Up arrow to see previous, Down arrow to go forward in history."""
    history = ["/help", "/lan devices list", "/host AMD uptime"]
    reader = ConsoleLineReader(history)
    reader.start_line()

    # User types a partial draft
    for ch in "draft":
        reader.feed_key(ch)
    assert "".join(reader.buffer) == "draft"

    # Press UP 1 -> recalls most recent command
    reader.feed_key("UP")
    assert "".join(reader.buffer) == "/host AMD uptime"
    assert reader.cursor_pos == len("/host AMD uptime")

    # Press UP 2 -> recalls second most recent
    reader.feed_key("UP")
    assert "".join(reader.buffer) == "/lan devices list"

    # Press UP 3 -> recalls oldest command
    reader.feed_key("UP")
    assert "".join(reader.buffer) == "/help"

    # Press UP 4 -> already at oldest command, remains on /help
    reader.feed_key("UP")
    assert "".join(reader.buffer) == "/help"

    # Press DOWN 1 -> forward to second most recent
    reader.feed_key("DOWN")
    assert "".join(reader.buffer) == "/lan devices list"

    # Press DOWN 2 -> forward to most recent
    reader.feed_key("DOWN")
    assert "".join(reader.buffer) == "/host AMD uptime"

    # Press DOWN 3 -> forward to stashed draft
    reader.feed_key("DOWN")
    assert "".join(reader.buffer) == "draft"

    # Press DOWN 4 -> stays at stashed draft
    reader.feed_key("DOWN")
    assert "".join(reader.buffer) == "draft"


def test_line_reader_up_down_empty_history():
    reader = ConsoleLineReader([])
    reader.start_line()
    for ch in "hello":
        reader.feed_key(ch)

    # With empty history, UP and DOWN do nothing
    reader.feed_key("UP")
    assert "".join(reader.buffer) == "hello"
    reader.feed_key("DOWN")
    assert "".join(reader.buffer) == "hello"


def test_line_reader_max_history():
    reader = ConsoleLineReader(max_history=3)
    for i in range(5):
        reader.start_line()
        for ch in f"cmd_{i}":
            reader.feed_key(ch)
        reader.feed_key("ENTER")

    assert len(reader.history) == 3
    assert reader.history == ["cmd_2", "cmd_3", "cmd_4"]


def test_line_reader_editing_keys():
    reader = ConsoleLineReader()
    reader.start_line()

    # Type "ac"
    reader.feed_key("a")
    reader.feed_key("c")
    assert "".join(reader.buffer) == "ac"

    # Move LEFT and insert "b" -> "abc"
    reader.feed_key("LEFT")
    assert reader.cursor_pos == 1
    reader.feed_key("b")
    assert "".join(reader.buffer) == "abc"
    assert reader.cursor_pos == 2

    # Move HOME -> cursor 0
    reader.feed_key("HOME")
    assert reader.cursor_pos == 0

    # Move LEFT at start does not go negative
    reader.feed_key("LEFT")
    assert reader.cursor_pos == 0

    # Move END -> cursor 3
    reader.feed_key("END")
    assert reader.cursor_pos == 3

    # Move RIGHT at end does not exceed buffer length
    reader.feed_key("RIGHT")
    assert reader.cursor_pos == 3

    # BACKSPACE at end deletes "c" -> "ab"
    reader.feed_key("BACKSPACE")
    assert "".join(reader.buffer) == "ab"
    assert reader.cursor_pos == 2

    # HOME and DELETE deletes "a" -> "b"
    reader.feed_key("HOME")
    reader.feed_key("DELETE")
    assert "".join(reader.buffer) == "b"
    assert reader.cursor_pos == 0

    # BACKSPACE at 0 does nothing
    reader.feed_key("BACKSPACE")
    assert "".join(reader.buffer) == "b"

    # DELETE at end does nothing
    reader.feed_key("END")
    reader.feed_key("DELETE")
    assert "".join(reader.buffer) == "b"


def test_line_reader_redraw_line():
    reader = ConsoleLineReader()
    reader.buffer = list("test")
    reader.cursor_pos = 2  # Cursor before 's'

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        reader.redraw_line("prompt> ")

    out = buf.getvalue()
    assert "\r\033[Kprompt> test" in out
    assert "\033[2D" in out


def test_line_reader_non_tty():
    reader = ConsoleLineReader()
    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdin.readline", return_value="piped line\n"),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "piped line"


def test_line_reader_non_tty_eof():
    reader = ConsoleLineReader()
    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stdin.readline", return_value=""),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
        pytest.raises(EOFError),
    ):
        reader.readline("prompt> ")


def test_line_reader_windows_esc_and_typing():
    reader = ConsoleLineReader()
    mock_keys = ["m", "i", "s", "t", "a", "k", "e", "\x1b", "c", "o", "r", "r", "e", "c", "t", "\r"]

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "nt"),
        patch("msvcrt.getwch", side_effect=mock_keys),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "correct"


def test_line_reader_windows_arrows_and_history():
    reader = ConsoleLineReader(["/help", "/lan version"])
    # Extended prefix \xe0 followed by 'H' (UP), then Enter '\r'
    mock_keys = ["\xe0", "H", "\r"]

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "nt"),
        patch("msvcrt.getwch", side_effect=mock_keys),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "/lan version"


def test_line_reader_windows_ctrl_c():
    reader = ConsoleLineReader()
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "nt"),
        patch("msvcrt.getwch", return_value="\x03"),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
        pytest.raises(KeyboardInterrupt),
    ):
        reader.readline("prompt> ")


def test_line_reader_windows_eof():
    reader = ConsoleLineReader()
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "nt"),
        patch("msvcrt.getwch", return_value="\x04"),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
        pytest.raises(EOFError),
    ):
        reader.readline("prompt> ")


def test_line_reader_windows_backspace():
    reader = ConsoleLineReader()
    mock_keys = ["a", "b", "\x08", "c", "\r"]

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "nt"),
        patch("msvcrt.getwch", side_effect=mock_keys),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "ac"


def test_line_reader_windows_extended_navigation():
    reader = ConsoleLineReader(["/help", "/lan version"])
    # UP, DOWN, LEFT, RIGHT, HOME, END, DELETE
    mock_keys = [
        "\xe0", "H",  # UP -> /lan version
        "\xe0", "P",  # DOWN -> empty/stash
        "h", "i",
        "\xe0", "G",  # HOME
        "\xe0", "M",  # RIGHT
        "\xe0", "S",  # DELETE
        "\xe0", "O",  # END
        "\xe0", "K",  # LEFT
        "!",
        "\r",
    ]

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "nt"),
        patch("msvcrt.getwch", side_effect=mock_keys),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "!h"


def test_line_reader_unix_fallback():
    reader = ConsoleLineReader()
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "posix"),
        patch("builtins.input", return_value="unix fallback line"),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "unix fallback line"


def test_line_reader_unix_interactive():
    reader = ConsoleLineReader(["/help", "/lan devices"])

    # Simulate chunks: b'test', b'\x1b' (standalone ESC), b'ok\n'
    chunks = [b"test", b"\x1b", b"ok\n"]

    mock_termios = MagicMock()
    mock_tty = MagicMock()
    mock_select = MagicMock()
    # Standalone ESC: select returns empty (timeout)
    mock_select.select.return_value = ([], [], [])

    with (
        patch.dict(sys.modules, {"termios": mock_termios, "tty": mock_tty, "select": mock_select}),
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "posix"),
        patch("sys.stdin.fileno", return_value=0),
        patch("os.read", side_effect=chunks),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "ok"


def test_line_reader_unix_up_arrow():
    reader = ConsoleLineReader(["/help", "/lan version"])
    # b"\x1b[A" (Up arrow), then b"\n"
    chunks = [b"\x1b[A", b"\n"]

    mock_termios = MagicMock()
    mock_tty = MagicMock()

    with (
        patch.dict(sys.modules, {"termios": mock_termios, "tty": mock_tty}),
        patch("sys.stdin.isatty", return_value=True),
        patch("os.name", "posix"),
        patch("sys.stdin.fileno", return_value=0),
        patch("os.read", side_effect=chunks),
        patch("sys.stdout.write"),
        patch("sys.stdout.flush"),
    ):
        line = reader.readline("prompt> ")
        assert line == "/lan version"


def test_line_reader_history_file_persistence(tmp_path):
    history_file = tmp_path / ".shango-history"

    # 1. File doesn't exist initially -> created on first command entry
    assert not history_file.exists()
    reader1 = ConsoleLineReader(history_file=history_file)
    assert reader1.history == []

    reader1.start_line()
    for ch in "/lan devices list":
        reader1.feed_key(ch)
    done, line = reader1.feed_key("ENTER")
    assert done is True
    assert line == "/lan devices list"
    assert history_file.exists()

    # Enter a second command
    reader1.start_line()
    for ch in "/host AMD uptime":
        reader1.feed_key(ch)
    reader1.feed_key("ENTER")

    # Verify file contents
    content = history_file.read_text(encoding="utf-8")
    assert content == "/lan devices list\n/host AMD uptime\n"

    # 2. Start a second reader pointing to the same file -> loads existing history
    reader2 = ConsoleLineReader(history_file=history_file)
    assert reader2.history == ["/lan devices list", "/host AMD uptime"]

    # History navigation works with loaded history
    reader2.start_line()
    reader2.feed_key("UP")
    assert "".join(reader2.buffer) == "/host AMD uptime"
    reader2.feed_key("UP")
    assert "".join(reader2.buffer) == "/lan devices list"

    # Appending a new command writes immediately
    reader2.start_line()
    for ch in "/help":
        reader2.feed_key(ch)
    reader2.feed_key("ENTER")

    content_updated = history_file.read_text(encoding="utf-8")
    assert content_updated == "/lan devices list\n/host AMD uptime\n/help\n"

