"""Smoke tests to verify test infrastructure is working."""

from pathlib import Path

import pytest

from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.core.events import EventQueue, EventType


class TestFixturesWork:
    """Verify that shared fixtures are properly configured."""

    def test_tmp_project_has_git_dir(self, tmp_project: Path):
        """tmp_project fixture creates a git repo."""
        assert (tmp_project / ".git").exists()

    def test_sample_task_is_pending(self, sample_task: Task):
        """sample_task fixture creates a PENDING task."""
        assert sample_task.status == TaskStatus.PENDING
        assert sample_task.description == "Test task description"

    def test_running_task_has_pid(self, running_task: Task):
        """running_task fixture has a PID set."""
        assert running_task.status == TaskStatus.RUNNING
        assert running_task.pid == 12345

    def test_event_queue_fixture(self, event_queue: EventQueue):
        """event_queue fixture provides fresh queue."""
        assert event_queue._try_pop() is None

    def test_frozen_time_is_utc(self, frozen_time):
        """frozen_time fixture is timezone-aware."""
        assert frozen_time.tzinfo is not None


class TestMocksWork:
    """Verify that mock helpers work correctly."""

    def test_subprocess_mock_records_calls(self, subprocess_mock):
        """SubprocessMock records called commands."""
        import subprocess
        subprocess.run(["echo", "hello"])
        assert ["echo", "hello"] in subprocess_mock.calls

    def test_subprocess_mock_returns_configured_response(self, subprocess_mock):
        """SubprocessMock returns configured response."""
        import subprocess
        subprocess_mock.set_response(["test", "cmd"], returncode=42, stdout="output")
        result = subprocess.run(["test", "cmd"])
        assert result.returncode == 42
        assert result.stdout == "output"

    def test_git_mock_has_preconfigured_responses(self, git_mock):
        """git_mock has common git commands configured."""
        import subprocess
        result = subprocess.run(["git", "rev-parse", "HEAD"])
        assert result.returncode == 0
        assert "abc123" in result.stdout


class TestEventQueueBasics:
    """Basic EventQueue tests using fixtures."""

    def test_emit_and_pop(self, event_queue: EventQueue, sample_completed_event):
        """Events can be emitted and popped."""
        event_queue.emit(sample_completed_event)
        popped = event_queue._try_pop()
        assert popped is not None
        assert popped.task_id == sample_completed_event.task_id

    def test_fifo_order(self, event_queue: EventQueue, sample_completed_event, sample_failed_event):
        """Events are returned in FIFO order."""
        event_queue.emit(sample_completed_event)
        event_queue.emit(sample_failed_event)

        first = event_queue._try_pop()
        second = event_queue._try_pop()

        assert first.event_type == EventType.COMPLETED
        assert second.event_type == EventType.FAILED


class TestAsyncSupport:
    """Verify async test support."""

    async def test_async_wait_timeout(self, event_queue: EventQueue):
        """Async wait returns None on timeout."""
        result = await event_queue.wait_async(timeout_ms=10)
        assert result is None

    async def test_async_wait_returns_event(self, event_queue: EventQueue, sample_completed_event):
        """Async wait returns event when available."""
        event_queue.emit(sample_completed_event)
        result = await event_queue.wait_async(timeout_ms=100)
        assert result is not None
        assert result.task_id == sample_completed_event.task_id
