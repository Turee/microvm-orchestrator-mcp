"""Tests for Orchestrator (tools.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from microvm_orchestrator.tools import Orchestrator, ToolError
from microvm_orchestrator.core.events import EventType
from microvm_orchestrator.core.git import MergeResult


# =============================================================================
# run_task Tests
# =============================================================================


class TestRunTask:
    """Tests for Orchestrator.run_task() method."""

    async def test_run_task_creates_task(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """run_task returns task_id and creates Task in RUNNING state."""
        result = await orchestrator.run_task("Test description", slot=1)

        assert "task_id" in result
        task_id = result["task_id"]
        assert len(task_id) == 36  # UUID format
        assert task_id in orchestrator._tasks
        assert task_id in orchestrator._processes

    async def test_run_task_detects_git_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mock_orchestrator_deps
    ):
        """run_task finds .git directory when repo_path not specified."""
        # Create nested structure with .git at root
        git_root = tmp_path / "repo"
        subdir = git_root / "src" / "deep"
        subdir.mkdir(parents=True)
        (git_root / ".git").mkdir()
        (git_root / "default.nix").write_text("{}")

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.chdir(subdir)

        with patch.object(Orchestrator, "_get_plugin_dir", return_value=git_root):
            orch = Orchestrator()  # No repo_path - should detect

        assert orch.repo_path == git_root

    async def test_run_task_api_key_from_env(
        self, orchestrator: Orchestrator, mock_orchestrator_deps, monkeypatch: pytest.MonkeyPatch
    ):
        """run_task uses ANTHROPIC_API_KEY from environment."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-api-key")

        await orchestrator.run_task("Test", slot=1)

        # Verify write_task_files was called with the api key
        mock_orchestrator_deps["write_files"].assert_called()
        call_args = mock_orchestrator_deps["write_files"].call_args
        assert call_args[0][1] == "env-api-key"  # api_key positional arg

    async def test_run_task_marks_failed_on_error(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """run_task marks task as FAILED and emits event on exception."""
        mock_orchestrator_deps["setup_repo"].side_effect = RuntimeError("Git error")

        with pytest.raises(ToolError, match="Failed to start task"):
            await orchestrator.run_task("Test", slot=1)

        # Should have emitted a failed event
        event = orchestrator.event_queue._try_pop()
        assert event is not None
        assert event.event_type == EventType.FAILED
        assert "Git error" in event.error


# =============================================================================
# get_task_info Tests
# =============================================================================


class TestGetTaskInfo:
    """Tests for Orchestrator.get_task_info() method."""

    async def test_get_task_info_running(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """get_task_info returns 'running' status while VM active."""
        result = await orchestrator.run_task("Test", slot=1)
        task_id = result["task_id"]

        info = orchestrator.get_task_info(task_id)

        assert info["status"] == "running"
        assert info["task_id"] == task_id
        assert info["pid"] is not None

    async def test_get_task_info_completed(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """get_task_info includes result.json for completed task."""
        # Start task then simulate completion
        result = await orchestrator.run_task("Test", slot=1)
        task_id = result["task_id"]

        # Remove from processes (simulates VM exit)
        orchestrator._processes.pop(task_id)

        # Write result.json
        task = orchestrator._tasks[task_id]
        task.task_dir.mkdir(parents=True, exist_ok=True)
        task.result_path.write_text('{"success": true, "summary": "Done"}')

        info = orchestrator.get_task_info(task_id)

        assert info["status"] == "completed"
        assert info["result"]["success"] is True
        assert info["result"]["summary"] == "Done"


# =============================================================================
# wait_next_event Tests
# =============================================================================


class TestWaitNextEvent:
    """Tests for Orchestrator.wait_next_event() method."""

    async def test_wait_next_event_returns(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """wait_next_event returns event when one is emitted."""
        result = await orchestrator.run_task("Test", slot=1)
        task_id = result["task_id"]

        # Emit an event
        event = orchestrator.event_queue.create_completed_event(
            task_id=task_id,
            exit_code=0,
            result={"success": True},
        )
        orchestrator.event_queue.emit(event)

        result = await orchestrator.wait_next_event(timeout_ms=1000)

        assert result["task_id"] == task_id
        assert result["event"] == "completed"

    async def test_wait_next_event_no_tasks(self, orchestrator: Orchestrator):
        """wait_next_event returns early when no running tasks."""
        # No tasks started
        result = await orchestrator.wait_next_event(timeout_ms=10000)

        assert result == {"no_running_tasks": True}


# =============================================================================
# cleanup_task Tests
# =============================================================================


class TestCleanupTask:
    """Tests for Orchestrator.cleanup_task() method."""

    async def test_cleanup_task_removes_files(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """cleanup_task deletes task directory."""
        result = await orchestrator.run_task("Test", slot=1)
        task_id = result["task_id"]
        task = orchestrator._tasks[task_id]

        # Create task directory
        task.task_dir.mkdir(parents=True, exist_ok=True)
        (task.task_dir / "task.json").write_text("{}")
        assert task.task_dir.exists()

        await orchestrator.cleanup_task(task_id)

        assert not task.task_dir.exists()
        assert task_id not in orchestrator._tasks
        assert task_id not in orchestrator._processes

    async def test_cleanup_task_deletes_ref(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """cleanup_task removes git ref when delete_ref=True."""
        result = await orchestrator.run_task("Test", slot=1)
        task_id = result["task_id"]
        task = orchestrator._tasks[task_id]
        task.task_dir.mkdir(parents=True, exist_ok=True)

        await orchestrator.cleanup_task(task_id, delete_ref=True)

        mock_orchestrator_deps["cleanup_ref"].assert_called_once_with(
            orchestrator.repo_path, task_id
        )


# =============================================================================
# Concurrent Tasks Tests
# =============================================================================


class TestConcurrentTasks:
    """Tests for running multiple tasks concurrently."""

    async def test_concurrent_tasks(
        self, orchestrator: Orchestrator, mock_orchestrator_deps
    ):
        """Multiple tasks can run in different slots."""
        result1 = await orchestrator.run_task("Task 1", slot=1)
        result2 = await orchestrator.run_task("Task 2", slot=2)
        result3 = await orchestrator.run_task("Task 3", slot=3)

        assert result1["task_id"] != result2["task_id"] != result3["task_id"]
        assert len(orchestrator._processes) == 3

        # All tasks should be registered
        assert result1["task_id"] in orchestrator._tasks
        assert result2["task_id"] in orchestrator._tasks
        assert result3["task_id"] in orchestrator._tasks

        # Get info for each
        info1 = orchestrator.get_task_info(result1["task_id"])
        info2 = orchestrator.get_task_info(result2["task_id"])
        info3 = orchestrator.get_task_info(result3["task_id"])

        assert info1["slot"] == 1
        assert info2["slot"] == 2
        assert info3["slot"] == 3


# =============================================================================
# _on_task_exit Callback Tests
# =============================================================================


class TestOnTaskExit:
    """Tests for Orchestrator._on_task_exit() callback."""

    def test_on_task_exit_emits_event(
        self, orchestrator: Orchestrator, running_task
    ):
        """_on_task_exit emits completion event to queue."""
        orchestrator._tasks[running_task.id] = running_task

        # Create start-ref file that _on_task_exit expects
        running_task.task_dir.mkdir(parents=True, exist_ok=True)

        with patch("microvm_orchestrator.tools.merge_task_commits") as mock_merge:
            mock_merge.return_value = MergeResult(merged=True, method="fast-forward", commits=1)

            orchestrator._on_task_exit(running_task, exit_code=0)

        event = orchestrator.event_queue._try_pop()
        assert event is not None
        assert event.task_id == running_task.id
        assert event.exit_code == 0

    def test_on_task_exit_attempts_merge_on_success(
        self, orchestrator: Orchestrator, running_task
    ):
        """_on_task_exit attempts merge when task succeeded."""
        orchestrator._tasks[running_task.id] = running_task
        running_task.task_dir.mkdir(parents=True, exist_ok=True)
        running_task.result_path.write_text('{"success": true}')
        (running_task.task_dir / "start-ref").write_text("abc123")

        with patch("microvm_orchestrator.tools.merge_task_commits") as mock_merge:
            mock_merge.return_value = MergeResult(merged=True, method="fast-forward", commits=3)

            orchestrator._on_task_exit(running_task, exit_code=0)

            mock_merge.assert_called_once()


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestEdgeCases:
    """Tests for error handling and edge cases."""

    async def test_run_task_no_api_key_raises(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """run_task raises ToolError when no API key available."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        (tmp_project / "default.nix").write_text("{}")

        with patch.object(Orchestrator, "_get_plugin_dir", return_value=tmp_project):
            orch = Orchestrator(repo_path=tmp_project)

        # Mock keychain to also fail
        with patch("subprocess.run", side_effect=Exception("No keychain")):
            with pytest.raises(ToolError, match="No API key found"):
                await orch.run_task("Test", slot=1)

    def test_get_task_info_not_found(self, orchestrator: Orchestrator):
        """get_task_info raises ToolError for unknown task."""
        with pytest.raises(ToolError, match="Task not found"):
            orchestrator.get_task_info("nonexistent-task-id")

    def test_get_task_loads_from_disk(
        self, orchestrator: Orchestrator, tmp_project: Path
    ):
        """get_task_info loads task from disk if not in memory."""
        # Create task on disk
        task_id = "disk-task-123"
        task_dir = tmp_project / ".microvm" / "tasks" / task_id
        task_dir.mkdir(parents=True)
        task_json = {
            "id": task_id,
            "description": "Loaded from disk",
            "status": "completed",
            "slot": 1,
            "repo_path": str(tmp_project),
            "created_at": "2024-01-15T12:00:00+00:00",
        }
        (task_dir / "task.json").write_text(json.dumps(task_json))
        (task_dir / "result.json").write_text('{"success": true}')

        info = orchestrator.get_task_info(task_id)

        assert info["task_id"] == task_id
        assert info["description"] == "Loaded from disk"

    def test_list_tasks(self, orchestrator: Orchestrator, tmp_project: Path):
        """list_tasks returns all tasks from disk."""
        # Create multiple tasks on disk
        for i in range(3):
            task_dir = tmp_project / ".microvm" / "tasks" / f"task-{i}"
            task_dir.mkdir(parents=True)
            (task_dir / "task.json").write_text(json.dumps({
                "id": f"task-{i}",
                "description": f"Task {i}",
                "status": "completed",
                "slot": i,
                "repo_path": str(tmp_project),
                "created_at": "2024-01-15T12:00:00+00:00",
            }))

        tasks = orchestrator.list_tasks()

        assert len(tasks) == 3
