"""Tests for tui/input.py escape sequence parsing."""

from __future__ import annotations

from unittest.mock import patch

from microvm_orchestrator.tui.input import (
    KEY_DOWN,
    KEY_ESCAPE,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    KEY_UP,
    InputReader,
)


def _always_ready(*args, **kwargs):
    return ([0], [], [])


def test_parse_arrow_up_escape_sequence() -> None:
    reader = InputReader(fd=0)
    chars = iter(["[", "A"])

    with patch("microvm_orchestrator.tui.input.select.select", _always_ready):
        with patch.object(reader, "_read_char", side_effect=lambda: next(chars, None)):
            assert reader._parse_escape(timeout=0.01) == KEY_UP


def test_parse_arrow_down_escape_sequence() -> None:
    reader = InputReader(fd=0)
    chars = iter(["[", "B"])

    with patch("microvm_orchestrator.tui.input.select.select", _always_ready):
        with patch.object(reader, "_read_char", side_effect=lambda: next(chars, None)):
            assert reader._parse_escape(timeout=0.01) == KEY_DOWN


def test_parse_page_up_escape_sequence() -> None:
    reader = InputReader(fd=0)
    chars = iter(["[", "5", "~"])

    with patch("microvm_orchestrator.tui.input.select.select", _always_ready):
        with patch.object(reader, "_read_char", side_effect=lambda: next(chars, None)):
            assert reader._parse_escape(timeout=0.01) == KEY_PAGE_UP


def test_parse_page_down_escape_sequence() -> None:
    reader = InputReader(fd=0)
    chars = iter(["[", "6", "~"])

    with patch("microvm_orchestrator.tui.input.select.select", _always_ready):
        with patch.object(reader, "_read_char", side_effect=lambda: next(chars, None)):
            assert reader._parse_escape(timeout=0.01) == KEY_PAGE_DOWN


def test_parse_unknown_escape_sequence_falls_back_to_escape() -> None:
    reader = InputReader(fd=0)
    chars = iter(["[", "9", "~"])

    with patch("microvm_orchestrator.tui.input.select.select", _always_ready):
        with patch.object(reader, "_read_char", side_effect=lambda: next(chars, None)):
            assert reader._parse_escape(timeout=0.01) == KEY_ESCAPE
