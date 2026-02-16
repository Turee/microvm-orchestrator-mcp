"""Tests for LogTailer - incremental file tailing."""

import os
from pathlib import Path

from microvm_orchestrator.tui.tail import LogTailer


def test_basic_tailing(tmp_path: Path) -> None:
    """Write to a temp file incrementally, assert get_lines() returns correct content."""
    log_file = tmp_path / "test.log"
    log_file.write_text("line1\nline2\n")

    tailer = LogTailer(log_file)
    tailer.poll()
    assert tailer.get_lines() == ["line1", "line2"]

    # Append more data
    with log_file.open("a") as f:
        f.write("line3\nline4\n")

    tailer.poll()
    assert tailer.get_lines() == ["line1", "line2", "line3", "line4"]


def test_rolling_buffer(tmp_path: Path) -> None:
    """Deque maxlen caps the number of retained lines."""
    log_file = tmp_path / "test.log"
    log_file.write_text("")

    tailer = LogTailer(log_file, maxlen=3)

    with log_file.open("a") as f:
        for i in range(5):
            f.write(f"line{i}\n")

    tailer.poll()
    assert tailer.get_lines() == ["line2", "line3", "line4"]


def test_file_not_found(tmp_path: Path) -> None:
    """Polling a nonexistent file does not raise."""
    tailer = LogTailer(tmp_path / "nonexistent.log")
    tailer.poll()
    assert tailer.get_lines() == []


def test_partial_line(tmp_path: Path) -> None:
    """Incomplete trailing line is carried to the next poll."""
    log_file = tmp_path / "test.log"
    log_file.write_text("complete\npartial")

    tailer = LogTailer(log_file)
    tailer.poll()
    # "partial" has no trailing newline, so it's buffered
    assert tailer.get_lines() == ["complete"]

    # Now finish the partial line
    with log_file.open("a") as f:
        f.write(" continued\n")

    tailer.poll()
    assert tailer.get_lines() == ["complete", "partial continued"]


def test_binary_safety(tmp_path: Path) -> None:
    """Binary data is handled via errors='replace' without crashing."""
    log_file = tmp_path / "test.log"
    log_file.write_bytes(b"hello\n\xff\xfe world\n")

    tailer = LogTailer(log_file)
    tailer.poll()
    lines = tailer.get_lines()
    assert len(lines) == 2
    assert lines[0] == "hello"


def test_reset(tmp_path: Path) -> None:
    """Reset clears buffer and re-reads from the start."""
    log_file = tmp_path / "test.log"
    log_file.write_text("line1\nline2\n")

    tailer = LogTailer(log_file)
    tailer.poll()
    assert len(tailer.get_lines()) == 2

    tailer.reset()
    assert tailer.get_lines() == []

    tailer.poll()
    assert tailer.get_lines() == ["line1", "line2"]


def test_empty_file(tmp_path: Path) -> None:
    """Tailing an empty file returns no lines."""
    log_file = tmp_path / "test.log"
    log_file.write_text("")

    tailer = LogTailer(log_file)
    tailer.poll()
    assert tailer.get_lines() == []


def test_multiple_polls_no_new_data(tmp_path: Path) -> None:
    """Repeated polls with no new data don't duplicate lines."""
    log_file = tmp_path / "test.log"
    log_file.write_text("line1\n")

    tailer = LogTailer(log_file)
    tailer.poll()
    tailer.poll()
    tailer.poll()
    assert tailer.get_lines() == ["line1"]
