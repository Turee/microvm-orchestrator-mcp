"""Contract tests for MCP Server (server.py).

These tests verify that MCP tool functions return correct response formats.
The orchestrator is mocked to isolate testing of the server layer.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from microvm_orchestrator.server import (
    run_task,
    get_task_info,
    get_task_logs,
    wait_next_event,
    cleanup_task,
)
from microvm_orchestrator.tools import ToolError


# =============================================================================
# run_task Tests
# =============================================================================


class TestRunTask:
    """Tests for the run_task MCP tool."""

    async def test_run_task_returns_dict(self):
        """run_task returns {"task_id": str} on success."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.run_task = AsyncMock(
            return_value={"task_id": "abc123-task-id"}
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await run_task("Test description", repo="my-project")

        assert result == {"task_id": "abc123-task-id"}
        mock_orchestrator.run_task.assert_called_once_with("Test description", "my-project")

    async def test_run_task_error_format(self):
        """run_task returns {"error": str} on ToolError."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.run_task = AsyncMock(
            side_effect=ToolError("Failed to start task")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await run_task("Test description", repo="my-project")

        assert result == {"error": "Failed to start task"}

    async def test_run_task_generic_error_format(self):
        """run_task returns {"error": str} on generic exception."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.run_task = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await run_task("Test description", repo="my-project")

        assert result == {"error": "Unexpected error"}


# =============================================================================
# get_task_info Tests
# =============================================================================


class TestGetTaskInfo:
    """Tests for the get_task_info MCP tool."""

    async def test_get_task_info_returns_dict(self):
        """get_task_info returns task info dict on success."""
        expected_info = {
            "task_id": "abc123",
            "status": "running",
            "description": "Test task",
            "slot": 1,
            "pid": 12345,
        }
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_task_info = MagicMock(return_value=expected_info)

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await get_task_info("abc123")

        assert result == expected_info
        mock_orchestrator.get_task_info.assert_called_once_with("abc123")

    async def test_get_task_info_error_format(self):
        """get_task_info returns {"error": str} on ToolError."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_task_info = MagicMock(
            side_effect=ToolError("Task not found: xyz789")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await get_task_info("xyz789")

        assert result == {"error": "Task not found: xyz789"}

    async def test_get_task_info_generic_error_format(self):
        """get_task_info returns {"error": str} on generic exception."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_task_info = MagicMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await get_task_info("abc123")

        assert result == {"error": "Unexpected error"}


# =============================================================================
# get_task_logs Tests
# =============================================================================


class TestGetTaskLogs:
    """Tests for the get_task_logs MCP tool."""

    async def test_get_task_logs_returns_dict(self):
        """get_task_logs returns {"log_path": str} on success."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_task_logs = MagicMock(
            return_value={"log_path": "/tmp/tasks/abc123/serial.log"}
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await get_task_logs("abc123")

        assert result == {"log_path": "/tmp/tasks/abc123/serial.log"}
        mock_orchestrator.get_task_logs.assert_called_once_with("abc123")

    async def test_get_task_logs_error_format(self):
        """get_task_logs returns {"error": str} on ToolError."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_task_logs = MagicMock(
            side_effect=ToolError("Task not found")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await get_task_logs("nonexistent")

        assert result == {"error": "Task not found"}

    async def test_get_task_logs_generic_error_format(self):
        """get_task_logs returns {"error": str} on generic exception."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.get_task_logs = MagicMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await get_task_logs("abc123")

        assert result == {"error": "Unexpected error"}


# =============================================================================
# wait_next_event Tests
# =============================================================================


class TestWaitNextEvent:
    """Tests for the wait_next_event MCP tool."""

    async def test_wait_next_event_returns_event(self):
        """wait_next_event returns event dict on success."""
        expected_event = {
            "task_id": "abc123",
            "event": "completed",
            "exit_code": 0,
            "result": {"success": True},
        }
        mock_orchestrator = MagicMock()
        mock_orchestrator.wait_next_event = AsyncMock(return_value=expected_event)

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await wait_next_event(timeout_ms=5000)

        assert result == expected_event
        mock_orchestrator.wait_next_event.assert_called_once_with(5000)

    async def test_wait_next_event_timeout(self):
        """wait_next_event returns {"timeout": True} on timeout."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.wait_next_event = AsyncMock(return_value={"timeout": True})

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await wait_next_event(timeout_ms=100)

        assert result == {"timeout": True}

    async def test_wait_next_event_cancelled(self):
        """wait_next_event returns {"cancelled": True} on CancelledError."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.wait_next_event = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await wait_next_event(timeout_ms=5000)

        assert result == {"cancelled": True}

    async def test_wait_next_event_no_running_tasks(self):
        """wait_next_event returns {"no_running_tasks": True} when no tasks."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.wait_next_event = AsyncMock(
            return_value={"no_running_tasks": True}
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await wait_next_event()

        assert result == {"no_running_tasks": True}

    async def test_wait_next_event_error_format(self):
        """wait_next_event returns {"error": str} on ToolError."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.wait_next_event = AsyncMock(
            side_effect=ToolError("Event queue error")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await wait_next_event()

        assert result == {"error": "Event queue error"}

    async def test_wait_next_event_generic_error_format(self):
        """wait_next_event returns {"error": str} on generic exception."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.wait_next_event = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await wait_next_event()

        assert result == {"error": "Unexpected error"}


# =============================================================================
# cleanup_task Tests
# =============================================================================


class TestCleanupTask:
    """Tests for the cleanup_task MCP tool."""

    async def test_cleanup_task_success(self):
        """cleanup_task returns {"success": True} on success."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.cleanup_task = AsyncMock(return_value={"success": True})

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await cleanup_task("abc123")

        assert result == {"success": True}
        mock_orchestrator.cleanup_task.assert_called_once_with("abc123", False)

    async def test_cleanup_task_with_delete_ref(self):
        """cleanup_task passes delete_ref parameter."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.cleanup_task = AsyncMock(return_value={"success": True})

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await cleanup_task("abc123", delete_ref=True)

        assert result == {"success": True}
        mock_orchestrator.cleanup_task.assert_called_once_with("abc123", True)

    async def test_cleanup_task_error_format(self):
        """cleanup_task returns {"error": str} on ToolError."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.cleanup_task = AsyncMock(
            side_effect=ToolError("Task not found: xyz789")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await cleanup_task("xyz789")

        assert result == {"error": "Task not found: xyz789"}

    async def test_cleanup_task_generic_error_format(self):
        """cleanup_task returns {"error": str} on generic exception."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.cleanup_task = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with patch("microvm_orchestrator.server.get_orchestrator", return_value=mock_orchestrator):
            result = await cleanup_task("abc123")

        assert result == {"error": "Unexpected error"}
