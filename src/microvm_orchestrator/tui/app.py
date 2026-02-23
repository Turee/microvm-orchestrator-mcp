"""Textual TUI app for monitoring orchestrator tasks and logs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, RichLog, Static, Tab, Tabs

from ..core.task import TaskStatus
from .format import format_jsonl_line, format_log_content
from .log_capture import LogCapture
from .tail import LogTailer
from .term_screen import TermScreen

if TYPE_CHECKING:
    from pathlib import Path

    from ..core.task import Task
    from ..tools import Orchestrator


logger = logging.getLogger(__name__)

_LOG_SOURCES: tuple[str, ...] = ("task", "claude", "server")
_LOG_LABELS: dict[str, str] = {
    "task": "VM Log",
    "claude": "Claude",
    "server": "Server Log",
}
_MAX_LOG_LINES_PER_REFRESH = 200


class TUIApp(App[None]):
    """Textual UI for live task and log monitoring."""

    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "next_log_source", "Source"),
        Binding("shift+tab", "prev_log_source", "Prev source", show=False),
        Binding("f", "toggle_follow", "Follow"),
        Binding("ctrl+r", "reset_layout", "Reset"),
        Binding("enter", "show_description", "Describe"),
        Binding("l", "focus_log", "Focus log", show=False),
        Binding("t", "focus_tasks", "Focus tasks", show=False),
        Binding("c", "clear_log_view", "Clear"),
        Binding("end", "jump_latest", "Latest"),
        Binding("escape", "close_description", "Back", show=False),
    ]

    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        log_capture: LogCapture | None = None,
    ) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._log_capture = log_capture
        self._selected_index = 0
        self._active_source = "task"
        self._follow_logs = True
        self._tasks: list[Task] = []
        self._tailers: dict[str, LogTailer] = {}
        self._term_screens: dict[str, TermScreen] = {}
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total: int = 0
        self._rendered_generation: int = 0
        self._description_preview_task_id: str | None = None
        self._refreshing_logs = False
        self._programmatic_update = False
        self._row_task_ids: list[str] = []
        self._col_keys: list[object] = []
        self._row_task_state: dict[str, tuple[TaskStatus, int]] = {}

    def compose(self) -> ComposeResult:
        """Compose the Textual widget tree."""
        yield Header(show_clock=True)
        with Horizontal(id="main-pane"):
            with Vertical(id="tasks-pane"):
                yield Static("Tasks", id="tasks-title")
                yield DataTable(id="task-table")
            with Vertical(id="logs-pane"):
                yield Tabs(
                    Tab("VM Log", id="source-task"),
                    Tab("Claude", id="source-claude"),
                    Tab("Server Log", id="source-server"),
                    id="log-tabs",
                )
                yield Static("follow:on", id="log-title")
                yield RichLog(id="log-view", wrap=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        """Configure widgets and start refresh timers."""
        table = self.query_one("#task-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        self._col_keys = [
            table.add_column("#", width=2),
            table.add_column("ID", width=8),
            table.add_column("Status", width=9),
            table.add_column("Slot", width=4),
        ]
        table.focus()

        self.query_one("#log-tabs", Tabs).active = f"source-{self._active_source}"

        log_view = self.query_one("#log-view", RichLog)
        log_view.auto_scroll = self._follow_logs
        log_view.max_lines = 1500

        self._refresh_tasks()
        self._refresh_logs()
        self.set_interval(0.25, self._refresh_tasks)
        self.set_interval(0.20, self._refresh_logs)

    def _get_tasks(self) -> list[Task]:
        """Read tasks from the orchestrator (GIL-safe read-only access)."""
        if self._orchestrator is None:
            return []
        return list(self._orchestrator._tasks.values())

    def _status_cell(self, status: TaskStatus) -> Text:
        """Render task status with compact semantic coloring."""
        style = {
            TaskStatus.PENDING: "yellow",
            TaskStatus.RUNNING: "green",
            TaskStatus.COMPLETED: "bright_black",
            TaskStatus.FAILED: "red",
        }.get(status, "")
        return Text(status.value.upper(), style=style)

    def _refresh_tasks(self) -> None:
        """Refresh task table data while preserving cursor selection."""
        try:
            tasks = self._get_tasks()
            self._tasks = tasks
            task_ids = [task.id for task in tasks]
            if tasks:
                self._selected_index = max(0, min(self._selected_index, len(tasks) - 1))
            else:
                self._selected_index = 0

            table = self.query_one("#task-table", DataTable)
            structural_change = task_ids != self._row_task_ids
            if structural_change:
                self._programmatic_update = True
                try:
                    table.clear(columns=False)
                    for i, task in enumerate(tasks):
                        table.add_row(
                            str(i + 1),
                            task.id[:8],
                            self._status_cell(task.status),
                            str(task.slot),
                            key=task.id,
                        )
                finally:
                    self._programmatic_update = False
                self._row_task_ids = task_ids
                self._row_task_state = {task.id: (task.status, task.slot) for task in tasks}
            elif self._col_keys:
                status_column = self._col_keys[2] if len(self._col_keys) > 2 else None
                slot_column = self._col_keys[3] if len(self._col_keys) > 3 else None
                self._programmatic_update = True
                try:
                    for task in tasks:
                        previous_state = self._row_task_state.get(task.id)
                        current_state = (task.status, task.slot)
                        if previous_state == current_state:
                            continue
                        if status_column is not None:
                            table.update_cell(task.id, status_column, self._status_cell(task.status))
                        if slot_column is not None:
                            table.update_cell(task.id, slot_column, str(task.slot))
                        self._row_task_state[task.id] = current_state
                finally:
                    self._programmatic_update = False

            if tasks:
                try:
                    cursor_row = getattr(table, "cursor_row", None)
                    if cursor_row != self._selected_index:
                        self._programmatic_update = True
                        try:
                            table.move_cursor(row=self._selected_index, column=0, animate=False)
                        finally:
                            self._programmatic_update = False
                except Exception:
                    pass
        except Exception:
            logger.exception("TUI task refresh error")

    def _get_tailer(self, source_key: str, path: Path) -> LogTailer:
        """Get or create a tailer for the given source key/path pair."""
        tailer = self._tailers.get(source_key)
        if tailer is None or tailer.path != path:
            tailer = LogTailer(path, maxlen=1000)
            self._tailers[source_key] = tailer
        return tailer

    def _get_term_screen(self, source_key: str, path: Path) -> TermScreen:
        """Get or create a TermScreen for the given source key/path pair."""
        term = self._term_screens.get(source_key)
        if term is None or term.path != path:
            cols, rows = self._log_view_size()
            term = TermScreen(path, columns=cols, lines=rows)
            self._term_screens[source_key] = term
        return term

    def _log_view_size(self) -> tuple[int, int]:
        """Return (columns, lines) of the log-view widget, with sane defaults."""
        try:
            log_view = self.query_one("#log-view", RichLog)
            w, h = log_view.size.width, log_view.size.height
            return (w if w > 0 else 120, h if h > 0 else 50)
        except Exception:
            return (120, 50)

    def _resolve_log_source(self) -> tuple[str, str, list[str], bool, LogTailer | None, TermScreen | None]:
        """Resolve active source metadata and lines to display.

        Returns (source_key, title, lines, force_jsonl, tailer, term_screen).
        For the "task" source, *term_screen* is set and lines/tailer are empty/None.
        For other sources, *term_screen* is None.
        """
        selected_task = self._tasks[self._selected_index] if self._tasks else None

        if self._active_source == "server":
            lines = self._log_capture.get_lines() if self._log_capture else []
            return ("server", _LOG_LABELS["server"], lines, False, None, None)

        if self._active_source == "claude":
            if not selected_task:
                return ("claude:none", _LOG_LABELS["claude"], [], True, None, None)
            source_key = f"claude:{selected_task.id}"
            tailer = self._get_tailer(source_key, selected_task.stream_log_path)
            tailer.poll()
            return (source_key, _LOG_LABELS["claude"], tailer.get_lines(), True, tailer, None)

        # "task" source — use TermScreen for proper ANSI rendering
        if not selected_task:
            return ("task:none", _LOG_LABELS["task"], [], False, None, None)
        source_key = f"task:{selected_task.id}"
        term = self._get_term_screen(source_key, selected_task.log_path)
        term.poll()
        return (source_key, _LOG_LABELS["task"], [], False, None, term)

    def _write_lines(self, log_view: RichLog, lines: list[str]) -> None:
        """Write plain text lines to RichLog as a single batch."""
        if lines:
            log_view.write(Text("\n".join(lines)))

    def _write_jsonl_delta(self, log_view: RichLog, lines: list[str]) -> bool:
        """Append parsed JSONL delta lines without rebuilding full panel."""
        rendered = Text()
        for line in lines:
            parsed = format_jsonl_line(line)
            if parsed is None:
                continue
            text, style = parsed
            rendered.append(text, style=style or None)
        if rendered.plain:
            log_view.write(rendered)
            return True
        return False

    def _refresh_logs(self) -> None:
        """Refresh log panel for active source using incremental rendering.

        For tailer-backed sources we track the tailer's monotonic
        ``total_appended`` counter so that new content is detected even
        after the underlying deque wraps (i.e. ``len()`` stops growing).
        For non-tailer sources (server log) we fall back to ``len(lines)``.
        For the "task" source, a TermScreen handles ANSI rendering via pyte.
        """
        if self._refreshing_logs:
            return
        self._refreshing_logs = True
        try:
            content_changed = False
            selected_task = self._tasks[self._selected_index] if self._tasks else None
            source_key, _title, lines, force_jsonl, tailer, term = self._resolve_log_source()
            log_view = self.query_one("#log-view", RichLog)

            if self._description_preview_task_id and selected_task:
                if self._description_preview_task_id != selected_task.id:
                    self._description_preview_task_id = selected_task.id

                self.query_one("#log-title", Static).update(
                    f"description | {selected_task.id[:8]} | esc to close"
                )
                log_view.clear()
                log_view.write(Markdown(selected_task.description or "_(no description)_"))
                content_changed = True
                self._last_source_key = "description"
                self._rendered_line_count = 0
                self._rendered_total = 0
                self._rendered_generation = 0
                return

            self.query_one("#log-title", Static).update(
                "follow:on" if self._follow_logs else "follow:off"
            )

            source_changed = source_key != self._last_source_key
            if source_changed:
                log_view.clear()
                content_changed = True
                self._rendered_line_count = 0
                self._rendered_total = 0
                self._rendered_generation = 0

            # TermScreen path: clear + rewrite on each new generation
            if term is not None:
                if term.generation != self._rendered_generation or source_changed:
                    log_view.clear()
                    rendered = term.render()
                    if rendered.plain:
                        log_view.write(rendered)
                    elif source_changed:
                        log_view.write("(no output)")
                    self._rendered_generation = term.generation
                    content_changed = True
                self._last_source_key = source_key
                if self._follow_logs and content_changed:
                    log_view.scroll_end(animate=False)
                return

            # Tailer / plain-text path
            total = tailer.total_appended if tailer else len(lines)
            new_count = total - self._rendered_total

            if new_count < 0:
                log_view.clear()
                self._rendered_line_count = 0
                self._rendered_total = 0
                new_count = total
                content_changed = True

            if new_count == 0 and not source_changed:
                pass
            elif force_jsonl:
                if source_changed and not lines:
                    log_view.write("(no output)")
                    content_changed = True
                elif new_count > len(lines):
                    log_view.clear()
                    chunk = lines[-_MAX_LOG_LINES_PER_REFRESH:]
                    content_changed = self._write_jsonl_delta(log_view, chunk) or content_changed
                else:
                    cap = min(new_count, _MAX_LOG_LINES_PER_REFRESH)
                    delta = lines[-cap:]
                    content_changed = self._write_jsonl_delta(log_view, delta) or content_changed
                self._rendered_total = total
                self._rendered_line_count = len(lines)
            else:
                if source_changed and not lines:
                    log_view.write("(no output)")
                    content_changed = True
                elif new_count > len(lines):
                    log_view.clear()
                    chunk = lines[-_MAX_LOG_LINES_PER_REFRESH:]
                    self._write_lines(log_view, chunk)
                    content_changed = bool(chunk)
                elif new_count > 0:
                    cap = min(new_count, _MAX_LOG_LINES_PER_REFRESH)
                    delta = lines[-cap:]
                    self._write_lines(log_view, delta)
                    content_changed = bool(delta)
                self._rendered_total = total
                self._rendered_line_count = len(lines)

            self._last_source_key = source_key
            if self._follow_logs and content_changed:
                log_view.scroll_end(animate=False)
        except Exception:
            logger.exception("TUI log refresh error")
        finally:
            self._refreshing_logs = False

    def on_resize(self, event: events.Resize) -> None:
        """Resize pyte screens to match the log pane dimensions."""
        try:
            cols, rows = self._log_view_size()
            for term in self._term_screens.values():
                term.resize(columns=cols, lines=rows)
        except Exception:
            pass

    def _set_selected_row(self, row_index: int) -> None:
        """Update selected row and force log refresh on task change."""
        if not self._tasks:
            self._selected_index = 0
            return
        self._selected_index = max(0, min(row_index, len(self._tasks) - 1))
        if self._active_source != "server":
            self._last_source_key = ""
            self._rendered_line_count = 0
            self._rendered_total = 0
            self._rendered_generation = 0
            self._refresh_logs()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Sync selected task with highlighted DataTable row."""
        if self._programmatic_update:
            return
        row_index = getattr(event, "cursor_row", None)
        if isinstance(row_index, int):
            self._set_selected_row(row_index)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Sync selected task with selected DataTable row."""
        if self._programmatic_update:
            return
        row_index = getattr(event, "cursor_row", None)
        if isinstance(row_index, int):
            self._set_selected_row(row_index)

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Switch log source when a tab is clicked or activated."""
        if event.tab is None:
            return
        source = event.tab.id.removeprefix("source-")
        if source == self._active_source:
            return
        self._active_source = source
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total = 0
        self._rendered_generation = 0
        self._refresh_logs()

    def action_next_log_source(self) -> None:
        """Cycle to the next log source tab."""
        index = _LOG_SOURCES.index(self._active_source)
        self._active_source = _LOG_SOURCES[(index + 1) % len(_LOG_SOURCES)]
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total = 0
        self._rendered_generation = 0
        self.query_one("#log-tabs", Tabs).active = f"source-{self._active_source}"
        self._refresh_logs()

    def action_prev_log_source(self) -> None:
        """Cycle to the previous log source tab."""
        index = _LOG_SOURCES.index(self._active_source)
        self._active_source = _LOG_SOURCES[(index - 1) % len(_LOG_SOURCES)]
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total = 0
        self._rendered_generation = 0
        self.query_one("#log-tabs", Tabs).active = f"source-{self._active_source}"
        self._refresh_logs()

    def action_toggle_follow(self) -> None:
        """Toggle auto-follow behavior for log scrolling."""
        self._follow_logs = not self._follow_logs
        log_view = self.query_one("#log-view", RichLog)
        log_view.auto_scroll = self._follow_logs
        if self._follow_logs:
            log_view.scroll_end(animate=False)
        self._refresh_logs()

    def action_focus_tasks(self) -> None:
        """Focus the task table pane."""
        self.query_one("#task-table", DataTable).focus()

    def action_focus_log(self) -> None:
        """Focus the log pane."""
        self.query_one("#log-view", RichLog).focus()

    def action_clear_log_view(self) -> None:
        """Clear currently rendered log content."""
        self.query_one("#log-view", RichLog).clear()
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total = 0
        self._rendered_generation = 0

    def action_jump_latest(self) -> None:
        """Jump log viewport to the latest entries."""
        log_view = self.query_one("#log-view", RichLog)
        log_view.scroll_end(animate=False)

    def action_show_description(self) -> None:
        """Show selected task description in markdown format."""
        selected_task = self._tasks[self._selected_index] if self._tasks else None
        if selected_task is None:
            return
        self._description_preview_task_id = selected_task.id
        self._refresh_logs()

    def action_close_description(self) -> None:
        """Close description preview and return to live logs."""
        if self._description_preview_task_id is None:
            return
        self._description_preview_task_id = None
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total = 0
        self._rendered_generation = 0
        self._refresh_logs()

    def action_reset_layout(self) -> None:
        """Reset UI layout state after maximize or preview actions."""
        self.screen.minimize()
        self._description_preview_task_id = None
        self._last_source_key = ""
        self._rendered_line_count = 0
        self._rendered_total = 0
        self._rendered_generation = 0
        self._refresh_logs()
        self.action_focus_tasks()
