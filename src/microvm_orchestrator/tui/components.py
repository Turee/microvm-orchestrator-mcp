"""Rich renderables for TUI panels: task table, log viewer, status bar."""

from __future__ import annotations

from typing import Sequence

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..core.task import Task, TaskStatus
from .format import format_log_content

# Tab cycling order and display labels
_TAB_ORDER: list[str] = ["task", "claude", "server"]
_TAB_LABELS: dict[str, str] = {
    "task": "VM Log",
    "claude": "Claude",
    "server": "Server Log",
}

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
    *,
    force_jsonl: bool = False,
    focused: bool = False,
    search_query: str = "",
    search_current: int = 0,
    search_total: int = 0,
    search_mode: bool = False,
    search_buffer: str = "",
    scroll_offset: int = 0,
) -> Panel:
    """Build a Rich Panel displaying log lines.

    Args:
        lines: Log lines to display.
        task_id: Optional task ID used to derive a default title.
        title: Explicit panel title (overrides task_id-based title).
        force_jsonl: When True, force JSONL parsing regardless of
            first-line auto-detection.

    Returns:
        A Rich Panel renderable.
    """
    if title is None:
        title = f"Log: {task_id[:8]}" if task_id else "Log"
    title_parts = [title]
    if focused:
        title_parts.append("FOCUS")
    if search_mode:
        title_parts.append(f"/{search_buffer}_")
    elif search_query:
        if search_total > 0:
            title_parts.append(f"search:{search_current}/{search_total}")
        else:
            title_parts.append("search:0/0")
    if scroll_offset > 0:
        title_parts.append(f"scroll:+{scroll_offset}")

    body = format_log_content(lines, force_jsonl=force_jsonl)
    return Panel(body, title=" | ".join(title_parts), expand=True)


def build_status_bar(
    active_tab: str = "task",
    focused_pane: str = "tasks",
    search_mode: bool = False,
    search_buffer: str = "",
) -> Text:
    """Build a status bar showing keybinding hints and active tab indicator.

    Args:
        active_tab: Currently active tab (one of ``_TAB_ORDER``).

    Returns:
        A Rich Text renderable.
    """
    bar = Text()
    bar.append(" [q]", style="bold")
    bar.append("uit  ")
    bar.append("[j/k]", style="bold")
    bar.append("nav/scroll  ")
    bar.append("[up/down]", style="bold")
    bar.append("nav/scroll  ")
    bar.append("[pgup/pgdn]", style="bold")
    bar.append("page-scroll  ")
    bar.append("[left/right]", style="bold")
    bar.append("focus pane  ")
    bar.append("[1-9]", style="bold")
    bar.append("select  ")
    bar.append("[/]", style="bold")
    bar.append("search  ")
    bar.append("[n/N]", style="bold")
    bar.append("match  ")
    bar.append("[tab]", style="bold")
    bar.append("switch  ")
    bar.append(f"[pane:{focused_pane}]")
    if search_mode:
        bar.append(f"  [search:/{search_buffer}_]", style="bold")

    # Tab indicator
    for i, tab_key in enumerate(_TAB_ORDER):
        if i > 0:
            bar.append(" | ")
        label = _TAB_LABELS[tab_key]
        if tab_key == active_tab:
            bar.append(label, style="bold underline")
        else:
            bar.append(label)

    return bar
