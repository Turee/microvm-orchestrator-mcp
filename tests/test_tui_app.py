"""Tests for Textual-based `tui/app.py`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.tui.app import TUIApp


def _make_task(
    id: str = "abcd1234-0000-0000-0000-000000000000",
    description: str = "Run tests",
    status: TaskStatus = TaskStatus.RUNNING,
    slot: int = 1,
) -> Task:
    return Task(
        id=id,
        description=description,
        status=status,
        slot=slot,
        repo_path=Path("/tmp/fake"),
    )


def _mock_orchestrator(tasks: list[Task] | None = None):
    orch = SimpleNamespace()
    orch._tasks = {t.id: t for t in (tasks or [])}
    return orch


class DummyTailer:
    def __init__(self, path: Path, lines: list[str]):
        self.path = path
        self._lines = lines
        self.poll_calls = 0
        self.total_appended = len(lines)

    def poll(self) -> None:
        self.poll_calls += 1

    def get_lines(self) -> list[str]:
        return list(self._lines)


def test_init_defaults():
    app = TUIApp()
    assert app._orchestrator is None
    assert app._log_capture is None
    assert app._selected_index == 0
    assert app._active_source == "task"
    assert app._follow_logs is True
    assert app._programmatic_update is False
    assert app._row_task_ids == []
    assert app._col_keys == []
    assert app._row_task_state == {}


def test_get_tasks_reads_orchestrator_values():
    t1 = _make_task()
    t2 = _make_task(id="beef5678-0000-0000-0000-000000000000", slot=2)
    app = TUIApp(_mock_orchestrator([t1, t2]))
    tasks = app._get_tasks()
    assert len(tasks) == 2


def test_resolve_log_source_task_uses_term_screen():
    task = _make_task()
    app = TUIApp(_mock_orchestrator([task]))
    app._tasks = [task]
    app._selected_index = 0
    app._active_source = "task"

    from microvm_orchestrator.tui.term_screen import TermScreen

    mock_term = MagicMock(spec=TermScreen)
    with patch.object(app, "_get_term_screen", return_value=mock_term):
        source_key, title, lines, force_jsonl, returned_tailer, term = app._resolve_log_source()

    assert source_key == f"task:{task.id}"
    assert title == "VM Log"
    assert lines == []
    assert force_jsonl is False
    assert returned_tailer is None
    assert term is mock_term
    mock_term.poll.assert_called_once()


def test_resolve_log_source_claude_forces_jsonl():
    task = _make_task()
    app = TUIApp(_mock_orchestrator([task]))
    app._tasks = [task]
    app._selected_index = 0
    app._active_source = "claude"

    tailer = DummyTailer(task.stream_log_path, ['{"type":"content_block_delta"}'])
    with patch.object(app, "_get_tailer", return_value=tailer):
        source_key, title, lines, force_jsonl, returned_tailer, term = app._resolve_log_source()

    assert source_key == f"claude:{task.id}"
    assert title == "Claude"
    assert lines
    assert force_jsonl is True
    assert returned_tailer is tailer
    assert term is None


def test_resolve_log_source_empty_when_no_selected_task():
    app = TUIApp()
    app._tasks = []
    app._active_source = "task"
    source_key, title, lines, force_jsonl, returned_tailer, term = app._resolve_log_source()
    assert source_key == "task:none"
    assert title == "VM Log"
    assert lines == []
    assert force_jsonl is False
    assert returned_tailer is None
    assert term is None


def test_action_next_and_prev_source_cycle():
    app = TUIApp()
    app._active_source = "task"
    app._last_source_key = "task:abc"
    app._rendered_line_count = 10
    app._rendered_total = 10
    with patch.object(app, "_refresh_logs"):
        app.action_next_log_source()
        assert app._active_source == "claude"
        assert app._last_source_key == ""
        assert app._rendered_line_count == 0
        assert app._rendered_total == 0

        app.action_prev_log_source()
        assert app._active_source == "task"


def test_set_selected_row_clamps_and_resets_log_state():
    task = _make_task()
    app = TUIApp(_mock_orchestrator([task]))
    app._tasks = [task]
    app._active_source = "task"
    app._last_source_key = "task:prev"
    app._rendered_line_count = 42
    with patch.object(app, "_refresh_logs") as refresh_logs:
        app._set_selected_row(99)

    assert app._selected_index == 0
    assert app._last_source_key == ""
    assert app._rendered_line_count == 0
    refresh_logs.assert_called_once()


def test_on_mount_configures_widgets_and_intervals():
    app = TUIApp()
    table = MagicMock()
    log_view = MagicMock()
    columns = ["col-1", "col-2", "col-3", "col-4"]
    table.add_columns.return_value = columns

    def fake_query(selector, *_args, **_kwargs):
        if selector == "#task-table":
            return table
        if selector == "#log-view":
            return log_view
        raise AssertionError(f"Unexpected selector: {selector}")

    app.query_one = fake_query  # type: ignore[method-assign]
    app.set_interval = MagicMock()  # type: ignore[method-assign]
    app._refresh_tasks = MagicMock()
    app._refresh_logs = MagicMock()

    app.on_mount()

    table.add_columns.assert_called_once_with("#", "ID", "Status", "Slot")
    table.focus.assert_called_once()
    assert app._col_keys == columns
    assert log_view.auto_scroll is True
    assert log_view.max_lines == 1500
    assert app.set_interval.call_count == 2


def test_show_and_close_description_preview():
    task = _make_task(description="# Hello\n\n- one\n- two")
    app = TUIApp(_mock_orchestrator([task]))
    app._tasks = [task]
    app._selected_index = 0

    with patch.object(app, "_refresh_logs") as refresh_logs:
        app.action_show_description()
        assert app._description_preview_task_id == task.id
        refresh_logs.assert_called_once()

    app._description_preview_task_id = task.id
    with patch.object(app, "_refresh_logs") as refresh_logs:
        app.action_close_description()
        assert app._description_preview_task_id is None
        assert app._last_source_key == ""
        assert app._rendered_line_count == 0
        refresh_logs.assert_called_once()


def test_refresh_logs_plain_text_appends_only_new_lines():
    app = TUIApp()
    title = MagicMock()
    log_view = MagicMock()

    def fake_query(selector, *_args, **_kwargs):
        if selector == "#log-title":
            return title
        if selector == "#log-view":
            return log_view
        raise AssertionError(f"Unexpected selector: {selector}")

    app.query_one = fake_query  # type: ignore[method-assign]
    app._follow_logs = False

    with patch.object(
        app,
        "_resolve_log_source",
        side_effect=[
            ("task:abc", "VM Log", ["a", "b"], False, None, None),
            ("task:abc", "VM Log", ["a", "b", "c"], False, None, None),
        ],
    ):
        app._refresh_logs()
        app._refresh_logs()

    assert log_view.clear.call_count == 1
    # Batched: one write per refresh cycle (2 lines batched, then 1 line)
    assert log_view.write.call_count == 2
    assert app._rendered_line_count == 3


def test_refresh_logs_jsonl_appends_incremental_lines():
    app = TUIApp()
    title = MagicMock()
    log_view = MagicMock()

    def fake_query(selector, *_args, **_kwargs):
        if selector == "#log-title":
            return title
        if selector == "#log-view":
            return log_view
        raise AssertionError(f"Unexpected selector: {selector}")

    app.query_one = fake_query  # type: ignore[method-assign]
    app._follow_logs = False

    with patch.object(
        app,
        "_resolve_log_source",
        side_effect=[
            ("claude:abc", "Claude", ['{"type":"content_block_delta","delta":{"type":"text_delta","text":"a"}}'], True, None, None),
            ("claude:abc", "Claude", ['{"type":"content_block_delta","delta":{"type":"text_delta","text":"a"}}', '{"type":"content_block_delta","delta":{"type":"text_delta","text":"b"}}'], True, None, None),
        ],
    ):
        app._refresh_logs()
        app._refresh_logs()

    assert log_view.clear.call_count == 1
    assert log_view.write.call_count == 2
    assert app._rendered_line_count == 2


def test_programmatic_update_suppresses_row_highlighted():
    app = TUIApp()
    app._programmatic_update = True

    with patch.object(app, "_set_selected_row") as set_selected_row:
        app.on_data_table_row_highlighted(SimpleNamespace(cursor_row=2))
        app.on_data_table_row_selected(SimpleNamespace(cursor_row=2))

    set_selected_row.assert_not_called()


def test_differential_update_uses_update_cell():
    task1 = _make_task(status=TaskStatus.PENDING, slot=1)
    task2 = _make_task(
        id="beef5678-0000-0000-0000-000000000000",
        status=TaskStatus.RUNNING,
        slot=2,
    )
    app = TUIApp()
    app._row_task_ids = [task1.id, task2.id]
    app._col_keys = ["col-0", "col-1", "col-2", "col-3"]
    app._row_task_state = {
        task1.id: (TaskStatus.PENDING, 1),
        task2.id: (TaskStatus.PENDING, 2),
    }
    app._selected_index = 0
    table = MagicMock()
    table.cursor_row = 0

    app.query_one = lambda *_args, **_kwargs: table  # type: ignore[method-assign]
    with patch.object(app, "_get_tasks", return_value=[task1, task2]):
        app._refresh_tasks()

    table.clear.assert_not_called()
    assert table.update_cell.call_count == 2

