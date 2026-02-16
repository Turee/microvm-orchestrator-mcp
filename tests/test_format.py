"""Tests for tui/format.py - Claude JSONL stream-json formatting."""

import json
from io import StringIO

from rich.console import Console
from rich.text import Text

from microvm_orchestrator.tui.format import _sanitize_line, format_jsonl_line, format_log_content


def _render(text: Text) -> str:
    """Render Rich Text to plain string (no ANSI)."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True, no_color=True)
    console.print(text, end="")
    return buf.getvalue()


def _jsonl(*events: dict) -> list[str]:
    """Convert dicts to JSONL lines."""
    return [json.dumps(e) for e in events]


# ── format_jsonl_line ───────────────────────────────────────────


class TestFormatJsonlLine:
    def test_text_delta(self):
        event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}
        result = format_jsonl_line(json.dumps(event))
        assert result == ("Hello", "")

    def test_empty_text_delta_skipped(self):
        event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_input_json_delta_skipped(self):
        event = {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"pa'}}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_tool_use_block_start(self):
        event = {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Read"}}
        result = format_jsonl_line(json.dumps(event))
        assert result is not None
        text, style = result
        assert "Read" in text
        assert "cyan" in style

    def test_non_tool_block_start_skipped(self):
        event = {"type": "content_block_start", "content_block": {"type": "text"}}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_result_success(self):
        event = {"type": "result", "subtype": "success", "result": "Task completed", "cost_usd": 0.0123, "duration_ms": 4500}
        result = format_jsonl_line(json.dumps(event))
        assert result is not None
        text, style = result
        assert "Task completed" in text
        assert "$0.0123" in text
        assert "4.5s" in text
        assert "green" in style

    def test_result_error(self):
        event = {"type": "result", "subtype": "error", "result": "Something failed"}
        result = format_jsonl_line(json.dumps(event))
        assert result is not None
        text, style = result
        assert "Something failed" in text
        assert "red" in style

    def test_result_empty_skipped(self):
        event = {"type": "result", "subtype": "success"}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_system_init(self):
        event = {"type": "system", "subtype": "init", "model": "claude-sonnet-4-5-20250929", "session_id": "abc12345-full-uuid"}
        result = format_jsonl_line(json.dumps(event))
        assert result is not None
        text, style = result
        assert "claude-sonnet-4-5-20250929" in text
        assert "abc12345" in text
        assert "dim" in style

    def test_system_non_init_skipped(self):
        event = {"type": "system", "subtype": "other"}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_message_start_skipped(self):
        event = {"type": "message_start"}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_message_stop_skipped(self):
        event = {"type": "message_stop"}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_assistant_skipped(self):
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}
        assert format_jsonl_line(json.dumps(event)) is None

    def test_plain_text_passthrough(self):
        assert format_jsonl_line("just plain text") == ("just plain text", "")

    def test_invalid_json_passthrough(self):
        assert format_jsonl_line("{bad json") == ("{bad json", "")

    def test_empty_line_skipped(self):
        assert format_jsonl_line("") is None
        assert format_jsonl_line("  ") is None


# ── format_log_content ──────────────────────────────────────────


class TestFormatLogContent:
    def test_empty_lines(self):
        result = format_log_content([])
        assert "no output" in result.plain

    def test_plain_text_passthrough(self):
        lines = ["hello world", "second line"]
        result = format_log_content(lines)
        assert result.plain == "hello world\nsecond line"

    def test_jsonl_text_deltas(self):
        lines = _jsonl(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello "}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "world!"}},
        )
        result = format_log_content(lines)
        assert "Hello " in result.plain
        assert "world!" in result.plain

    def test_jsonl_tool_and_text(self):
        lines = _jsonl(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Let me check."}},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Bash"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Done."}},
        )
        result = format_log_content(lines)
        plain = result.plain
        assert "Let me check." in plain
        assert "Bash" in plain
        assert "Done." in plain

    def test_jsonl_skips_noise(self):
        lines = _jsonl(
            {"type": "message_start"},
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "visible"}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        )
        result = format_log_content(lines)
        assert result.plain.strip() == "visible"

    def test_jsonl_with_result(self):
        lines = _jsonl(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Working..."}},
            {"type": "result", "subtype": "success", "result": "All done", "cost_usd": 0.05},
        )
        result = format_log_content(lines)
        plain = result.plain
        assert "Working..." in plain
        assert "All done" in plain
        assert "$0.0500" in plain

    def test_jsonl_all_skipped_shows_no_output(self):
        lines = _jsonl(
            {"type": "message_start"},
            {"type": "message_stop"},
        )
        result = format_log_content(lines)
        assert "no output" in result.plain

    def test_jsonl_system_init(self):
        lines = _jsonl(
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-5-20250929"},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        )
        result = format_log_content(lines)
        assert "claude-sonnet-4-5-20250929" in result.plain
        assert "Hi" in result.plain

    def test_mixed_plain_and_json_detected_as_plain(self):
        # First non-empty line is not JSON, so all treated as plain text
        lines = ["plain first", '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}']
        result = format_log_content(lines)
        assert "plain first" in result.plain

    def test_force_jsonl_with_leading_plain_text(self):
        """force_jsonl=True processes JSONL even when first line is plain text."""
        lines = [
            "NixOS boot message",
            '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}',
            '{"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Bash"}}',
        ]
        result = format_log_content(lines, force_jsonl=True)
        plain = result.plain
        # Plain text passes through, JSONL events are parsed
        assert "NixOS boot message" in plain
        assert "Hello" in plain
        assert "Bash" in plain

    def test_force_jsonl_false_preserves_autodetect(self):
        """force_jsonl=False (default) still auto-detects plain text."""
        lines = ["plain first", '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}']
        result = format_log_content(lines, force_jsonl=False)
        # Auto-detect sees plain first line, treats all as plain
        assert "plain first" in result.plain


# ── Integration with Rich rendering ────────────────────────────


class TestRichRendering:
    def test_styled_tool_name(self):
        lines = _jsonl(
            {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Write"}},
        )
        result = format_log_content(lines)
        rendered = _render(result)
        assert "Write" in rendered

    def test_full_session_rendering(self):
        """Simulate a realistic Claude stream-json session."""
        lines = _jsonl(
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-5-20250929", "session_id": "sess-12345678"},
            {"type": "message_start", "message": {"role": "assistant"}},
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "I'll help you "}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "fix that bug."}},
            {"type": "content_block_stop"},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Read", "id": "tool_1"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"file'}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '": "main.py"}'}},
            {"type": "content_block_stop"},
            {"type": "message_stop"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll help you fix that bug."}]}},
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Fixed!"}},
            {"type": "content_block_stop"},
            {"type": "result", "subtype": "success", "result": "Bug fixed successfully", "cost_usd": 0.0234, "duration_ms": 12000},
        )
        result = format_log_content(lines)
        plain = result.plain
        assert "claude-sonnet-4-5-20250929" in plain
        assert "I'll help you fix that bug." in plain
        assert "Read" in plain
        assert "Fixed!" in plain
        assert "Bug fixed successfully" in plain
        assert "$0.0234" in plain
        assert "12.0s" in plain


# ── Sanitize tests ─────────────────────────────────────────────


class TestSanitizeLine:
    def test_strips_ansi_color_codes(self):
        assert _sanitize_line("\x1b[31mred\x1b[0m") == "red"

    def test_strips_cursor_movement(self):
        assert _sanitize_line("\x1b[2Jhello\x1b[H") == "hello"

    def test_strips_control_characters(self):
        # BEL (\x07), BS (\x08), etc. are stripped; \n and \t are kept
        assert _sanitize_line("hello\x07\x08world") == "helloworld"

    def test_preserves_tabs_and_normal_text(self):
        assert _sanitize_line("col1\tcol2") == "col1\tcol2"

    def test_mixed_ansi_and_control(self):
        line = "\x1b[1;32mOK\x1b[0m\x00\x07 done"
        assert _sanitize_line(line) == "OK done"

    def test_clean_line_unchanged(self):
        assert _sanitize_line("just normal text") == "just normal text"


class TestFormatLogContentSanitization:
    def test_plain_text_ansi_stripped(self):
        """ANSI escapes in plain-text (non-JSONL) serial output are stripped."""
        lines = ["\x1b[32mboot\x1b[0m", "normal line", "\x1b[2J\x1b[Hclear"]
        result = format_log_content(lines)
        plain = result.plain
        assert "\x1b" not in plain
        assert "boot" in plain
        assert "normal line" in plain
        assert "clear" in plain
