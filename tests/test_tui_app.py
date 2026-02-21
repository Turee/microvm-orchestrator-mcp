"""Tests for tui/app.py - TUIApp main loop."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.tui.app import TUIApp
from microvm_orchestrator.tui.input import (
    KEY_DOWN,
    KEY_ENTER,
    KEY_ESCAPE,
    KEY_LEFT,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    KEY_RIGHT,
    KEY_UP,
)
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
        assert app._focused_pane == "tasks"
        assert app._claude_tailer is None

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

    def test_left_and_right_switch_focus_panes(self):
        app = TUIApp()
        assert app._focused_pane == "tasks"
        app._handle_key(KEY_RIGHT, task_count=2)
        assert app._focused_pane == "log"
        app._handle_key(KEY_LEFT, task_count=2)
        assert app._focused_pane == "tasks"

    def test_up_down_scroll_logs_when_log_pane_focused(self):
        app = TUIApp()
        app._focused_pane = "log"
        app._selected = 1
        app._current_log_source_key = "task:abc"
        app._current_log_lines = [f"line {i}" for i in range(50)]
        app._current_viewport_lines = 10
        app._log_offsets["task:abc"] = 0

        app._handle_key(KEY_DOWN, task_count=3)
        assert app._selected == 1
        assert app._log_offsets["task:abc"] == 1

        app._handle_key(KEY_UP, task_count=3)
        assert app._selected == 1
        assert app._log_offsets["task:abc"] == 0

    def test_page_scroll_clamps_bounds(self):
        app = TUIApp()
        app._focused_pane = "log"
        app._current_log_source_key = "task:abc"
        app._current_log_lines = [f"line {i}" for i in range(25)]
        app._current_viewport_lines = 10
        app._log_offsets["task:abc"] = 0

        app._handle_key(KEY_PAGE_DOWN, task_count=1)
        assert app._log_offsets["task:abc"] == 9
        app._handle_key(KEY_PAGE_DOWN, task_count=1)
        # max offset = line_count - viewport = 15
        assert app._log_offsets["task:abc"] == 15
        app._handle_key(KEY_PAGE_UP, task_count=1)
        assert app._log_offsets["task:abc"] == 6
        app._handle_key(KEY_PAGE_UP, task_count=1)
        app._handle_key(KEY_PAGE_UP, task_count=1)
        assert app._log_offsets["task:abc"] == 0

    def test_search_mode_lifecycle(self):
        app = TUIApp()
        app._current_log_source_key = "task:abc"
        app._current_log_lines = ["alpha", "beta error", "gamma error"]
        app._current_viewport_lines = 5

        app._handle_key("/", task_count=1)
        assert app._search_mode is True
        app._handle_key("e", task_count=1)
        app._handle_key("r", task_count=1)
        app._handle_key("r", task_count=1)
        app._handle_key(KEY_ENTER, task_count=1)
        assert app._search_mode is False
        assert app._search_query == "err"
        assert len(app._search_matches) == 2

    def test_escape_cancels_search_mode(self):
        app = TUIApp()
        app._search_query = "old"
        app._handle_key("/", task_count=1)
        app._handle_key("x", task_count=1)
        assert app._search_mode is True
        app._handle_key(KEY_ESCAPE, task_count=1)
        assert app._search_mode is False
        assert app._search_query == "old"

    def test_n_and_shift_n_navigate_matches(self):
        app = TUIApp()
        app._current_log_source_key = "task:abc"
        app._current_log_lines = ["aaa", "error1", "bbb", "error2", "ccc"]
        app._current_viewport_lines = 3
        app._search_query = "error"
        app._refresh_search_matches()
        assert app._search_matches == [1, 3]

        app._handle_key("n", task_count=1)
        assert app._search_match_cursor == 1
        app._handle_key("N", task_count=1)
        assert app._search_match_cursor == 0

    def test_j_with_no_tasks_stays_at_zero(self):
        app = TUIApp()
        app._selected = 0
        app._handle_key("j", task_count=0)
        assert app._selected == 0

    def test_tab_cycles_task_to_claude(self):
        from microvm_orchestrator.tui.input import KEY_TAB

        app = TUIApp()
        assert app._tab == "task"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "claude"

    def test_tab_cycles_claude_to_server(self):
        from microvm_orchestrator.tui.input import KEY_TAB

        app = TUIApp()
        app._tab = "claude"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "server"

    def test_tab_cycles_server_to_task(self):
        from microvm_orchestrator.tui.input import KEY_TAB

        app = TUIApp()
        app._tab = "server"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "task"

    def test_tab_full_cycle(self):
        from microvm_orchestrator.tui.input import KEY_TAB

        app = TUIApp()
        assert app._tab == "task"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "claude"
        app._handle_key(KEY_TAB, task_count=3)
        assert app._tab == "server"
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

    def test_claude_tab_creates_claude_tailer(self):
        t1 = _make_task()
        app = TUIApp(_mock_orchestrator([t1]))
        app._tab = "claude"
        layout = app._build_layout()
        app._update_layout(layout)
        assert app._claude_tailer is not None
        assert app._claude_tailer.path == t1.stream_log_path

    def test_claude_tab_switches_tailer_on_selection_change(self):
        t1 = _make_task()
        t2 = _make_task(id="beef5678-0000-0000-0000-000000000000", slot=2)
        app = TUIApp(_mock_orchestrator([t1, t2]))
        app._tab = "claude"
        layout = app._build_layout()

        app._selected = 0
        app._update_layout(layout)
        first_tailer = app._claude_tailer

        app._selected = 1
        app._update_layout(layout)
        assert app._claude_tailer is not first_tailer
        assert app._claude_tailer.path == t2.stream_log_path

    def test_claude_tab_clears_tailer_when_no_tasks(self):
        app = TUIApp(_mock_orchestrator([]))
        app._tab = "claude"
        app._claude_tailer = object()  # type: ignore[assignment]
        layout = app._build_layout()
        app._update_layout(layout)
        assert app._claude_tailer is None

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

    def test_live_created_with_auto_refresh_false(self):
        """Live should be created with auto_refresh=False to prevent the refresh thread."""
        app = TUIApp()

        live_kwargs = {}

        original_live_init = None

        class CaptureLive:
            """Capture Live() kwargs and act as a context manager."""

            def __init__(self, *args, **kwargs):
                live_kwargs.update(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def refresh(self):
                pass

        key_calls = 0

        def fake_read_key(timeout=0.0):
            nonlocal key_calls
            key_calls += 1
            return "q"

        with patch("microvm_orchestrator.tui.app.Live", CaptureLive):
            with patch("microvm_orchestrator.tui.app.InputReader") as MockReader:
                mock_reader = MagicMock()
                mock_reader.read_key = fake_read_key
                mock_reader.__enter__ = MagicMock(return_value=mock_reader)
                mock_reader.__exit__ = MagicMock(return_value=False)
                MockReader.return_value = mock_reader

                app.run()

        assert live_kwargs.get("auto_refresh") is False

    def test_update_layout_exception_does_not_crash_loop(self):
        """An exception in _update_layout should be caught, not crash the loop."""
        app = TUIApp()

        call_count = 0

        def exploding_update(layout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("render boom")
            # Second call: let it succeed (after 'q' is pressed)

        def fake_read_key(timeout=0.0):
            # Return None first time (triggering the error path), then 'q'
            if call_count == 0:
                return None
            return "q"

        class FakeLive:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def refresh(self):
                pass

        with patch("microvm_orchestrator.tui.app.Live", FakeLive):
            with patch("microvm_orchestrator.tui.app.InputReader") as MockReader:
                mock_reader = MagicMock()
                mock_reader.read_key = fake_read_key
                mock_reader.__enter__ = MagicMock(return_value=mock_reader)
                mock_reader.__exit__ = MagicMock(return_value=False)
                MockReader.return_value = mock_reader

                with patch.object(app, "_update_layout", side_effect=exploding_update):
                    app.run()  # Should NOT raise

        assert call_count >= 1  # The error was hit at least once
        assert app._running is False  # Loop exited cleanly
