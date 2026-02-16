"""Rich renderables for TUI panels: task table, log viewer, status bar."""

from __future__ import annotations

from typing import Sequence

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..core.task import Task, TaskStatus
from .format import format_log_content

# Status -> Rich style mapping
_STATUS_STYLES: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "yellow",
    TaskStatus.RUNNING: "green",
    TaskStatus.COMPLETED: "dim",
    TaskStatus.FAILED: "red",
}


def build_task_table(tasks: Sequence[Task], selected_index: int) -> Table:
    """Build a Rich Table of tasks with status-colored rows and selection highlight.

    Args:
        tasks: Ordered sequence of tasks to display.
        selected_index: Index of the currently selected row (0-based).

    Returns:
        A Rich Table renderable.
    """
    table = Table(title="Tasks", expand=True, highlight=False)
    table.add_column("#", width=3, justify="right")
    table.add_column("ID", width=8)
    table.add_column("Status", width=10)
    table.add_column("Slot", width=4, justify="right")
    table.add_column("Description", ratio=1)

    for i, task in enumerate(tasks):
        style = _STATUS_STYLES.get(task.status, "")
        is_selected = i == selected_index

        marker = ">" if is_selected else " "
        row_style = f"bold {style}" if is_selected else style

        table.add_row(
            f"{marker}{i + 1}",
            task.id[:8],
            task.status.value.upper(),
            str(task.slot),
            task.description,
            style=row_style,
        )

    return table


def build_log_panel(
    lines: list[str],
    task_id: str | None = None,
    title: str | None = None,
) -> Panel:
    """Build a Rich Panel displaying log lines.

    Args:
        lines: Log lines to display.
        task_id: Optional task ID used to derive a default title.
        title: Explicit panel title (overrides task_id-based title).

    Returns:
        A Rich Panel renderable.
    """
    if title is None:
        title = f"Log: {task_id[:8]}" if task_id else "Log"
    body = format_log_content(lines)
    return Panel(body, title=title, expand=True)


def build_status_bar(active_tab: str = "task") -> Text:
    """Build a status bar showing keybinding hints and active tab indicator.

    Args:
        active_tab: Currently active tab (``"task"`` or ``"server"``).

    Returns:
        A Rich Text renderable.
    """
    bar = Text()
    bar.append(" [q]", style="bold")
    bar.append("uit  ")
    bar.append("[j/k]", style="bold")
    bar.append("nav  ")
    bar.append("[1-9]", style="bold")
    bar.append("select  ")
    bar.append("[tab]", style="bold")
    bar.append("switch  ")

    # Tab indicator
    if active_tab == "task":
        bar.append("VM Log", style="bold underline")
        bar.append(" | Server Log")
    else:
        bar.append("VM Log | ")
        bar.append("Server Log", style="bold underline")

    return bar
