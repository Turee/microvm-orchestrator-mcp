"""Tests for Task state machine (core/task.py)."""

from __future__ import annotations

import concurrent.futures
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from microvm_orchestrator.core.task import (
    Task,
    TaskStatus,
    InvalidStateTransition,
    VALID_TRANSITIONS,
)


# =============================================================================
# Task Creation Tests
# =============================================================================


class TestTaskCreation:
    """Tests for Task.create() factory method."""

    def test_initial_state_pending(self, tmp_project: Path):
        """New task starts in PENDING state."""
        task = Task.create(
            description="Test description",
            slot=1,
            project_root=tmp_project,
        )

        assert task.status == TaskStatus.PENDING
        assert task.description == "Test description"
        assert task.slot == 1
        assert task.project_root == tmp_project

    def test_create_generates_uuid(self, tmp_project: Path):
        """Task.create() generates unique IDs."""
        task1 = Task.create("task1", slot=1, project_root=tmp_project)
        task2 = Task.create("task2", slot=2, project_root=tmp_project)

        assert task1.id != task2.id
        # UUID format check
        assert len(task1.id) == 36
        assert task1.id.count("-") == 4

    def test_create_with_mocked_uuid(self, tmp_project: Path, mock_uuid, fixed_uuid: str):
        """Task.create() uses uuid.uuid4 for ID generation."""
        task = Task.create("test", slot=1, project_root=tmp_project)

        assert task.id == fixed_uuid


# =============================================================================
# State Transition Tests
# =============================================================================


class TestStateTransitions:
    """Tests for valid state transitions."""

    def test_transition_pending_to_running(self, sample_task: Task):
        """PENDING → RUNNING is a valid transition."""
        assert sample_task.status == TaskStatus.PENDING

        result = sample_task.mark_running(pid=12345)

        assert result is True
        assert sample_task.status == TaskStatus.RUNNING
        assert sample_task.pid == 12345

    def test_transition_running_to_completed(self, running_task: Task):
        """RUNNING → COMPLETED when exit code is 0."""
        result = running_task.mark_completed(exit_code=0)

        assert result is True
        assert running_task.status == TaskStatus.COMPLETED
        assert running_task.exit_code == 0

    def test_transition_running_to_failed(self, running_task: Task):
        """RUNNING → FAILED when exit code is non-zero."""
        result = running_task.mark_completed(exit_code=1)

        assert result is True
        assert running_task.status == TaskStatus.FAILED
        assert running_task.exit_code == 1

    def test_transition_pending_to_failed(self, sample_task: Task):
        """PENDING → FAILED is valid (startup failure)."""
        result = sample_task.mark_failed(error="Failed to start VM")

        assert result is True
        assert sample_task.status == TaskStatus.FAILED
        assert sample_task.error == "Failed to start VM"


