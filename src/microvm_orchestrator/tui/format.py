"""Parse and format Claude JSONL stream-json events for Rich display."""

from __future__ import annotations

import json

from rich.text import Text


def format_jsonl_line(line: str) -> tuple[str, str] | None:
    """Parse a single Claude stream-json line.

    Returns:
        (text, rich_style) for displayable events, or None to skip.
    """
    stripped = line.strip()
    if not stripped:
        return None
    if not stripped.startswith("{"):
        # Plain text (not JSON) — pass through unstyled
        return (stripped, "")

    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return (stripped, "")

    if not isinstance(event, dict):
        return (stripped, "")

    event_type = event.get("type", "")

    if event_type == "content_block_delta":
        return _handle_delta(event)
    if event_type == "content_block_start":
        return _handle_block_start(event)
    if event_type == "result":
        return _handle_result(event)
    if event_type == "system":
        return _handle_system(event)
    # Skip noise: message_start, message_stop, message_delta,
    # content_block_stop, assistant (redundant with deltas), user, ping
    return None


def format_log_content(raw_lines: list[str]) -> Text:
    """Convert raw log lines (possibly JSONL) into styled Rich Text.

    Auto-detects JSONL: if the first non-empty line starts with ``{``,
    all lines are processed as stream-json events.  Otherwise they're
    rendered as plain text.
    """
    if not raw_lines:
        return Text("(no output)", style="dim italic")

    # Detect JSONL by checking first non-empty line
    is_jsonl = False
    for line in raw_lines:
        s = line.strip()
        if s:
            is_jsonl = s.startswith("{")
            break

    if not is_jsonl:
        return Text("\n".join(raw_lines))

    result = Text()
    first = True
    for line in raw_lines:
        parsed = format_jsonl_line(line)
        if parsed is None:
            continue
        text, style = parsed
        if not first and text and not text[0] == "\n":
            # Append without separator for streaming text deltas;
            # newlines within text are preserved naturally
            pass
        result.append(text, style=style or None)
        first = False

    if not result.plain:
        return Text("(no output)", style="dim italic")
    return result


# ── Event handlers ──────────────────────────────────────────────


def _handle_delta(event: dict) -> tuple[str, str] | None:
    delta = event.get("delta", {})
    delta_type = delta.get("type", "")

    if delta_type == "text_delta":
        text = delta.get("text", "")
        return (text, "") if text else None

    if delta_type == "input_json_delta":
        # Tool input fragments — skip (noisy)
        return None

    return None


def _handle_block_start(event: dict) -> tuple[str, str] | None:
    block = event.get("content_block", {})
    if block.get("type") == "tool_use":
        name = block.get("name", "unknown")
        return (f"\n>> Tool: {name}\n", "bold cyan")
    return None


def _handle_result(event: dict) -> tuple[str, str] | None:
    subtype = event.get("subtype", "")
    result_text = event.get("result", "")
    cost = event.get("cost_usd")
    duration = event.get("duration_ms")

    parts: list[str] = []
    if result_text:
        parts.append(result_text)

    meta: list[str] = []
    if cost is not None:
        meta.append(f"${cost:.4f}")
    if duration is not None:
        secs = duration / 1000
        meta.append(f"{secs:.1f}s")
    if meta:
        parts.append(f"[{', '.join(meta)}]")

    if not parts:
        return None

    style = "bold green" if subtype != "error" else "bold red"
    return (f"\n{'─' * 40}\n" + " ".join(parts) + "\n", style)


def _handle_system(event: dict) -> tuple[str, str] | None:
    # Show model/session info on init
    subtype = event.get("subtype", "")
    if subtype == "init":
        model = event.get("model", "")
        session = event.get("session_id", "")[:8] if event.get("session_id") else ""
        parts = [p for p in [model, session] if p]
        if parts:
            return (f"[{' · '.join(parts)}]\n", "dim")
    return None
