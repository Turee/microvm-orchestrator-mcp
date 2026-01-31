"""Task model and persistence."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidStateTransition(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, task_id: str, from_status: TaskStatus, to_status: TaskStatus):
        self.task_id = task_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Task {task_id}: invalid transition from {from_status.value} to {to_status.value}"
        )


# Valid state transitions for task lifecycle
# Format: {from_status: {allowed_to_statuses}}
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),  # Terminal state - no transitions allowed
    TaskStatus.FAILED: set(),      # Terminal state - no transitions allowed
}


@dataclass
class Task:
    """
    Represents a delegated task running in a microVM.

    State Machine:
        PENDING -> RUNNING  (task started successfully)
        PENDING -> FAILED   (task failed to start)
        RUNNING -> COMPLETED (task finished with exit code 0)
        RUNNING -> FAILED   (task finished with non-zero exit code or error)

    COMPLETED and FAILED are terminal states - no further transitions allowed.
    All state transitions are thread-safe.
    """

    id: str
    description: str
    status: TaskStatus
    slot: int
    repo_path: Path
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None

    # Thread lock for state transitions (not serialized)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self):
        # Ensure lock exists (needed when loading from disk)
        if not hasattr(self, "_lock") or self._lock is None:
            object.__setattr__(self, "_lock", threading.Lock())

    @classmethod
    def create(cls, description: str, slot: int, repo_path: Path) -> Task:
        """Create a new task with generated ID."""
        return cls(
            id=str(uuid.uuid4()),
            description=description,
            status=TaskStatus.PENDING,
            slot=slot,
            repo_path=repo_path,
        )

    @property
    def task_dir(self) -> Path:
        """Directory containing task files."""
        return self.repo_path / ".microvm" / "tasks" / self.id

    @property
    def isolated_repo_path(self) -> Path:
        """Path to isolated git repository."""
        return self.task_dir / "repo"

    @property
    def log_path(self) -> Path:
        """Path to serial console log."""
        return self.task_dir / "serial.log"

    @property
    def result_path(self) -> Path:
        """Path to result.json written by VM."""
        return self.task_dir / "result.json"

    @property
    def merge_result_path(self) -> Path:
        """Path to merge-result.json."""
        return self.task_dir / "merge-result.json"

    @property
    def task_json_path(self) -> Path:
        """Path to task.json metadata."""
        return self.task_dir / "task.json"

    @property
    def api_key_path(self) -> Path:
        """Path to temporary API key file."""
        return self.task_dir / ".api-key"

    def save(self) -> None:
        """Persist task metadata to disk."""
        self.task_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "slot": self.slot,
            "repo_path": str(self.repo_path),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "error": self.error,
        }
        self.task_json_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, task_dir: Path) -> Task:
        """Load task from disk."""
        task_json = task_dir / "task.json"
        data = json.loads(task_json.read_text())
        return cls(
            id=data["id"],
            description=data["description"],
            status=TaskStatus(data["status"]),
            slot=data["slot"],
            repo_path=Path(data["repo_path"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            pid=data.get("pid"),
            exit_code=data.get("exit_code"),
            error=data.get("error"),
        )

    def get_result(self) -> Optional[dict]:
        """Read result.json if it exists."""
        if self.result_path.exists():
            return json.loads(self.result_path.read_text())
        return None

    def get_merge_result(self) -> Optional[dict]:
        """Read merge-result.json if it exists."""
        if self.merge_result_path.exists():
            return json.loads(self.merge_result_path.read_text())
        return None

    def _can_transition_to(self, new_status: TaskStatus) -> bool:
        """Check if transition to new_status is valid from current status."""
        return new_status in VALID_TRANSITIONS.get(self.status, set())

    def _transition_to(self, new_status: TaskStatus) -> bool:
        """
        Attempt to transition to a new status.

        Thread-safe state transition that validates the transition is allowed.

        Args:
            new_status: The target status

        Returns:
            True if transition succeeded, False if transition was invalid
            (e.g., task already in terminal state)
        """
        with self._lock:
            if not self._can_transition_to(new_status):
                logger.warning(
                    "Task %s: ignoring invalid transition from %s to %s",
                    self.id,
                    self.status.value,
                    new_status.value,
                )
                return False

            self.status = new_status
            return True

    def is_terminal(self) -> bool:
        """Check if task is in a terminal state (COMPLETED or FAILED)."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)

    def mark_running(self, pid: int) -> bool:
        """
        Mark task as running with given PID.

        Thread-safe. Will be ignored if task has already completed/failed.

        Args:
            pid: Process ID of the running task

        Returns:
            True if status was updated, False if transition was invalid
        """
        with self._lock:
            if not self._can_transition_to(TaskStatus.RUNNING):
                logger.warning(
                    "Task %s: cannot mark as running, current status is %s",
                    self.id,
                    self.status.value,
                )
                return False

            self.status = TaskStatus.RUNNING
            self.started_at = datetime.now(timezone.utc)
            self.pid = pid
            self.save()
            return True

    def mark_completed(self, exit_code: int) -> bool:
        """
        Mark task as completed or failed based on exit code.

        Thread-safe. Will be ignored if task is already in terminal state.

        Args:
            exit_code: Process exit code (0 = completed, non-zero = failed)

        Returns:
            True if status was updated, False if transition was invalid
        """
        new_status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED

        with self._lock:
            if not self._can_transition_to(new_status):
                logger.warning(
                    "Task %s: cannot mark as %s, current status is %s",
                    self.id,
                    new_status.value,
                    self.status.value,
                )
                return False

            self.status = new_status
            self.completed_at = datetime.now(timezone.utc)
            self.exit_code = exit_code
            self.save()
            return True

    def mark_failed(self, error: str) -> bool:
        """
        Mark task as failed with error message.

        Thread-safe. Will be ignored if task is already in terminal state.

        Args:
            error: Error message describing the failure

        Returns:
            True if status was updated, False if transition was invalid
        """
        with self._lock:
            if not self._can_transition_to(TaskStatus.FAILED):
                logger.warning(
                    "Task %s: cannot mark as failed, current status is %s",
                    self.id,
                    self.status.value,
                )
                return False

            self.status = TaskStatus.FAILED
            self.completed_at = datetime.now(timezone.utc)
            self.error = error
            self.save()
            return True
