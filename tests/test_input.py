"""Tests for InputReader - non-blocking keyboard input with mocked stdin."""

from __future__ import annotations

import os
import select
from unittest.mock import patch

from microvm_orchestrator.tui.input import (
    KEY_DOWN,
    KEY_ENTER,
    KEY_ESCAPE,
    KEY_LEFT,
    KEY_RIGHT,
    KEY_TAB,
    KEY_UP,
    InputReader,
)


def _make_pipe_reader(data: bytes) -> tuple[InputReader, int]:
    """Create an InputReader backed by a pipe, write data into the pipe.

    Returns (reader, write_fd) so the caller can inject more data if needed.
    """
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    reader = InputReader(fd=read_fd)
    reader._started = True  # bypass start() which needs a real terminal
    return reader, write_fd


def _read_all(reader: InputReader) -> list[str | None]:
    """Drain all available keys from the reader."""
    keys = []
    while True:
        k = reader.read_key(timeout=0.05)
        if k is None:
            break
        keys.append(k)
    return keys


class TestInputReader:
    def test_regular_characters(self) -> None:
        reader, w = _make_pipe_reader(b"abc")
        os.close(w)
        assert _read_all(reader) == ["a", "b", "c"]

    def test_enter_key_cr(self) -> None:
        reader, w = _make_pipe_reader(b"\r")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_ENTER

    def test_enter_key_lf(self) -> None:
        reader, w = _make_pipe_reader(b"\n")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_ENTER

    def test_tab_key(self) -> None:
        reader, w = _make_pipe_reader(b"\t")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_TAB

    def test_arrow_up(self) -> None:
        reader, w = _make_pipe_reader(b"\x1b[A")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_UP

    def test_arrow_down(self) -> None:
        reader, w = _make_pipe_reader(b"\x1b[B")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_DOWN

    def test_arrow_right(self) -> None:
        reader, w = _make_pipe_reader(b"\x1b[C")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_RIGHT

    def test_arrow_left(self) -> None:
        reader, w = _make_pipe_reader(b"\x1b[D")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_LEFT

    def test_bare_escape(self) -> None:
        """ESC with no following sequence returns KEY_ESCAPE."""
        reader, w = _make_pipe_reader(b"\x1b")
        os.close(w)
        assert reader.read_key(timeout=0.05) == KEY_ESCAPE

    def test_unknown_escape_sequence(self) -> None:
        """Unknown escape sequences fall back to KEY_ESCAPE."""
        reader, w = _make_pipe_reader(b"\x1b[Z")
        os.close(w)
        # [Z is not mapped, so should return ESCAPE
        # (the 'Z' remains in the pipe but that's fine for this test)
        assert reader.read_key(timeout=0.05) == KEY_ESCAPE

    def test_no_input_returns_none(self) -> None:
        reader, w = _make_pipe_reader(b"")
        os.close(w)
        assert reader.read_key(timeout=0.0) is None

    def test_not_started_returns_none(self) -> None:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"x")
        os.close(write_fd)
        reader = InputReader(fd=read_fd)
        # Not started, so should return None even with data
        assert reader.read_key(timeout=0.05) is None

    def test_mixed_input(self) -> None:
        """Regular chars mixed with arrow keys."""
        reader, w = _make_pipe_reader(b"a\x1b[Ab\x1b[B")
        os.close(w)
        assert _read_all(reader) == ["a", KEY_UP, "b", KEY_DOWN]

    def test_context_manager(self) -> None:
        """Context manager calls start/stop."""
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        reader = InputReader(fd=read_fd)

        # Patch termios to avoid needing a real terminal
        with patch("microvm_orchestrator.tui.input.termios") as mock_termios, \
             patch("microvm_orchestrator.tui.input.tty") as mock_tty:
            mock_termios.tcgetattr.return_value = [0, 0, 0, 0, 0, 0, []]
            with reader:
                assert reader._started is True
                mock_tty.setcbreak.assert_called_once_with(read_fd)
            assert reader._started is False
            mock_termios.tcsetattr.assert_called_once()

    def test_stop_idempotent(self) -> None:
        """Calling stop() multiple times is safe."""
        reader, w = _make_pipe_reader(b"")
        os.close(w)
        reader.stop()
        reader.stop()  # should not raise
