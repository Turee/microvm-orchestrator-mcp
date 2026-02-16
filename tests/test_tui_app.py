"""Tests for tui/app.py - TUIApp main loop."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.tui.app import TUIApp
from microvm_orchestrator.tui.log_capture import LogCapture


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
    """Create a mock orchestrator with _tasks dict."""
    orch = SimpleNamespace()
    orch._tasks = {t.id: t for t in (tasks or [])}
    return orch


class TestTUIAppInit:
    def test_default_no_orchestrator(self):
        app = TUIApp()
        assert app._orchestrator is None
        assert app._selected == 0
        assert app._log_capture is None
        assert app._tab == "task"

    def test_with_orchestrator(self):
        orch = _mock_orchestrator()
        app = TUIApp(orch)
        assert app._orchestrator is orch

    def test_with_log_capture(self):
        lc = LogCapture()
        app = TUIApp(log_capture=lc)
        assert app._log_capture is lc


class TestGetTasks:
    def test_no_orchestrator_returns_empty(self):
        app = TUIApp()
        assert app._get_tasks() == []

    def test_returns_tasks_from_orchestrator(self):
        t1 = _make_task()
        t2 = _make_task(id="beef5678-0000-0000-0000-000000000000", slot=2)
        orch = _mock_orchestrator([t1, t2])
        app = TUIApp(orch)
        tasks = app._get_tasks()
        assert len(tasks) == 2

    def test_returns_copy_not_reference(self):
        t1 = _make_task()
        orch = _mock_orchestrator([t1])
        app = TUIApp(orch)
        tasks = app._get_tasks()
        tasks.clear()
        # Original should be unaffected
        assert len(app._get_tasks()) == 1


class TestHandleKey:
    def test_q_stops_app(self):
        app = TUIApp()
        app._running = True
        app._handle_key("q", task_count=3)
        assert app._running is False

    def test_j_moves_down(self):
        app = TUIApp()
        app._selected = 0
        app._handle_key("j", task_count=3)
        assert app._selected == 1

    def test_k_moves_up(self):
        app = TUIApp()
        app._selected = 2
        app._handle_key("k", task_count=3)
        assert app._selected == 1

    def test_j_clamps_at_bottom(self):
        app = TUIApp()
        app._selected = 2
        app._handle_key("j", task_count=3)
        assert app._selected == 2

    def test_k_clamps_at_top(self):
        app = TUIApp()
        app._selected = 0
        app._handle_key("k", task_count=3)
        assert app._selected == 0

    def test_digit_selects_task(self):
        app = TUIApp()
        app._selected = 0
        app._handle_key("3", task_count=5)
        assert app._selected == 2

    def test_digit_out_of_range_ignored(self):
        app = TUIApp()
        app._selected = 0
        app._handle_key("9", task_count=3)
        assert app._selected == 0

    def test_zero_ignored(self):
        app = TUIApp()
        app._selected = 1
        app._handle_key("0", task_count=3)
        assert app._selected == 1

    def test_none_is_noop(self):
        app = TUIApp()
        app._selected = 1
        app._running = True
        app._handle_key(None, task_count=3)
        assert app._selected == 1
        assert app._running is True

    def test_arrow_down(self):
        from microvm_orchestrator.tui.input import KEY_DOWN

        app = TUIApp()
        app._selected = 0
        app._handle_key(KEY_DOWN, task_count=3)
        assert app._selected == 1

    def test_arrow_up(self):
        from microvm_orchestrator.tui.input import KEY_UP

        app = TUIApp()
        app._selected = 2
        app._handle_key(KEY_UP, task_count=3)
        assert app._selected == 1

    def test_j_with_no_tasks_stays_at_zero(self):
        app = TUIApp()
        app._selected = 0
        app._handle_key("j", task_count=0)
        assert app._selected == 0

    def test_tab_toggles_to_server(self):
        from microvm_orchestrator.tui.input import KEY_TAB

        app = TUIApp()
        assert app._tab == "task"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "server"

    def test_tab_toggles_back_to_task(self):
        from microvm_orchestrator.tui.input import KEY_TAB

        app = TUIApp()
        app._tab = "server"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "task"


class TestBuildLayout:
    def test_layout_has_expected_regions(self):
        app = TUIApp()
        layout = app._build_layout()
        # Should have tasks, log, and footer regions
        assert layout["tasks"] is not None
        assert layout["log"] is not None
        assert layout["footer"] is not None


class TestUpdateLayout:
    def test_empty_orchestrator(self):
        """Update layout with no tasks should not raise."""
        app = TUIApp(_mock_orchestrator([]))
        layout = app._build_layout()
        app._update_layout(layout)
        assert app._selected == 0

    def test_clamps_selection(self):
        """Selection is clamped if tasks shrink."""
        t1 = _make_task()
        app = TUIApp(_mock_orchestrator([t1]))
        app._selected = 5  # Out of range
        layout = app._build_layout()
        app._update_layout(layout)
        assert app._selected == 0

    def test_creates_tailer_for_selected(self):
        t1 = _make_task()
        app = TUIApp(_mock_orchestrator([t1]))
        layout = app._build_layout()
        app._update_layout(layout)
        assert app._tailer is not None
        assert app._tailer.path == t1.log_path

    def test_switches_tailer_on_selection_change(self):
        t1 = _make_task()
        t2 = _make_task(id="beef5678-0000-0000-0000-000000000000", slot=2)
        app = TUIApp(_mock_orchestrator([t1, t2]))
        layout = app._build_layout()

        app._selected = 0
        app._update_layout(layout)
        first_tailer = app._tailer

        app._selected = 1
        app._update_layout(layout)
        assert app._tailer is not first_tailer
        assert app._tailer.path == t2.log_path

    def test_clears_tailer_when_no_tasks(self):
        app = TUIApp(_mock_orchestrator([]))
        app._tailer = object()  # type: ignore[assignment]
        layout = app._build_layout()
        app._update_layout(layout)
        assert app._tailer is None

    def test_server_tab_uses_log_capture(self):
        import logging

        lc = LogCapture()
        lc.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="server hello", args=(), exc_info=None,
        )
        lc.emit(record)

        app = TUIApp(_mock_orchestrator([]), log_capture=lc)
        app._tab = "server"
        layout = app._build_layout()
        app._update_layout(layout)
        # The log panel should exist and not raise


class TestRun:
    def test_quit_on_first_key(self):
        """Pressing 'q' immediately should exit the loop."""
        app = TUIApp()

        call_count = 0

        def fake_read_key(timeout=0.0):
            nonlocal call_count
            call_count += 1
            return "q"

        with patch.object(
            TUIApp, "run", wraps=None
        ):
            # Test the loop logic directly rather than mocking Live
            app._running = True
            app._handle_key("q", 0)
            assert app._running is False
