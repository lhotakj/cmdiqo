"""Console line reader with history navigation and line editing.

Provides interactive command-line editing with:
- Up Arrow: navigate backwards through history
- Down Arrow: navigate forwards through history
- Escape: clear/delete current message buffer
- Left / Right arrows: cursor movement
- Home / End: start / end of line navigation
- Backspace / Delete: character deletion
- Cross-platform support for Windows (msvcrt) and Unix (termios/tty/select)
- Graceful non-interactive fallback (pipes, non-tty stdin)
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path
from typing import Any

# Default history file path in user's home directory
DEFAULT_HISTORY_FILE = Path.home() / ".cmdiqo-history"

# Track original Unix terminal attributes for guaranteed restoration
_orig_termios: Any = None


def restore_terminal() -> None:
    """Restore original terminal attributes on Unix."""
    global _orig_termios
    if _orig_termios is not None and os.name != "nt":
        with contextlib.suppress(Exception):
            import termios

            termios.tcsetattr(0, termios.TCSADRAIN, _orig_termios)
        _orig_termios = None


if os.name != "nt":
    with contextlib.suppress(Exception):
        import atexit

        atexit.register(restore_terminal)


class ConsoleLineReader:
    """Interactive console line reader with history navigation and Esc line clear."""

    def __init__(
        self,
        history: list[str] | None = None,
        max_history: int = 1000,
        history_file: Path | str | None = DEFAULT_HISTORY_FILE,
    ) -> None:
        self.max_history = max_history
        self.history_file: Path | None = Path(history_file) if history_file is not None else None
        self.history: list[str] = []

        # Load persisted history from file if history is not explicitly provided
        if history is not None:
            self.history = list(history)
        elif self.history_file is not None:
            self._load_history_from_file()

        self.history_index: int = len(self.history)
        self.stashed_buffer: str = ""
        self.buffer: list[str] = []
        self.cursor_pos: int = 0
        self._init_terminal()

    def _load_history_from_file(self) -> None:
        """Load history items from the history file if it exists."""
        if self.history_file is None:
            return
        with contextlib.suppress(Exception):
            if self.history_file.exists():
                with open(self.history_file, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        cmd = line.rstrip("\r\n")
                        if cmd:
                            self.history.append(cmd)
                if len(self.history) > self.max_history:
                    self.history = self.history[-self.max_history :]

    def _append_history_to_file(self, cmd: str) -> None:
        """Immediately append a command to the history file, creating it if needed."""
        if self.history_file is None:
            return
        with contextlib.suppress(Exception):
            # Ensure parent directory exists
            if self.history_file.parent:
                self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(f"{cmd}\n")

    def _init_terminal(self) -> None:
        """Enable virtual terminal processing on Windows if supported."""
        if os.name == "nt":
            with contextlib.suppress(Exception):
                # Trigger Windows console VT100 processing mode
                os.system("")

    def start_line(self) -> None:
        """Initialize state for reading a new input line."""
        self.buffer.clear()
        self.cursor_pos = 0
        self.history_index = len(self.history)
        self.stashed_buffer = ""

    def feed_key(self, key: str) -> tuple[bool, str | None]:
        """Process a logical key event.

        Returns:
            Tuple of (done: bool, line: Optional[str]).
            When done is True, line contains the submitted string.
        """
        if key == "ENTER":
            result = "".join(self.buffer)
            trimmed = result.strip()
            if trimmed and (not self.history or self.history[-1] != trimmed):
                self.history.append(trimmed)
                if len(self.history) > self.max_history:
                    self.history.pop(0)
                self._append_history_to_file(trimmed)
            self.history_index = len(self.history)
            self.stashed_buffer = ""
            return True, result

        if key == "ESC":
            # Clear/delete current message buffer
            self.buffer.clear()
            self.cursor_pos = 0
            return False, None

        if key == "UP":
            if self.history:
                if self.history_index == len(self.history):
                    self.stashed_buffer = "".join(self.buffer)
                if self.history_index > 0:
                    self.history_index -= 1
                    self.buffer = list(self.history[self.history_index])
                    self.cursor_pos = len(self.buffer)
            return False, None

        if key == "DOWN":
            if self.history_index < len(self.history):
                self.history_index += 1
                if self.history_index < len(self.history):
                    self.buffer = list(self.history[self.history_index])
                else:
                    self.buffer = list(self.stashed_buffer)
                self.cursor_pos = len(self.buffer)
            return False, None

        if key == "BACKSPACE":
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
                self.buffer.pop(self.cursor_pos)
            return False, None

        if key == "DELETE":
            if self.cursor_pos < len(self.buffer):
                self.buffer.pop(self.cursor_pos)
            return False, None

        if key == "LEFT":
            if self.cursor_pos > 0:
                self.cursor_pos -= 1
            return False, None

        if key == "RIGHT":
            if self.cursor_pos < len(self.buffer):
                self.cursor_pos += 1
            return False, None

        if key == "HOME":
            self.cursor_pos = 0
            return False, None

        if key == "END":
            self.cursor_pos = len(self.buffer)
            return False, None

        # Printable character
        if len(key) == 1 and key >= " " and key != "\x7f":
            self.buffer.insert(self.cursor_pos, key)
            self.cursor_pos += 1
            return False, None

        return False, None

    def redraw_line(self, prompt: str) -> None:
        """Redraw current line in terminal with proper prompt and cursor position."""
        line = "".join(self.buffer)
        # \r to start of line, \033[K to clear line from cursor to end
        sys.stdout.write(f"\r\033[K{prompt}{line}")
        # Position cursor correctly if not at end
        back = len(self.buffer) - self.cursor_pos
        if back > 0:
            sys.stdout.write(f"\033[{back}D")
        sys.stdout.flush()

    def readline(self, prompt: str = "") -> str:
        """Read a line of text from standard input with interactive editing and history."""
        # Non-interactive / piped fallback
        if not sys.stdin.isatty():
            sys.stdout.write(prompt)
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:
                raise EOFError
            res = line.rstrip("\r\n")
            trimmed = res.strip()
            if trimmed and (not self.history or self.history[-1] != trimmed):
                self.history.append(trimmed)
                if len(self.history) > self.max_history:
                    self.history.pop(0)
                self._append_history_to_file(trimmed)
            return res

        self.start_line()
        sys.stdout.write(prompt)
        sys.stdout.flush()

        if os.name == "nt":
            return self._readline_windows(prompt)
        return self._readline_unix(prompt)

    def _readline_windows(self, prompt: str) -> str:
        """Windows interactive key reading via msvcrt."""
        import msvcrt

        while True:
            ch = msvcrt.getwch()

            if ch == "\r":
                sys.stdout.write("\n")
                sys.stdout.flush()
                _, line = self.feed_key("ENTER")
                return line if line is not None else ""

            if ch == "\x03":  # Ctrl+C
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt

            if ch in ("\x1a", "\x04"):  # Ctrl+Z or Ctrl+D
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise EOFError

            if ch == "\x1b":  # ESC: clear message content
                self.feed_key("ESC")
                self.redraw_line(prompt)
                continue

            if ch in ("\x08", "\x7f"):  # Backspace
                self.feed_key("BACKSPACE")
                self.redraw_line(prompt)
                continue

            if ch in ("\x00", "\xe0"):  # Special key prefix
                code = msvcrt.getwch()
                key_map = {
                    "H": "UP",
                    "P": "DOWN",
                    "K": "LEFT",
                    "M": "RIGHT",
                    "G": "HOME",
                    "O": "END",
                    "S": "DELETE",
                }
                mapped = key_map.get(code)
                if mapped:
                    self.feed_key(mapped)
                    self.redraw_line(prompt)
                continue

            if ch >= " " and ch != "\x7f":
                self.feed_key(ch)
                self.redraw_line(prompt)

    def _readline_unix(self, prompt: str) -> str:
        """Unix unbuffered interactive key reading via raw os.read and termios."""
        try:
            import select
            import termios
            import tty

            fd = sys.stdin.fileno()
            global _orig_termios
            if _orig_termios is None:
                _orig_termios = termios.tcgetattr(fd)

            key_map: dict[bytes, str] = {
                b"\x1b[A": "UP",
                b"\x1bOA": "UP",
                b"\x1b[B": "DOWN",
                b"\x1bOB": "DOWN",
                b"\x1b[C": "RIGHT",
                b"\x1bOC": "RIGHT",
                b"\x1b[D": "LEFT",
                b"\x1bOD": "LEFT",
                b"\x1b[H": "HOME",
                b"\x1b[1~": "HOME",
                b"\x1b[F": "END",
                b"\x1b[4~": "END",
                b"\x1b[3~": "DELETE",
            }

            try:
                tty.setcbreak(fd)
                while True:
                    data = os.read(fd, 64)
                    if not data:
                        raise EOFError

                    # Handle Enter (\r or \n)
                    if b"\r" in data or b"\n" in data:
                        parts = re.split(rb"[\r\n]", data)
                        prefix = parts[0].decode("utf-8", errors="replace")
                        for ch in prefix:
                            if ch >= " " and ch != "\x7f":
                                self.feed_key(ch)
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        _, line = self.feed_key("ENTER")
                        return line if line is not None else ""

                    # Handle Ctrl+C
                    if b"\x03" in data:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        raise KeyboardInterrupt

                    # Handle Ctrl+D
                    if b"\x04" in data:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        raise EOFError

                    # Handle Backspace
                    if data in (b"\x08", b"\x7f"):
                        self.feed_key("BACKSPACE")
                        self.redraw_line(prompt)
                        continue

                    # Handle Escape and escape sequences
                    if data.startswith(b"\x1b"):
                        if data == b"\x1b":
                            # Check if remaining bytes follow within 50ms
                            r, _, _ = select.select([fd], [], [], 0.05)
                            if not r:
                                # Standalone ESC -> clear line buffer
                                self.feed_key("ESC")
                                self.redraw_line(prompt)
                                continue
                            data += os.read(fd, 64)

                        # Match against multi-byte escape sequences
                        mapped = key_map.get(data)
                        if mapped:
                            self.feed_key(mapped)
                            self.redraw_line(prompt)
                        continue

                    # Normal text insertion
                    text = data.decode("utf-8", errors="replace")
                    for ch in text:
                        if ch >= " " and ch != "\x7f":
                            self.feed_key(ch)
                    self.redraw_line(prompt)

            finally:
                restore_terminal()

        except Exception:  # noqa: BLE001
            restore_terminal()
            res = input(prompt)
            trimmed = res.strip()
            if trimmed and (not self.history or self.history[-1] != trimmed):
                self.history.append(trimmed)
                if len(self.history) > self.max_history:
                    self.history.pop(0)
                self._append_history_to_file(trimmed)
            return res
