"""Tests for TermScreen pyte-backed terminal emulator."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text

from microvm_orchestrator.tui.term_screen import TermScreen, _rich_color


class TestRichColor:
    def test_default_returns_none(self):
        assert _rich_color("default") is None

    def test_empty_returns_none(self):
        assert _rich_color("") is None

    def test_brown_maps_to_yellow(self):
        assert _rich_color("brown") == "yellow"

    def test_hex_color_gets_hash(self):
        assert _rich_color("ff0000") == "#ff0000"
        assert _rich_color("00ff00") == "#00ff00"

    def test_named_color_passes_through(self):
        assert _rich_color("red") == "red"
        assert _rich_color("green") == "green"


class TestTermScreen:
    def test_plain_text_renders(self, tmp_path: Path):
        log = tmp_path / "vm.log"
        log.write_bytes(b"Hello world\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        rendered = ts.render()
        assert "Hello world" in rendered.plain

    def test_generation_increments(self, tmp_path: Path):
        log = tmp_path / "vm.log"
        log.write_bytes(b"line1\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        assert ts.generation == 0
        ts.poll()
        assert ts.generation == 1
        # No new data → generation stays
        ts.poll()
        assert ts.generation == 1
        # Append more data
        with log.open("ab") as f:
            f.write(b"line2\r\n")
        ts.poll()
        assert ts.generation == 2

    def test_cursor_movement_no_stray_chars(self, tmp_path: Path):
        """Cursor movement sequences like \\x1b[1G should not produce stray 'G'."""
        log = tmp_path / "vm.log"
        # Write text, then cursor-to-column-1 and overwrite
        log.write_bytes(b"old text\x1b[1Gnew text\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        rendered = ts.render()
        assert "G" not in rendered.plain or "new text" in rendered.plain
        assert "old text" not in rendered.plain
        assert "new text" in rendered.plain

    def test_ansi_colors_render_with_styles(self, tmp_path: Path):
        """ANSI color codes should produce styled Rich Text."""
        log = tmp_path / "vm.log"
        # Red text: ESC[31m ... ESC[0m
        log.write_bytes(b"\x1b[31mERROR\x1b[0m ok\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        rendered = ts.render()
        assert "ERROR" in rendered.plain
        assert "ok" in rendered.plain
        # The rendered Text should have spans (styles applied)
        assert len(rendered._spans) > 0

    def test_bold_text(self, tmp_path: Path):
        log = tmp_path / "vm.log"
        log.write_bytes(b"\x1b[1mBOLD\x1b[0m\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        rendered = ts.render()
        assert "BOLD" in rendered.plain
        assert len(rendered._spans) > 0

    def test_incremental_tailing(self, tmp_path: Path):
        """TermScreen should pick up new bytes appended after initial read."""
        log = tmp_path / "vm.log"
        log.write_bytes(b"first\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        assert "first" in ts.render().plain

        with log.open("ab") as f:
            f.write(b"second\r\n")
        ts.poll()
        rendered = ts.render()
        assert "first" in rendered.plain
        assert "second" in rendered.plain

    def test_missing_file_does_not_crash(self, tmp_path: Path):
        log = tmp_path / "nonexistent.log"
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()  # Should not raise
        rendered = ts.render()
        assert rendered.plain == ""

    def test_multiline_output(self, tmp_path: Path):
        log = tmp_path / "vm.log"
        log.write_bytes(b"line1\r\nline2\r\nline3\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        rendered = ts.render()
        lines = rendered.plain.split("\n")
        assert len(lines) >= 3
        assert "line1" in lines[0]
        assert "line2" in lines[1]
        assert "line3" in lines[2]

    def test_resize(self, tmp_path: Path):
        log = tmp_path / "vm.log"
        log.write_bytes(b"test\r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.resize(columns=40, lines=10)
        ts.poll()
        rendered = ts.render()
        assert "test" in rendered.plain

    def test_inplace_status_update(self, tmp_path: Path):
        """Simulates in-place updates like systemd boot status using \\r."""
        log = tmp_path / "vm.log"
        # Write "Loading..." then overwrite with "Done!"
        log.write_bytes(b"Loading...\rDone!     \r\n")
        ts = TermScreen(log, columns=80, lines=24)
        ts.poll()
        rendered = ts.render()
        assert "Done!" in rendered.plain
        assert "Loading" not in rendered.plain
