"""Tests for tui/format.py - Claude stream timeline formatting."""

from __future__ import annotations

import json

from microvm_orchestrator.tui.format import (
    ClaudeParseState,
    _sanitize_line,
    format_jsonl_line,
    format_log_content,
    parse_jsonl_line,
    render_parsed_events,
)


def _jsonl(*events: dict) -> list[str]:
    """Convert dicts to JSONL lines."""
    return [json.dumps(event) for event in events]


class TestParseJsonlLine:
    def test_assistant_tool_use_records_state(self):
        state = ClaudeParseState()
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_1",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_123",
                                "name": "Bash",
                                "input": {"command": "bun test"},
                            }
                        ],
                    },
                }
            ),
            state,
        )

        assert len(events) == 1
        assert events[0].kind == "tool_use"
        assert events[0].tool_name == "Bash"
        assert state.tool_names["toolu_123"] == "Bash"

    def test_user_tool_result_uses_recorded_tool_name(self):
        state = ClaudeParseState(tool_names={"toolu_123": "Bash"})
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_123",
                                "content": "Exit code 1",
                                "is_error": True,
                            }
                        ]
                    },
                }
            ),
            state,
        )

        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert events[0].tool_name == "Bash"
        assert events[0].is_error is True
        assert "Exit code 1" in events[0].text

    def test_plain_text_passthrough(self):
        events = parse_jsonl_line("devShell ready")
        assert len(events) == 1
        assert events[0].kind == "plain"
        assert events[0].text == "devShell ready"

    def test_invalid_json_passthrough(self):
        events = parse_jsonl_line("{broken json")
        assert len(events) == 1
        assert events[0].kind == "plain"
        assert events[0].text == "{broken json"

    def test_rate_limit_event(self):
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "rejected",
                        "rateLimitType": "five_hour",
                        "resetsAt": 1771848000,
                    },
                }
            )
        )
        assert len(events) == 1
        assert events[0].kind == "rate_limit"
        assert "five_hour" in events[0].text

    def test_legacy_delta_still_parsed(self):
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hello"},
                }
            )
        )
        assert len(events) == 1
        assert events[0].kind == "legacy_delta_text"
        assert events[0].text == "Hello"


class TestRenderParsedEvents:
    def test_standard_hides_thinking(self):
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "private"},
                            {"type": "text", "text": "public"},
                        ]
                    },
                }
            ),
            ClaudeParseState(),
        )

        rendered = render_parsed_events(events, mode="standard", show_thinking=False)
        assert "public" in rendered.plain
        assert "private" not in rendered.plain

    def test_thinking_toggle_shows_thinking(self):
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "thinking", "thinking": "private"}],
                    },
                }
            ),
            ClaudeParseState(),
        )

        rendered = render_parsed_events(events, mode="standard", show_thinking=True)
        assert "THINK:" in rendered.plain
        assert "private" in rendered.plain

    def test_compact_hides_assistant_text(self):
        events = parse_jsonl_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "assistant body"}],
                    },
                }
            ),
            ClaudeParseState(),
        )

        rendered = render_parsed_events(events, mode="compact")
        assert rendered.plain == ""

    def test_full_shows_unknown_event(self):
        events = parse_jsonl_line(json.dumps({"type": "message_custom", "payload": 1}))
        rendered = render_parsed_events(events, mode="full")
        assert "EVENT:" in rendered.plain
        assert "message_custom" in rendered.plain


class TestFormatJsonlLineCompatibility:
    def test_returns_none_for_noise(self):
        assert format_jsonl_line(json.dumps({"type": "message_start"})) is None

    def test_renders_assistant_text(self):
        rendered = format_jsonl_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                }
            )
        )
        assert rendered is not None
        text, _style = rendered
        assert "A: hello" in text


class TestFormatLogContent:
    def test_empty_lines(self):
        result = format_log_content([])
        assert "no output" in result.plain

    def test_plain_text_passthrough(self):
        result = format_log_content(["hello", "world"])
        assert result.plain == "hello\nworld"

    def test_force_jsonl_renders_activity_timeline(self):
        lines = _jsonl(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Bash",
                            "input": {"command": "bun test"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "Exit code 1",
                            "is_error": True,
                        }
                    ]
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "Done",
                "cost_usd": 0.5,
            },
        )
        result = format_log_content(lines, force_jsonl=True)

        plain = result.plain
        assert "TOOL> Bash" in plain
        assert "TOOL< Bash [ERROR]" in plain
        assert "RESULT:" in plain
        assert "$0.5000" in plain

    def test_mixed_plain_and_json_force_jsonl(self):
        lines = [
            "Using devShell target: path:.",
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "Running tests"}],
                    },
                }
            ),
        ]
        result = format_log_content(lines, force_jsonl=True)
        assert "Using devShell target: path:." in result.plain
        assert "A: Running tests" in result.plain

    def test_force_jsonl_false_preserves_autodetect(self):
        lines = ["plain first", json.dumps({"type": "assistant", "message": {"content": []}})]
        result = format_log_content(lines, force_jsonl=False)
        assert "plain first" in result.plain


class TestSanitizeLine:
    def test_strips_ansi_color_codes(self):
        assert _sanitize_line("\x1b[31mred\x1b[0m") == "red"

    def test_strips_cursor_movement(self):
        assert _sanitize_line("\x1b[2Jhello\x1b[H") == "hello"

    def test_strips_control_characters(self):
        # BEL (\x07), BS (\x08), etc. are stripped; \n and \t are kept.
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
        """ANSI escapes in plain-text output are stripped."""
        lines = ["\x1b[32mboot\x1b[0m", "normal line", "\x1b[2J\x1b[Hclear"]
        result = format_log_content(lines)
        plain = result.plain
        assert "\x1b" not in plain
        assert "boot" in plain
        assert "normal line" in plain
        assert "clear" in plain
