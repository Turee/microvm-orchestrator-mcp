"""TUIApp - main loop with Rich Live + Layout."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

from rich.layout import Layout
from rich.live import Live

from .components import _TAB_ORDER, build_log_panel, build_status_bar, build_task_table
from .input import (
    KEY_DOWN,
    KEY_ENTER,
    KEY_ESCAPE,
    KEY_LEFT,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    KEY_RIGHT,
    KEY_TAB,
    KEY_UP,
    InputReader,
)
from .log_capture import LogCapture
from .tail import LogTailer

if TYPE_CHECKING:
    from ..tools import Orchestrator

    from ..core.task import Task


class TUIApp:
    """Full-screen TUI for monitoring orchestrator tasks.

    Uses Rich.Live with a Layout, polling input and refreshing
    at ~10 FPS on the alternate screen buffer.
    """

    def __init__(
        self,
        orchestrator: Optional[Orchestrator] = None,
        log_capture: Optional[LogCapture] = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._log_capture = log_capture
        self._selected: int = 0
        self._tab: str = "task"
        self._focused_pane: str = "tasks"
        self._tailer: Optional[LogTailer] = None
        self._claude_tailer: Optional[LogTailer] = None
        self._log_offsets: dict[str, int] = {}
        self._current_log_source_key: str = "task:none"
        self._current_log_lines: list[str] = []
        self._current_viewport_lines: int = 20
        self._search_mode = False
        self._search_buffer = ""
        self._search_query = ""
        self._search_matches: list[int] = []
        self._search_match_cursor = -1
        self._running = False

    def _estimate_log_viewport(self, layout: Layout) -> int:
        """Estimate the available line count for log content."""
        try:
            region = layout["log"].region
            return max(3, region.height - 2)
        except Exception:
            return 20

    def _clamp_scroll_offset(self, source_key: str, line_count: int, viewport: int) -> None:
        """Clamp scroll offset for a log source to legal bounds."""
        max_offset = max(0, line_count - viewport)
        self._log_offsets[source_key] = max(
            0, min(self._log_offsets.get(source_key, 0), max_offset)
        )

    def _slice_visible_lines(
        self, lines: list[str], source_key: str, viewport: int
    ) -> tuple[list[str], int]:
        """Return visible viewport lines and start index into full log lines."""
        if not lines:
            return ([], 0)
        offset = self._log_offsets.get(source_key, 0)
        start = max(0, len(lines) - viewport - offset)
        end = start + viewport
        return (lines[start:end], start)

    def _set_scroll_offset(self, delta: int) -> None:
        """Adjust the current log source scroll offset by delta lines."""
        source_key = self._current_log_source_key
        if not source_key:
            return
        current = self._log_offsets.get(source_key, 0)
        self._log_offsets[source_key] = current + delta
        self._clamp_scroll_offset(
            source_key, len(self._current_log_lines), self._current_viewport_lines
        )

    def _refresh_search_matches(self) -> None:
        """Recompute match indexes for the active search query."""
        if not self._search_query:
            self._search_matches = []
            self._search_match_cursor = -1
            return

        prior_line = None
        if 0 <= self._search_match_cursor < len(self._search_matches):
            prior_line = self._search_matches[self._search_match_cursor]

        needle = self._search_query.lower()
        self._search_matches = [
            idx for idx, line in enumerate(self._current_log_lines) if needle in line.lower()
        ]

        if not self._search_matches:
            self._search_match_cursor = -1
            return

        if prior_line in self._search_matches:
            self._search_match_cursor = self._search_matches.index(prior_line)
            return

        if self._search_match_cursor < 0 or self._search_match_cursor >= len(
            self._search_matches
        ):
            self._search_match_cursor = 0

    def _scroll_to_line(self, line_index: int) -> None:
        """Scroll viewport so the target line is visible near the center."""
        if not self._current_log_lines:
            return
        source_key = self._current_log_source_key
        target = max(0, min(line_index, len(self._current_log_lines) - 1))
        start = max(0, target - (self._current_viewport_lines // 2))
        max_start = max(0, len(self._current_log_lines) - self._current_viewport_lines)
        start = min(start, max_start)
        self._log_offsets[source_key] = max(
            0, len(self._current_log_lines) - self._current_viewport_lines - start
        )
        self._clamp_scroll_offset(
            source_key, len(self._current_log_lines), self._current_viewport_lines
        )

    def _jump_search_match(self, direction: int) -> None:
        """Jump to next/previous search match and keep it in view."""
        if not self._search_matches:
            return
        if self._search_match_cursor < 0:
            self._search_match_cursor = 0 if direction >= 0 else len(self._search_matches) - 1
        else:
            self._search_match_cursor = (
                self._search_match_cursor + direction
            ) % len(self._search_matches)
        self._scroll_to_line(self._search_matches[self._search_match_cursor])

    def _handle_search_key(self, key: str) -> None:
        """Handle key input while inline search mode is active."""
        if key == KEY_ESCAPE:
            self._search_mode = False
            self._search_buffer = self._search_query
            return
        if key == KEY_ENTER:
            self._search_mode = False
            self._search_query = self._search_buffer
            self._refresh_search_matches()
            if self._search_matches:
                self._search_match_cursor = 0
                self._scroll_to_line(self._search_matches[0])
            return
        if key in ("\x7f", "\b"):
            self._search_buffer = self._search_buffer[:-1]
            return
        if len(key) == 1 and key.isprintable():
            self._search_buffer += key

    def _get_tasks(self) -> list[Task]:
        """Read tasks from the orchestrator (GIL-safe read-only access)."""
        if self._orchestrator is None:
            return []
        return list(self._orchestrator._tasks.values())

    def _build_layout(self) -> Layout:
        """Create the three-region layout: tasks | log, status bar at bottom."""
        layout = Layout()
        layout.split_column(
            Layout(name="body", ratio=1),
            Layout(name="footer", size=1),
        )
        layout["body"].split_row(
            Layout(name="tasks", ratio=1),
            Layout(name="log", ratio=2),
        )
        return layout

    def _update_layout(self, layout: Layout) -> None:
        """Refresh all layout regions from current state."""
        tasks = self._get_tasks()

        # Clamp selection index
        if tasks:
            self._selected = max(0, min(self._selected, len(tasks) - 1))
        else:
            self._selected = 0

        # Update task table
        layout["tasks"].update(build_task_table(tasks, self._selected))

        # Update log panel based on active tab
        log_lines: list[str] = []
        log_title = "Log"
        force_jsonl = False
        source_key = f"{self._tab}:none"
        selected_task: Task | None = None
        if self._tab == "server" and self._log_capture is not None:
            log_lines = self._log_capture.get_lines()
            log_title = "Server Log"
            source_key = "server"
        elif self._tab == "claude":
            selected_task = tasks[self._selected] if tasks else None
            if selected_task:
                log_path = selected_task.stream_log_path
                if self._claude_tailer is None or self._claude_tailer.path != log_path:
                    self._claude_tailer = LogTailer(log_path)
                self._claude_tailer.poll()
                log_lines = self._claude_tailer.get_lines()
                log_title = "Claude"
                source_key = f"claude:{selected_task.id}"
                force_jsonl = True
            else:
                self._claude_tailer = None
                log_title = "Claude"
                source_key = "claude:none"
        else:
            selected_task = tasks[self._selected] if tasks else None
            if selected_task:
                # Lazily create or switch tailer when selection changes
                log_path = selected_task.log_path
                if self._tailer is None or self._tailer.path != log_path:
                    self._tailer = LogTailer(log_path)
                self._tailer.poll()
                log_lines = self._tailer.get_lines()
                source_key = f"task:{selected_task.id}"
            else:
                self._tailer = None
                source_key = "task:none"

        self._current_log_source_key = source_key
        self._current_log_lines = log_lines
        self._current_viewport_lines = self._estimate_log_viewport(layout)
        self._clamp_scroll_offset(source_key, len(log_lines), self._current_viewport_lines)
        self._refresh_search_matches()

        visible_lines, _ = self._slice_visible_lines(
            log_lines, source_key, self._current_viewport_lines
        )
        current_match = self._search_match_cursor + 1 if self._search_match_cursor >= 0 else 0
        layout["log"].update(
            build_log_panel(
                visible_lines,
                selected_task.id if selected_task else None,
                title=log_title,
                force_jsonl=force_jsonl,
                focused=self._focused_pane == "log",
                search_query=self._search_query,
                search_current=current_match,
                search_total=len(self._search_matches),
                search_mode=self._search_mode,
                search_buffer=self._search_buffer,
                scroll_offset=self._log_offsets.get(source_key, 0),
            )
        )

        # Status bar
        layout["footer"].update(
            build_status_bar(
                active_tab=self._tab,
                focused_pane=self._focused_pane,
                search_mode=self._search_mode,
                search_buffer=self._search_buffer,
            )
        )

    def _handle_key(self, key: Optional[str], task_count: int) -> None:
        """Process a single key press, updating selection state."""
        if key is None:
            return

        if self._search_mode:
            self._handle_search_key(key)
            return

        if key == "q":
            self._running = False
        elif key == "/":
            self._search_mode = True
            self._search_buffer = self._search_query
        elif key == KEY_TAB:
            idx = _TAB_ORDER.index(self._tab) if self._tab in _TAB_ORDER else 0
            self._tab = _TAB_ORDER[(idx + 1) % len(_TAB_ORDER)]
        elif key in (KEY_LEFT, "h"):
            self._focused_pane = "tasks"
        elif key in (KEY_RIGHT, "l"):
            self._focused_pane = "log"
        elif key == "n":
            self._jump_search_match(direction=1)
        elif key == "N":
            self._jump_search_match(direction=-1)
        elif self._focused_pane == "tasks":
            if key in ("j", KEY_DOWN):
                if task_count > 0:
                    self._selected = min(self._selected + 1, task_count - 1)
            elif key in ("k", KEY_UP):
                self._selected = max(self._selected - 1, 0)
            elif key.isdigit() and key != "0":
                idx = int(key) - 1
                if idx < task_count:
                    self._selected = idx
        else:
            if key in ("j", KEY_DOWN):
                self._set_scroll_offset(delta=1)
            elif key in ("k", KEY_UP):
                self._set_scroll_offset(delta=-1)
            elif key == KEY_PAGE_DOWN:
                self._set_scroll_offset(delta=max(1, self._current_viewport_lines - 1))
            elif key == KEY_PAGE_UP:
                self._set_scroll_offset(delta=-max(1, self._current_viewport_lines - 1))

    def run(self) -> None:
        """Run the TUI main loop until the user presses 'q'."""
        layout = self._build_layout()
        reader = InputReader()
        self._running = True

        interval = 1.0 / 10  # 10 FPS

        try:
            with reader:
                with Live(
                    layout,
                    refresh_per_second=10,
                    screen=True,
                    auto_refresh=False,
                ) as live:
                    while self._running:
                        start = time.monotonic()

                        try:
                            # Poll input (non-blocking)
                            key = reader.read_key(timeout=0.0)
                            tasks = self._get_tasks()
                            self._handle_key(key, len(tasks))

                            if not self._running:
                                break

                            # Update display
                            self._update_layout(layout)
                            live.refresh()
                        except Exception:
                            logger.exception("TUI render error")

                        # Sleep remainder of frame
                        elapsed = time.monotonic() - start
                        sleep_time = interval - elapsed
                        if sleep_time > 0:
                            time.sleep(sleep_time)
        except KeyboardInterrupt:
            pass
