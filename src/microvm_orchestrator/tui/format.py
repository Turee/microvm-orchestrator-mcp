"""Parse and format Claude JSONL stream events for Rich display."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from rich.text import Text

ClaudeViewMode = Literal["compact", "standard", "full"]

# Strip ANSI escape sequences (CSI, OSC, etc.) and C0 control chars except \n \t
_ANSI_RE = re.compile(r"\x1b[\[\]()#;?]*[0-9;]*[A-Za-z@`]|\x1b[()][AB012]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NOISE_TYPES = {"message_start", "message_stop", "message_delta", "content_block_stop", "ping"}


@dataclass(slots=True)
class ParsedEvent:
    """Normalized event emitted from a Claude stream JSONL line."""

    kind: str
    text: str
    style: str = ""
    raw: Any = None
    is_error: bool = False
    message_id: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None


@dataclass(slots=True)
class ClaudeParseState:
    """State carried across parsed lines for tool call/result correlation."""

    tool_names: dict[str, str] = field(default_factory=dict)


def _sanitize_line(line: str) -> str:
    """Strip ANSI escapes and control characters from a log line."""
    line = _ANSI_RE.sub("", line)
    return _CTRL_RE.sub("", line)


def _truncate(text: str, limit: int) -> str:
    """Truncate text to a fixed limit while preserving readability."""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[:limit - 3]}..."


def _safe_dumps(value: Any) -> str:
    """Serialize arbitrary values to a compact JSON-ish string."""
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _stringify_tool_content(content: Any) -> str:
    """Normalize tool result payloads into displayable text."""
    if isinstance(content, str):
        return _sanitize_line(content)

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(_sanitize_line(item))
                continue
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(_sanitize_line(item["text"]))
                else:
                    parts.append(_sanitize_line(_safe_dumps(item)))
                continue
            parts.append(_sanitize_line(str(item)))
        return "\n".join(part for part in parts if part)

    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return _sanitize_line(content["text"])
        return _sanitize_line(_safe_dumps(content))

    return _sanitize_line(str(content))


def _render_raw_payload(payload: Any, *, mode: ClaudeViewMode) -> str:
    """Render a raw payload line for debug view."""
    text = payload if isinstance(payload, str) else _safe_dumps(payload)
    limit = 240 if mode != "full" else 1200
    return _truncate(_sanitize_line(text), limit)


def _should_render_event(event: ParsedEvent, *, mode: ClaudeViewMode, show_thinking: bool) -> bool:
    """Decide whether an event is visible in the current view settings."""
    if event.kind == "thinking":
        return show_thinking

    if mode == "compact":
        return event.kind in {
            "plain",
            "tool_use",
            "tool_result",
            "result",
            "rate_limit",
        }

    if mode == "standard":
        return event.kind not in {"stream_event", "unknown"}

    return True


def _render_event_text(event: ParsedEvent, *, mode: ClaudeViewMode) -> tuple[str, str]:
    """Render a single parsed event into text and Rich style."""
    if event.kind == "plain":
        return (f"{event.text}\n", event.style)

    if event.kind == "legacy_delta_text":
        return (event.text, event.style)

    if event.kind == "assistant_text":
        limit = 900 if mode == "standard" else 3000
        return (f"A: {_truncate(event.text, limit)}\n", event.style)

    if event.kind == "thinking":
        limit = 300 if mode != "full" else 1000
        return (f"THINK: {_truncate(event.text, limit)}\n", event.style or "dim italic")

    if event.kind == "tool_use":
        name = event.tool_name or event.text or "unknown"
        input_limit = 100 if mode == "compact" else 180
        if mode == "full":
            input_limit = 600

        payload = ""
        if event.raw is not None:
            payload = _truncate(_render_raw_payload(event.raw, mode=mode), input_limit)

        line = f"TOOL> {name}"
        if payload:
            line = f"{line}: {payload}"
        return (f"{line}\n", event.style or "bold cyan")

    if event.kind == "tool_result":
        name = event.tool_name or "unknown"
        status = "ERROR" if event.is_error else "OK"

        result_limit = 140 if mode == "compact" else 420
        if mode == "full":
            result_limit = 2000

        line = f"TOOL< {name} [{status}]"
        if event.tool_use_id and name == "unknown":
            line = f"{line} ({event.tool_use_id[:8]})"

        if event.text:
            line = f"{line}: {_truncate(event.text, result_limit)}"

        style = "bold red" if event.is_error else (event.style or "yellow")
        return (f"{line}\n", style)

    if event.kind == "system":
        return (f"SYS: {event.text}\n", event.style or "dim")

    if event.kind == "result":
        divider = "-" * 40
        return (f"\n{divider}\nRESULT: {event.text}\n", event.style or "bold green")

    if event.kind == "rate_limit":
        return (f"LIMIT: {event.text}\n", event.style or "bold yellow")

    if event.kind == "stream_event":
        return (f"STREAM: {event.text}\n", event.style or "dim")

    if event.kind == "unknown":
        return (f"EVENT: {event.text}\n", event.style or "dim")

    return ("", "")


def render_parsed_events(
    events: list[ParsedEvent],
    *,
    mode: ClaudeViewMode = "standard",
    show_thinking: bool = False,
    show_raw: bool = False,
) -> Text:
    """Render normalized parsed events to Rich Text."""
    result = Text()

    for event in events:
        if not _should_render_event(event, mode=mode, show_thinking=show_thinking):
            continue

        rendered, style = _render_event_text(event, mode=mode)
        if not rendered:
            continue

        result.append(rendered, style=style or None)

        if show_raw and event.raw is not None and event.kind not in {"plain", "legacy_delta_text"}:
            result.append(
                f"    raw: {_render_raw_payload(event.raw, mode=mode)}\n",
                style="dim",
            )

    return result


def _parse_legacy_event(event: dict[str, Any], state: ClaudeParseState) -> list[ParsedEvent]:
    """Parse legacy content_block_* style stream events."""
    event_type = str(event.get("type", ""))

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if not isinstance(delta, dict):
            return []
        if delta.get("type") == "text_delta":
            text = _sanitize_line(str(delta.get("text", "")))
            return [ParsedEvent(kind="legacy_delta_text", text=text)] if text else []
        return []

    if event_type == "content_block_start":
        block = event.get("content_block", {})
        if not isinstance(block, dict):
            return []
        if block.get("type") != "tool_use":
            return []

        tool_name = str(block.get("name", "unknown"))
        tool_use_id = str(block.get("id", "")) or None
        if tool_use_id:
            state.tool_names[tool_use_id] = tool_name

        return [
            ParsedEvent(
                kind="tool_use",
                text=tool_name,
                style="bold cyan",
                raw=block.get("input"),
                tool_name=tool_name,
                tool_use_id=tool_use_id,
            )
        ]

    return []


def parse_jsonl_line(line: str, state: ClaudeParseState | None = None) -> list[ParsedEvent]:
    """Parse one stream JSONL line into normalized events."""
    stripped = line.strip()
    if not stripped:
        return []

    parse_state = state or ClaudeParseState()

    if not stripped.startswith("{"):
        return [ParsedEvent(kind="plain", text=_sanitize_line(stripped))]

    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return [ParsedEvent(kind="plain", text=_sanitize_line(stripped))]

    if not isinstance(event, dict):
        return [ParsedEvent(kind="plain", text=_sanitize_line(stripped))]

    event_type = str(event.get("type", ""))

    # Legacy stream-json format support.
    if event_type in {"content_block_delta", "content_block_start"}:
        return _parse_legacy_event(event, parse_state)

    if event_type in _NOISE_TYPES:
        return []

    if event_type == "system":
        subtype = str(event.get("subtype", ""))
        if subtype == "init":
            model = str(event.get("model", ""))
            session_id = str(event.get("session_id", ""))
            session_short = session_id[:8] if session_id else ""
            parts = [part for part in [model, session_short] if part]
            text = " | ".join(parts) if parts else "init"
        else:
            text = subtype or "system"
        return [ParsedEvent(kind="system", text=text, style="dim", raw=event)]

    if event_type == "assistant":
        message = event.get("message", {})
        if not isinstance(message, dict):
            return []

        message_id = str(message.get("id", "")) or None
        content = message.get("content", [])
        if not isinstance(content, list):
            return []

        parsed: list[ParsedEvent] = []
        for item in content:
            if not isinstance(item, dict):
                continue

            content_type = item.get("type")
            if content_type == "text":
                text = _sanitize_line(str(item.get("text", ""))).strip()
                if text:
                    parsed.append(
                        ParsedEvent(
                            kind="assistant_text",
                            text=text,
                            message_id=message_id,
                            raw=item,
                        )
                    )
                continue

            if content_type == "thinking":
                thinking = _sanitize_line(str(item.get("thinking", ""))).strip()
                if thinking:
                    parsed.append(
                        ParsedEvent(
                            kind="thinking",
                            text=thinking,
                            style="dim italic",
                            message_id=message_id,
                            raw=item,
                        )
                    )
                continue

            if content_type == "tool_use":
                tool_name = str(item.get("name", "unknown"))
                tool_use_id = str(item.get("id", "")) or None
                if tool_use_id:
                    parse_state.tool_names[tool_use_id] = tool_name

                parsed.append(
                    ParsedEvent(
                        kind="tool_use",
                        text=tool_name,
                        style="bold cyan",
                        raw=item.get("input"),
                        message_id=message_id,
                        tool_use_id=tool_use_id,
                        tool_name=tool_name,
                    )
                )

        return parsed

    if event_type == "user":
        message = event.get("message", {})
        if not isinstance(message, dict):
            return []

        content = message.get("content", [])
        if not isinstance(content, list):
            return []

        parsed: list[ParsedEvent] = []
        for item in content:
            if not isinstance(item, dict):
                continue

            if item.get("type") != "tool_result":
                continue

            tool_use_id = str(item.get("tool_use_id", "")) or None
            tool_name = parse_state.tool_names.get(tool_use_id or "", "unknown")
            payload = _stringify_tool_content(item.get("content", ""))
            is_error = bool(item.get("is_error", False))

            parsed.append(
                ParsedEvent(
                    kind="tool_result",
                    text=payload,
                    style="yellow",
                    raw=item,
                    is_error=is_error,
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                )
            )

        return parsed

    if event_type == "result":
        subtype = str(event.get("subtype", ""))
        result_text = _sanitize_line(str(event.get("result", "")).strip())

        meta: list[str] = []
        cost = event.get("cost_usd")
        duration = event.get("duration_ms")

        if isinstance(cost, (int, float)):
            meta.append(f"${cost:.4f}")
        if isinstance(duration, (int, float)):
            meta.append(f"{duration / 1000:.1f}s")

        text = result_text or "(no result text)"
        if meta:
            text = f"{text} [{', '.join(meta)}]"

        is_error = subtype == "error" or bool(event.get("is_error", False))
        style = "bold red" if is_error else "bold green"
        return [ParsedEvent(kind="result", text=text, style=style, raw=event, is_error=is_error)]

    if event_type == "rate_limit_event":
        info = event.get("rate_limit_info", {})
        if not isinstance(info, dict):
            return [ParsedEvent(kind="rate_limit", text="rate limit event", style="bold yellow", raw=event)]

        status = str(info.get("status", ""))
        limit_type = str(info.get("rateLimitType", ""))
        resets_at = str(info.get("resetsAt", ""))
        fragments = [fragment for fragment in [status, limit_type] if fragment]
        text = " ".join(fragments) if fragments else "rate limit"
        if resets_at:
            text = f"{text} (reset={resets_at})"

        style = "bold red" if status == "rejected" else "bold yellow"
        return [ParsedEvent(kind="rate_limit", text=text, style=style, raw=event)]

    if event_type == "stream_event":
        stream_name = str(event.get("event", "stream_event"))
        return [ParsedEvent(kind="stream_event", text=stream_name, style="dim", raw=event)]

    return [ParsedEvent(kind="unknown", text=event_type or "<unknown>", style="dim", raw=event)]


def format_jsonl_line(line: str) -> tuple[str, str] | None:
    """Backward-compatible single-line formatter.

    This helper keeps the old tuple return signature for existing call sites,
    while internally using the normalized parser/renderer pipeline.
    """
    events = parse_jsonl_line(line, ClaudeParseState())
    rendered = render_parsed_events(events)
    if not rendered.plain:
        return None

    style = ""
    for event in events:
        if event.style:
            style = event.style
            break

    return (rendered.plain, style)


def format_log_content(
    raw_lines: list[str],
    *,
    force_jsonl: bool = False,
    mode: ClaudeViewMode = "standard",
    show_thinking: bool = False,
    show_raw: bool = False,
) -> Text:
    """Convert raw log lines (possibly JSONL) into styled Rich Text."""
    if not raw_lines:
        return Text("(no output)", style="dim italic")

    # Detect JSONL by checking first non-empty line.
    is_jsonl = force_jsonl
    if not is_jsonl:
        for line in raw_lines:
            sample = line.strip()
            if sample:
                is_jsonl = sample.startswith("{")
                break

    if not is_jsonl:
        return Text("\n".join(_sanitize_line(line) for line in raw_lines))

    state = ClaudeParseState()
    events: list[ParsedEvent] = []
    for line in raw_lines:
        events.extend(parse_jsonl_line(line, state))

    rendered = render_parsed_events(
        events,
        mode=mode,
        show_thinking=show_thinking,
        show_raw=show_raw,
    )
    if not rendered.plain:
        return Text("(no output)", style="dim italic")
    return rendered