class TestInvalidTransitions:
    """Tests for invalid state transitions."""

    def test_invalid_transition_completed_to_running(self, completed_task: Task):
        """COMPLETED → RUNNING returns False (terminal state)."""
        result = completed_task.mark_running(pid=99999)

        assert result is False
        assert completed_task.status == TaskStatus.COMPLETED
        assert completed_task.pid != 99999

    def test_invalid_transition_failed_to_running(self, failed_task: Task):
        """FAILED → RUNNING returns False (terminal state)."""
        result = failed_task.mark_running(pid=99999)

        assert result is False
        assert failed_task.status == TaskStatus.FAILED

    def test_invalid_transition_completed_to_completed(self, completed_task: Task):
        """COMPLETED → COMPLETED returns False (terminal state)."""
        result = completed_task.mark_completed(exit_code=0)

        assert result is False
        assert completed_task.status == TaskStatus.COMPLETED

    def test_invalid_transition_failed_to_failed(self, failed_task: Task):
        """FAILED → FAILED returns False (terminal state)."""
        result = failed_task.mark_failed(error="Another error")

        assert result is False
        assert failed_task.status == TaskStatus.FAILED

    def test_invalid_transition_running_to_running(self, running_task: Task):
        """RUNNING → RUNNING returns False."""
        original_pid = running_task.pid
        result = running_task.mark_running(pid=99999)

        assert result is False
        assert running_task.status == TaskStatus.RUNNING
        assert running_task.pid == original_pid

    def test_valid_transitions_table(self):
        """Verify VALID_TRANSITIONS constant is correct."""
        assert VALID_TRANSITIONS[TaskStatus.PENDING] == {TaskStatus.RUNNING, TaskStatus.FAILED}
        assert VALID_TRANSITIONS[TaskStatus.RUNNING] == {TaskStatus.COMPLETED, TaskStatus.FAILED}
        assert VALID_TRANSITIONS[TaskStatus.COMPLETED] == set()
        assert VALID_TRANSITIONS[TaskStatus.FAILED] == set()


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Tests for thread-safe state transitions."""

    def test_thread_safe_state_updates(self, tmp_project: Path):
        """Concurrent mark_running() calls are thread-safe."""
        # Create many tasks to test
        tasks = [
            Task.create(f"task-{i}", slot=i, project_root=tmp_project)
            for i in range(10)
        ]

        successful_transitions = []
        lock = threading.Lock()

        def mark_running(task: Task, pid: int) -> None:
            result = task.mark_running(pid=pid)
            if result:
                with lock:
                    successful_transitions.append((task.id, pid))

        # Try to mark each task as running from multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for task in tasks:
                # Submit multiple mark_running calls for same task
                for pid in range(100, 105):
                    futures.append(executor.submit(mark_running, task, pid))

            concurrent.futures.wait(futures)

        # Each task should have transitioned exactly once
        task_ids = [t[0] for t in successful_transitions]
        assert len(task_ids) == len(tasks)
        assert len(set(task_ids)) == len(tasks)  # All unique

        # All tasks should be in RUNNING state
        for task in tasks:
            assert task.status == TaskStatus.RUNNING

    def test_concurrent_terminal_transitions(self, tmp_project: Path):
        """Only one terminal transition succeeds under concurrent access."""
        task = Task.create("concurrent-test", slot=1, project_root=tmp_project)
        task.mark_running(pid=1234)

        results = {"completed": 0, "failed": 0}
        lock = threading.Lock()

        def try_complete() -> None:
            if task.mark_completed(exit_code=0):
                with lock:
                    results["completed"] += 1

        def try_fail() -> None:
            if task.mark_failed(error="Test error"):
                with lock:
                    results["failed"] += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for _ in range(10):
                futures.append(executor.submit(try_complete))
                futures.append(executor.submit(try_fail))

            concurrent.futures.wait(futures)

        # Only one transition should have succeeded
        total_successes = results["completed"] + results["failed"]
        assert total_successes == 1


# =============================================================================
# Persistence Tests
# =============================================================================


class TestPersistence:
    """Tests for save/load operations."""

    def test_save_load_roundtrip(self, sample_task: Task):
        """Task can be saved and loaded from disk."""
        sample_task.mark_running(pid=5678)
        sample_task.mark_completed(exit_code=0)

        # Save to disk
        sample_task.save()

        # Load from disk
        loaded = Task.load(sample_task.task_dir)

        assert loaded.id == sample_task.id
        assert loaded.description == sample_task.description
        assert loaded.status == sample_task.status
        assert loaded.slot == sample_task.slot
        assert loaded.pid == sample_task.pid
        assert loaded.exit_code == sample_task.exit_code

    def test_save_creates_directory(self, sample_task: Task):
        """save() creates task directory if it doesn't exist."""
        assert not sample_task.task_dir.exists()

        sample_task.save()

        assert sample_task.task_dir.exists()
        assert sample_task.task_json_path.exists()

    def test_save_format(self, sample_task: Task):
        """save() writes valid JSON with expected fields."""
        sample_task.save()

        data = json.loads(sample_task.task_json_path.read_text())

        assert data["id"] == sample_task.id
        assert data["description"] == sample_task.description
        assert data["status"] == "pending"
        assert data["slot"] == sample_task.slot
        assert data["project_root"] == str(sample_task.project_root)
        assert "created_at" in data

    def test_load_restores_lock(self, sample_task: Task):
        """Loaded task has a working thread lock."""
        sample_task.save()
        loaded = Task.load(sample_task.task_dir)

        # Lock should work (not raise)
        result = loaded.mark_running(pid=9999)
        assert result is True
        assert loaded.status == TaskStatus.RUNNING


# =============================================================================
# Timestamp Tests
# =============================================================================


