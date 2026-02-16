"""End-to-end integration tests: MCP → Orchestrator → TUI.

Verifies that MCP tool functions drive a real Orchestrator (with mocked VM deps),
and that the TUI renders the resulting state correctly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from microvm_orchestrator.core.events import EventType
from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.server import (
    get_task_info,
    list_tasks,
    run_task,
    wait_next_event,
)
from microvm_orchestrator.tools import Orchestrator
from microvm_orchestrator.tui.app import TUIApp
from microvm_orchestrator.tui.components import build_log_panel, build_task_table


def _render(renderable) -> str:
    """Render a Rich renderable to plain text."""
    buf = StringIO()
    console = Console(file=buf, width=120, force_terminal=True, no_color=True)
    console.print(renderable)
    return buf.getvalue()


# =============================================================================
# Class 1: MCP → Orchestrator integration
# =============================================================================


@pytest.mark.integration
class TestMCPToOrchestratorIntegration:
    """Test that MCP tool functions drive the real Orchestrator correctly."""

    @pytest.mark.asyncio
    async def test_run_task_through_mcp_creates_running_task(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """MCP run_task() → task appears in Orchestrator._tasks with RUNNING status."""
        with patch("microvm_orchestrator.server.get_orchestrator", return_value=orchestrator):
            result = await run_task("Build the widget", repo="project")

        assert "task_id" in result
        task_id = result["task_id"]
        assert task_id in orchestrator._tasks
        task = orchestrator._tasks[task_id]
        assert task.status == TaskStatus.RUNNING
        assert task.description == "Build the widget"

    @pytest.mark.asyncio
    async def test_mcp_get_task_info_reflects_orchestrator_state(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """get_task_info() returns live state from the real Orchestrator."""
        with patch("microvm_orchestrator.server.get_orchestrator", return_value=orchestrator):
            result = await run_task("Info test task", repo="project")
            task_id = result["task_id"]

            info = await get_task_info(task_id)

        assert info["task_id"] == task_id
        assert info["status"] == "running"
        assert info["description"] == "Info test task"

    @pytest.mark.asyncio
    async def test_mcp_list_tasks_reflects_orchestrator_state(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """list_tasks() shows multiple tasks created through MCP."""
        with patch("microvm_orchestrator.server.get_orchestrator", return_value=orchestrator):
            r1 = await run_task("Task A", repo="project")
            r2 = await run_task("Task B", repo="project")

            result = await list_tasks()

        task_ids = {t["task_id"] for t in result["tasks"]}
        assert r1["task_id"] in task_ids
        assert r2["task_id"] in task_ids

    @pytest.mark.asyncio
    async def test_mcp_wait_event_receives_completion(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """wait_next_event() receives an event after _on_task_exit() from a background thread."""
        with patch("microvm_orchestrator.server.get_orchestrator", return_value=orchestrator):
            result = await run_task("Event test", repo="project")
            task_id = result["task_id"]
            task = orchestrator._tasks[task_id]

            # Simulate VM exit from a background thread (mirrors real behavior)
            def background_exit():
                time.sleep(0.05)
                orchestrator._on_task_exit(task, 0)

            t = threading.Thread(target=background_exit)
            t.start()

            event = await wait_next_event(timeout_ms=5000)
            t.join(timeout=2)

        assert event["task_id"] == task_id
        assert event["event"] == "completed"


# =============================================================================
# Class 2: Task lifecycle through real Orchestrator
# =============================================================================


@pytest.mark.integration
class TestTaskLifecycleIntegration:
    """Test the full PENDING → RUNNING → COMPLETED/FAILED lifecycle."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_pending_running_completed(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Task goes PENDING → RUNNING → COMPLETED with exit code 0."""
        # Track state transitions
        observed_states: list[TaskStatus] = []
        original_mark_running = Task.mark_running

        def tracking_mark_running(self_task, pid):
            # Task is PENDING right before mark_running
            observed_states.append(self_task.status)
            result = original_mark_running(self_task, pid)
            observed_states.append(self_task.status)
            return result

        with patch.object(Task, "mark_running", tracking_mark_running):
            result = await orchestrator.run_task("Lifecycle test", "project")

        task_id = result["task_id"]
        task = orchestrator._tasks[task_id]

        # Verify PENDING → RUNNING transition was observed
        assert observed_states == [TaskStatus.PENDING, TaskStatus.RUNNING]

        # Simulate completion
        orchestrator._on_task_exit(task, 0)

        assert task.status == TaskStatus.COMPLETED
        assert task.exit_code == 0

        # Verify event was emitted
        event = orchestrator.event_queue._try_pop()
        assert event is not None
        assert event.event_type == EventType.COMPLETED
        assert event.task_id == task_id

    @pytest.mark.asyncio
    async def test_full_lifecycle_pending_running_failed(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Task goes PENDING → RUNNING → FAILED with non-zero exit code."""
        result = await orchestrator.run_task("Fail test", "project")
        task_id = result["task_id"]
        task = orchestrator._tasks[task_id]

        assert task.status == TaskStatus.RUNNING

        # Simulate failure
        orchestrator._on_task_exit(task, 1)

        assert task.status == TaskStatus.FAILED
        assert task.exit_code == 1

        event = orchestrator.event_queue._try_pop()
        assert event is not None
        assert event.event_type == EventType.FAILED

    @pytest.mark.asyncio
    async def test_lifecycle_failed_at_start(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """If setup_isolated_repo fails, task goes directly to FAILED and emits event."""
        mock_orchestrator_deps["setup_repo"].side_effect = RuntimeError("git clone failed")

        from microvm_orchestrator.tools import ToolError

        with pytest.raises(ToolError, match="Failed to start task"):
            await orchestrator.run_task("Should fail", "project")

        # Find the failed task
        failed_tasks = [t for t in orchestrator._tasks.values() if t.status == TaskStatus.FAILED]
        assert len(failed_tasks) == 1
        assert "git clone failed" in failed_tasks[0].error

        # Event was emitted
        event = orchestrator.event_queue._try_pop()
        assert event is not None
        assert event.event_type == EventType.FAILED

        # Slot was released (all slots available)
        available = orchestrator.slot_manager.get_available_slots()
        assert len(available) == orchestrator.slot_manager.max_slots

    @pytest.mark.asyncio
    async def test_slot_released_after_completion(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Slot count returns to original after task completion."""
        initial_available = len(orchestrator.slot_manager.get_available_slots())

        result = await orchestrator.run_task("Slot test", "project")
        task = orchestrator._tasks[result["task_id"]]

        # One slot consumed
        during_available = len(orchestrator.slot_manager.get_available_slots())
        assert during_available == initial_available - 1

        # Complete the task
        orchestrator._on_task_exit(task, 0)

        # Slot released
        after_available = len(orchestrator.slot_manager.get_available_slots())
        assert after_available == initial_available


# =============================================================================
# Class 3: TUI renders Orchestrator state
# =============================================================================


@pytest.mark.integration
class TestTUIRendersOrchestratorState:
    """Test that TUIApp reflects real Orchestrator state via rendering."""

    def _make_task(self, repo_path: Path, description: str, slot: int = 1) -> Task:
        """Create a task with a valid task_dir."""
        task = Task.create(description=description, slot=slot, repo_path=repo_path)
        task.task_dir.mkdir(parents=True, exist_ok=True)
        task.save()
        return task

    @pytest.mark.asyncio
    async def test_tui_shows_running_task(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Task table contains RUNNING status, task ID, and description."""
        result = await orchestrator.run_task("Widget builder", "project")
        task_id = result["task_id"]

        app = TUIApp(orchestrator)
        layout = app._build_layout()
        app._update_layout(layout)

        tasks = app._get_tasks()
        rendered = _render(build_task_table(tasks, 0))

        assert "RUNNING" in rendered
        assert task_id[:8] in rendered
        assert "Widget builder" in rendered

    @pytest.mark.asyncio
    async def test_tui_shows_completed_task(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """After _on_task_exit(0), table shows COMPLETED."""
        result = await orchestrator.run_task("Complete me", "project")
        task = orchestrator._tasks[result["task_id"]]

        orchestrator._on_task_exit(task, 0)

        app = TUIApp(orchestrator)
        tasks = app._get_tasks()
        rendered = _render(build_task_table(tasks, 0))

        assert "COMPLETED" in rendered

    @pytest.mark.asyncio
    async def test_tui_shows_failed_task(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """After _on_task_exit(1), table shows FAILED."""
        result = await orchestrator.run_task("Fail me", "project")
        task = orchestrator._tasks[result["task_id"]]

        orchestrator._on_task_exit(task, 1)

        app = TUIApp(orchestrator)
        tasks = app._get_tasks()
        rendered = _render(build_task_table(tasks, 0))

        assert "FAILED" in rendered

    @pytest.mark.asyncio
    async def test_tui_shows_multiple_tasks_mixed_states(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Three tasks in RUNNING/COMPLETED/FAILED all render correctly."""
        r1 = await orchestrator.run_task("Running task", "project")
        r2 = await orchestrator.run_task("Completed task", "project")
        r3 = await orchestrator.run_task("Failed task", "project")

        orchestrator._on_task_exit(orchestrator._tasks[r2["task_id"]], 0)
        orchestrator._on_task_exit(orchestrator._tasks[r3["task_id"]], 1)

        app = TUIApp(orchestrator)
        tasks = app._get_tasks()
        rendered = _render(build_task_table(tasks, 0))

        assert "RUNNING" in rendered
        assert "COMPLETED" in rendered
        assert "FAILED" in rendered

    @pytest.mark.asyncio
    async def test_tui_log_panel_shows_streamed_content(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Writing to task.log_path is picked up by LogTailer during _update_layout."""
        result = await orchestrator.run_task("Log test", "project")
        task = orchestrator._tasks[result["task_id"]]

        # Write log content
        task.log_path.parent.mkdir(parents=True, exist_ok=True)
        task.log_path.write_text("Boot complete\nRunning task...\n")

        app = TUIApp(orchestrator)
        layout = app._build_layout()
        app._update_layout(layout)

        # Verify tailer picked up content
        assert app._tailer is not None
        lines = app._tailer.get_lines()
        assert "Boot complete" in lines
        assert "Running task..." in lines

    @pytest.mark.asyncio
    async def test_tui_log_panel_updates_on_append(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Appending to log file is picked up on second _update_layout call."""
        result = await orchestrator.run_task("Append test", "project")
        task = orchestrator._tasks[result["task_id"]]

        task.log_path.parent.mkdir(parents=True, exist_ok=True)
        task.log_path.write_text("Line 1\n")

        app = TUIApp(orchestrator)
        layout = app._build_layout()
        app._update_layout(layout)

        assert "Line 1" in app._tailer.get_lines()

        # Append more content
        with task.log_path.open("a") as f:
            f.write("Line 2\nLine 3\n")

        app._update_layout(layout)

        lines = app._tailer.get_lines()
        assert "Line 1" in lines
        assert "Line 2" in lines
        assert "Line 3" in lines

    @pytest.mark.asyncio
    async def test_tui_selection_switches_log_view(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Changing _selected switches LogTailer to different task's log."""
        r1 = await orchestrator.run_task("Task A log", "project")
        r2 = await orchestrator.run_task("Task B log", "project")

        task_a = orchestrator._tasks[r1["task_id"]]
        task_b = orchestrator._tasks[r2["task_id"]]

        # Write different log content for each task
        for t, content in [(task_a, "Alpha output\n"), (task_b, "Beta output\n")]:
            t.log_path.parent.mkdir(parents=True, exist_ok=True)
            t.log_path.write_text(content)

        app = TUIApp(orchestrator)
        layout = app._build_layout()

        # Select first task
        app._selected = 0
        app._update_layout(layout)
        first_tailer_path = app._tailer.path

        # Select second task
        app._selected = 1
        app._update_layout(layout)
        second_tailer_path = app._tailer.path

        # Tailer should have switched to the other task's log
        assert first_tailer_path != second_tailer_path
