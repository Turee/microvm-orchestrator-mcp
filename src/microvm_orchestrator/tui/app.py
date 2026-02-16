"""TUIApp - main loop with Rich Live + Layout."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

from rich.layout import Layout
from rich.live import Live

from .components import _TAB_ORDER, build_log_panel, build_status_bar, build_task_table
from .input import KEY_DOWN, KEY_TAB, KEY_UP, InputReader
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
        self._tailer: Optional[LogTailer] = None
        self._claude_tailer: Optional[LogTailer] = None
        self._running = False

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
        if self._tab == "server" and self._log_capture is not None:
            layout["log"].update(
                build_log_panel(self._log_capture.get_lines(), title="Server Log")
            )
        elif self._tab == "claude":
            selected_task = tasks[self._selected] if tasks else None
            if selected_task:
                log_path = selected_task.stream_log_path
                if self._claude_tailer is None or self._claude_tailer.path != log_path:
                    self._claude_tailer = LogTailer(log_path)
                self._claude_tailer.poll()
                layout["log"].update(
                    build_log_panel(
                        self._claude_tailer.get_lines(),
                        selected_task.id,
                        title="Claude",
                        force_jsonl=True,
                    )
                )
            else:
                self._claude_tailer = None
                layout["log"].update(build_log_panel([], title="Claude"))
        else:
            selected_task = tasks[self._selected] if tasks else None
            if selected_task:
                # Lazily create or switch tailer when selection changes
                log_path = selected_task.log_path
                if self._tailer is None or self._tailer.path != log_path:
                    self._tailer = LogTailer(log_path)
                self._tailer.poll()
                layout["log"].update(
                    build_log_panel(self._tailer.get_lines(), selected_task.id)
                )
            else:
                self._tailer = None
                layout["log"].update(build_log_panel([]))

        # Status bar
        layout["footer"].update(build_status_bar(active_tab=self._tab))

    def _handle_key(self, key: Optional[str], task_count: int) -> None:
        """Process a single key press, updating selection state."""
        if key is None:
            return

        if key == "q":
            self._running = False
        elif key == KEY_TAB:
            idx = _TAB_ORDER.index(self._tab) if self._tab in _TAB_ORDER else 0
            self._tab = _TAB_ORDER[(idx + 1) % len(_TAB_ORDER)]
        elif key in ("j", KEY_DOWN):
            if task_count > 0:
                self._selected = min(self._selected + 1, task_count - 1)
        elif key in ("k", KEY_UP):
            self._selected = max(self._selected - 1, 0)
        elif key.isdigit() and key != "0":
            idx = int(key) - 1
            if idx < task_count:
                self._selected = idx

    def run(self) -> None:
        """Run the TUI main loop until the user presses 'q'."""
        layout = self._build_layout()
        reader = InputReader()
        self._running = True

        interval = 1.0 / 10  # 10 FPS

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