class TestTimestamps:
    """Tests for timestamp management."""

    def test_created_at_set_on_init(self, tmp_project: Path):
        """created_at is set when Task is created."""
        before = datetime.now(timezone.utc)
        task = Task.create("test", slot=1, project_root=tmp_project)
        after = datetime.now(timezone.utc)

        assert task.created_at is not None
        assert before <= task.created_at <= after

    def test_started_at_set_on_running(self, sample_task: Task):
        """started_at is set when task transitions to RUNNING."""
        assert sample_task.started_at is None

        before = datetime.now(timezone.utc)
        sample_task.mark_running(pid=1234)
        after = datetime.now(timezone.utc)

        assert sample_task.started_at is not None
        assert before <= sample_task.started_at <= after

    def test_completed_at_set_on_completion(self, running_task: Task):
        """completed_at is set when task transitions to COMPLETED."""
        assert running_task.completed_at is None

        before = datetime.now(timezone.utc)
        running_task.mark_completed(exit_code=0)
        after = datetime.now(timezone.utc)

        assert running_task.completed_at is not None
        assert before <= running_task.completed_at <= after

    def test_completed_at_set_on_failure(self, running_task: Task):
        """completed_at is set when task transitions to FAILED."""
        assert running_task.completed_at is None

        before = datetime.now(timezone.utc)
        running_task.mark_failed(error="Test error")
        after = datetime.now(timezone.utc)

        assert running_task.completed_at is not None
        assert before <= running_task.completed_at <= after

    def test_timestamps_preserved_in_roundtrip(
        self, sample_task: Task, mock_datetime, frozen_time: datetime
    ):
        """Timestamps are correctly saved and loaded."""
        sample_task.mark_running(pid=1234)
        sample_task.mark_completed(exit_code=0)
        sample_task.save()

        loaded = Task.load(sample_task.task_dir)

        # Note: fromisoformat parsing is used, so compare isoformat strings
        assert loaded.created_at.isoformat() == sample_task.created_at.isoformat()
        assert loaded.started_at.isoformat() == sample_task.started_at.isoformat()
        assert loaded.completed_at.isoformat() == sample_task.completed_at.isoformat()


# =============================================================================
# Path Property Tests
# =============================================================================


class TestPathProperties:
    """Tests for task directory path properties."""

    def test_task_dir(self, sample_task: Task, fixed_uuid: str):
        """task_dir returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid
        assert sample_task.task_dir == expected

    def test_repo_path(self, sample_task: Task, fixed_uuid: str):
        """repo_path returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid / "repo"
        assert sample_task.repo_path == expected

    def test_log_path(self, sample_task: Task, fixed_uuid: str):
        """log_path returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid / "serial.log"
        assert sample_task.log_path == expected

    def test_result_path(self, sample_task: Task, fixed_uuid: str):
        """result_path returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid / "result.json"
        assert sample_task.result_path == expected

    def test_merge_result_path(self, sample_task: Task, fixed_uuid: str):
        """merge_result_path returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid / "merge-result.json"
        assert sample_task.merge_result_path == expected

    def test_task_json_path(self, sample_task: Task, fixed_uuid: str):
        """task_json_path returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid / "task.json"
        assert sample_task.task_json_path == expected

    def test_api_key_path(self, sample_task: Task, fixed_uuid: str):
        """api_key_path returns correct path."""
        expected = sample_task.project_root / ".microvm" / "tasks" / fixed_uuid / ".api-key"
        assert sample_task.api_key_path == expected


# =============================================================================
# Result Reading Tests
# =============================================================================


class TestResultReading:
    """Tests for reading result files."""

    def test_get_result_when_exists(self, sample_task: Task):
        """get_result() returns parsed JSON when file exists."""
        sample_task.task_dir.mkdir(parents=True)
        result_data = {"success": True, "summary": "Test passed"}
        sample_task.result_path.write_text(json.dumps(result_data))

        result = sample_task.get_result()

        assert result == result_data

    def test_get_result_when_missing(self, sample_task: Task):
        """get_result() returns None when file doesn't exist."""
        result = sample_task.get_result()

        assert result is None

    def test_get_merge_result_when_exists(self, sample_task: Task):
        """get_merge_result() returns parsed JSON when file exists."""
        sample_task.task_dir.mkdir(parents=True)
        merge_data = {"merged": True, "method": "fast-forward"}
        sample_task.merge_result_path.write_text(json.dumps(merge_data))

        result = sample_task.get_merge_result()

        assert result == merge_data

    def test_get_merge_result_when_missing(self, sample_task: Task):
        """get_merge_result() returns None when file doesn't exist."""
        result = sample_task.get_merge_result()

        assert result is None


# =============================================================================
# Terminal State Tests
# =============================================================================


class TestTerminalState:
    """Tests for is_terminal() method."""

    def test_pending_not_terminal(self, sample_task: Task):
        """PENDING is not a terminal state."""
        assert sample_task.is_terminal() is False

    def test_running_not_terminal(self, running_task: Task):
        """RUNNING is not a terminal state."""
        assert running_task.is_terminal() is False

    def test_completed_is_terminal(self, completed_task: Task):
        """COMPLETED is a terminal state."""
        assert completed_task.is_terminal() is True

    def test_failed_is_terminal(self, failed_task: Task):
        """FAILED is a terminal state."""
        assert failed_task.is_terminal() is True
