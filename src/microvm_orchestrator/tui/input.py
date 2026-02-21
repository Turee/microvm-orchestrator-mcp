"""Non-blocking keyboard reader (cbreak mode)."""

from __future__ import annotations

import select
import sys
import termios
import tty
from typing import Optional


# Named key constants
KEY_UP = "up"
KEY_DOWN = "down"
KEY_RIGHT = "right"
KEY_LEFT = "left"
KEY_PAGE_UP = "page_up"
KEY_PAGE_DOWN = "page_down"
KEY_ENTER = "enter"
KEY_TAB = "tab"
KEY_ESCAPE = "escape"

# Escape sequences for arrow keys
_ESCAPE_SEQS = {
    "[A": KEY_UP,
    "[B": KEY_DOWN,
    "[C": KEY_RIGHT,
    "[D": KEY_LEFT,
    "[5~": KEY_PAGE_UP,
    "[6~": KEY_PAGE_DOWN,
}


class InputReader:
    """Non-blocking keyboard input with cbreak mode and escape sequence parsing.

    Uses cbreak mode (not raw) so Ctrl+C still raises KeyboardInterrupt.
    Reads are non-blocking via select() with a configurable timeout.
    """

    def __init__(self, fd: Optional[int] = None) -> None:
        self._fd = fd if fd is not None else sys.stdin.fileno()
        self._old_settings: Optional[list] = None
        self._started = False

    def start(self) -> None:
        """Enter cbreak mode, saving the original terminal settings."""
        if self._started:
            return
        self._old_settings = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._started = True

    def stop(self) -> None:
        """Restore the original terminal settings."""
        if not self._started or self._old_settings is None:
            return
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
        self._old_settings = None
        self._started = False

    def read_key(self, timeout: float = 0.0) -> Optional[str]:
        """Read a single key or named key event, or None if nothing available.

        Args:
            timeout: Seconds to wait for input. 0 = non-blocking poll.

        Returns:
            A single character, a KEY_* constant for special keys, or None.
        """
        if not self._started:
            return None

        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None

        ch = self._read_char()
        if ch is None:
            return None

        if ch == "\x1b":
            return self._parse_escape(timeout=0.05)

        if ch == "\r" or ch == "\n":
            return KEY_ENTER
        if ch == "\t":
            return KEY_TAB

        return ch

    def _read_char(self) -> Optional[str]:
        """Read a single byte from the fd."""
        import os

        try:
            data = os.read(self._fd, 1)
        except OSError:
            return None
        return data.decode("utf-8", errors="replace") if data else None

    def _parse_escape(self, timeout: float) -> str:
        """Try to read an escape sequence; fall back to bare ESC."""
        seq = ""
        for _ in range(4):
            ready, _, _ = select.select([self._fd], [], [], timeout)
            if not ready:
                break
            ch = self._read_char()
            if ch is None:
                break
            seq += ch

            # CSI-based sequences usually terminate with a letter or "~".
            if ch.isalpha() or ch == "~":
                break

        return _ESCAPE_SEQS.get(seq, KEY_ESCAPE)

    def __enter__(self) -> InputReader:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
