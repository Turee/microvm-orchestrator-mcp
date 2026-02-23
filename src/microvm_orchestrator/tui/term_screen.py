"""Terminal screen emulator backed by pyte for rendering raw pty output."""

from __future__ import annotations

from pathlib import Path

import pyte
from rich.style import Style
from rich.text import Text


def _rich_color(raw: str) -> str | None:
    """Convert pyte color to Rich color string.

    pyte uses: "default", named colors ("red", "green", ...),
    "brown" for ANSI yellow, and bare 6-char hex ("ff0000") for 256-color.
    Rich needs "#ff0000" for hex colors.
    """
    if not raw or raw == "default":
        return None
    if raw == "brown":
        return "yellow"
    if len(raw) == 6 and all(c in "0123456789abcdef" for c in raw):
        return f"#{raw}"
    return raw  # named colors pass through


class TermScreen:
    """Headless terminal emulator that tails a log file in binary mode.

    Feeds raw bytes to pyte, renders the screen buffer as Rich Text.
    """

    def __init__(self, path: Path, columns: int = 120, lines: int = 50):
        self.path = path
        self._screen = pyte.Screen(columns, lines)
        self._stream = pyte.ByteStream(self._screen)
        self._pos: int = 0
        self._generation: int = 0

    def poll(self) -> None:
        """Read new bytes from log file and feed to pyte."""
        try:
            with self.path.open("rb") as f:
                f.seek(self._pos)
                data = f.read()
                self._pos = f.tell()
        except (OSError, ValueError):
            return
        if data:
            self._stream.feed(data)
            self._generation += 1

    @property
    def generation(self) -> int:
        return self._generation

    def render(self) -> Text:
        """Render current pyte screen buffer as Rich Text.

        Only renders up to the last non-empty row to avoid bloating
        the RichLog with blank lines.
        """
        result = Text()
        screen = self._screen
        buf = screen.buffer

        # Find last row that has any content
        last_row = -1
        for row in range(screen.lines):
            if row in buf:
                last_row = row

        for row in range(last_row + 1):
            if row > 0:
                result.append("\n")
            if row not in buf:
                continue
            # Find last non-space column to avoid trailing whitespace
            line_end = screen.columns
            while line_end > 0 and buf[row][line_end - 1].data == " ":
                line_end -= 1
            for col in range(line_end):
                char = buf[row][col]
                style = self._char_style(char)
                result.append(char.data, style=style)
        return result

    def resize(self, columns: int, lines: int) -> None:
        self._screen.resize(lines, columns)

    @staticmethod
    def _char_style(char: pyte.screens.Char) -> Style | None:
        parts: list[str] = []
        fg = _rich_color(char.fg)
        bg = _rich_color(char.bg)
        if fg:
            parts.append(fg)
        if bg:
            parts.append(f"on {bg}")
        if char.bold:
            parts.append("bold")
        if char.italics:
            parts.append("italic")
        if char.underscore:
            parts.append("underline")
        if char.strikethrough:
            parts.append("strike")
        if char.reverse:
            parts.append("reverse")
        if char.blink:
            parts.append("blink")
        try:
            return Style.parse(" ".join(parts)) if parts else None
        except Exception:
            return None
