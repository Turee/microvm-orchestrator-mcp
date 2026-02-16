"""LogTailer - incremental file tailing with deque buffer."""

from __future__ import annotations

from collections import deque
from pathlib import Path


class LogTailer:
    """Incrementally tail a file, keeping a bounded rolling buffer of lines.

    Uses seek() to resume from the last read position on each poll,
    and a deque(maxlen) to cap memory usage.
    """

    def __init__(self, path: str | Path, maxlen: int = 200) -> None:
        self.path = Path(path)
        self._maxlen = maxlen
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._pos: int = 0
        self._partial: str = ""  # incomplete trailing line from last read

    def poll(self) -> None:
        """Read any new data appended since the last poll."""
        try:
            with self.path.open("r", errors="replace") as f:
                f.seek(self._pos)
                new_data = f.read()
                self._pos = f.tell()
        except (OSError, ValueError):
            return

        if not new_data:
            return

        # Prepend any leftover partial line from previous read
        text = self._partial + new_data

        # Split into lines; last element is either "" (if data ended with \n)
        # or an incomplete line to carry forward.
        parts = text.split("\n")
        self._partial = parts.pop()

        for line in parts:
            self._lines.append(line)

    def get_lines(self) -> list[str]:
        """Return the current buffer contents as a list."""
        return list(self._lines)

    def reset(self) -> None:
        """Clear the buffer and reset the read position."""
        self._lines.clear()
        self._pos = 0
        self._partial = ""
