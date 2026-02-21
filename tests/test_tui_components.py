"""Tests for tui/components.py - Rich renderables."""

from io import StringIO
from pathlib import Path

from rich.console import Console

from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.tui.components import (
    build_log_panel,
    build_status_bar,
    build_task_table,
)


def _render(renderable) -> str:
    """Render a Rich object to a plain string (no ANSI)."""
    buf = StringIO()
    console = Console(file=buf, width=100, force_terminal=True, no_color=True)
    console.print(renderable)
    return buf.getvalue()


def _make_task(
    id: str = "abcd1234-0000-0000-0000-000000000000",
    description: str = "Run tests",
    status: TaskStatus = TaskStatus.PENDING,
    slot: int = 1,
) -> Task:
    return Task(
        id=id,
        description=description,
        status=status,
        slot=slot,
        repo_path=Path("/tmp/fake"),
    )


# --- build_task_table ---


class TestBuildTaskTable:
    def test_empty_table(self):
        output = _render(build_task_table([], selected_index=0))
        assert "Tasks" in output

    def test_single_task_displayed(self):
        tasks = [_make_task()]
        output = _render(build_task_table(tasks, selected_index=0))
        assert "abcd1234" in output
        assert "PENDING" in output
        assert "Run tests" in output

    def test_selected_row_has_marker(self):
        tasks = [_make_task(), _make_task(id="beef5678-0000-0000-0000-000000000000", slot=2)]
        output = _render(build_task_table(tasks, selected_index=0))
        lines = output.splitlines()
        # The selected row (index 0) should have a ">" marker
        row_lines = [l for l in lines if "abcd1234" in l]
        assert any(">" in l for l in row_lines)
        # The non-selected row should not have ">"
        other_lines = [l for l in lines if "beef5678" in l]
        assert all(">" not in l for l in other_lines)

    def test_status_values_shown(self):
        tasks = [
            _make_task(id="aaaa0000-0000-0000-0000-000000000000", status=TaskStatus.RUNNING),
            _make_task(id="bbbb0000-0000-0000-0000-000000000000", status=TaskStatus.COMPLETED),
            _make_task(id="cccc0000-0000-0000-0000-000000000000", status=TaskStatus.FAILED),
        ]
        output = _render(build_task_table(tasks, selected_index=0))
        assert "RUNNING" in output
        assert "COMPLETED" in output
        assert "FAILED" in output

    def test_slot_numbers(self):
        tasks = [
            _make_task(slot=3),
            _make_task(id="beef5678-0000-0000-0000-000000000000", slot=7),
        ]
        output = _render(build_task_table(tasks, selected_index=1))
        assert "3" in output
        assert "7" in output


# --- build_log_panel ---


class TestBuildLogPanel:
    def test_with_lines(self):
        lines = ["booting kernel...", "login: root", "# ready"]
        output = _render(build_log_panel(lines, task_id="abcd1234-full-uuid"))
        assert "abcd1234" in output
        assert "booting kernel..." in output
        assert "# ready" in output

    def test_empty_lines(self):
        output = _render(build_log_panel([], task_id="abcd1234"))
        assert "no output" in output

    def test_no_task_id(self):
        output = _render(build_log_panel(["hello"]))
        assert "Log" in output
        assert "hello" in output

    def test_custom_title(self):
        output = _render(build_log_panel(["data"], title="Server Log"))
        assert "Server Log" in output
        assert "data" in output

    def test_title_overrides_task_id(self):
        output = _render(
            build_log_panel(["x"], task_id="abcd1234", title="Custom")
        )
        assert "Custom" in output

    def test_force_jsonl_passed_through(self):
        """force_jsonl=True causes JSONL parsing even with plain first line."""
        import json

        lines = [
            "boot message",
            json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
        ]
        output = _render(build_log_panel(lines, title="Claude", force_jsonl=True))
        assert "Claude" in output
        assert "Hello" in output
        assert "boot message" in output

    def test_focus_search_and_scroll_metadata(self):
        output = _render(
            build_log_panel(
                ["line1"],
                title="Server Log",
                focused=True,
                search_query="warn",
                search_current=2,
                search_total=5,
                scroll_offset=8,
            )
        )
        assert "FOCUS" in output
        assert "search:2/5" in output
        assert "scroll:+8" in output

    def test_search_mode_prompt_in_title(self):
        output = _render(
            build_log_panel(
                ["line1"],
                title="Log",
                search_mode=True,
                search_buffer="error",
            )
        )
        assert "/error_" in output


# --- build_status_bar ---


class TestBuildStatusBar:
    def test_contains_keybindings(self):
        output = _render(build_status_bar())
        assert "[q]" in output
        assert "uit" in output
        assert "[j/k]" in output
        assert "nav" in output
        assert "[1-9]" in output
        assert "select" in output
        assert "[tab]" in output
        assert "switch" in output
        assert "[left/right]" in output
        assert "focus pane" in output
        assert "[pgup/pgdn]" in output
        assert "search" in output
        assert "match" in output

    def test_shows_focus_and_search_prompt(self):
        output = _render(
            build_status_bar(
                active_tab="task",
                focused_pane="log",
                search_mode=True,
                search_buffer="timeout",
            )
        )
        assert "pane:log" in output
        assert "/timeout_" in output

    def test_task_tab_active(self):
        output = _render(build_status_bar(active_tab="task"))
        assert "VM Log" in output
        assert "Claude" in output
        assert "Server Log" in output

    def test_server_tab_active(self):
        output = _render(build_status_bar(active_tab="server"))
        assert "VM Log" in output
        assert "Claude" in output
        assert "Server Log" in output

    def test_claude_tab_active(self):
        output = _render(build_status_bar(active_tab="claude"))
        assert "VM Log" in output
        assert "Claude" in output
        assert "Server Log" in output

    def test_all_three_tabs_present(self):
        """Status bar shows all three tab labels."""
        output = _render(build_status_bar(active_tab="task"))
        assert "VM Log" in output
        assert "Claude" in output
        assert "Server Log" in output
