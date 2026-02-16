"""Thread-safe logging handler that captures records into a bounded buffer."""

from __future__ import annotations

import logging
import threading
from collections import deque


class LogCapture(logging.Handler):
    """A logging handler that stores formatted lines in a thread-safe deque.

    Multiline log messages are split so each line is a separate entry.
    The API (get_lines) matches LogTailer for interchangeable use in the TUI.
    """

    def __init__(self, maxlen: int = 500) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                for line in msg.split("\n"):
                    self._lines.append(line)
        except Exception:
            self.handleError(record)

    def get_lines(self) -> list[str]:
        """Return a snapshot of the buffer contents."""
        with self._lock:
            return list(self._lines)
