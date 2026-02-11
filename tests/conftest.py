"""Shared pytest fixtures for microvm-orchestrator-mcp tests."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from microvm_orchestrator.core.task import Task, TaskStatus
from microvm_orchestrator.core.events import EventQueue, TaskEvent, EventType
from microvm_orchestrator.core.git import MergeResult
from microvm_orchestrator.tools import Orchestrator

from .fixtures.mocks import (
    SubprocessMock,
    PopenMock,
    PTYMock,
    create_git_repo,
)


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (may be slow)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (e.g., e2e tests with real VMs)"
    )
    config.addinivalue_line(
        "markers", "nix: marks tests that require nix-instantiate (Nix evaluation smoke tests)"
    )


# =============================================================================
# Time and UUID Fixtures
# =============================================================================

@pytest.fixture
def frozen_time() -> datetime:
    """A fixed datetime for deterministic tests."""
    return datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixed_uuid() -> str:
    """A fixed UUID for deterministic tests."""
    return "12345678-1234-5678-1234-567812345678"


@pytest.fixture
def mock_datetime(frozen_time: datetime):
    """Mock datetime.now to return frozen_time."""
    with patch("microvm_orchestrator.core.task.datetime") as mock_dt:
        mock_dt.now.return_value = frozen_time
        mock_dt.fromisoformat = datetime.fromisoformat
        yield mock_dt


@pytest.fixture
def mock_uuid(fixed_uuid: str):
    """Mock uuid.uuid4 to return fixed_uuid."""
    with patch("microvm_orchestrator.core.task.uuid.uuid4") as mock:
        mock.return_value = MagicMock(__str__=lambda self: fixed_uuid)
        yield mock


# =============================================================================
# Temporary Directory Fixtures
# =============================================================================

@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with git repo."""
    project = tmp_path / "project"
    create_git_repo(project)
    return project


@pytest.fixture
def tmp_task_dir(tmp_path: Path) -> Path:
    """Create a temporary task directory."""
    task_dir = tmp_path / "tasks" / "test-task-id"
    task_dir.mkdir(parents=True)
    return task_dir


@pytest.fixture
def tmp_git_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Create two temporary git repos (original and task)."""
    original = tmp_path / "original"
    task_repo = tmp_path / "task-repo"
    create_git_repo(original)
    return original, task_repo


# =============================================================================
# Task Fixtures
# =============================================================================

@pytest.fixture
def sample_task(tmp_project: Path, fixed_uuid: str) -> Task:
    """Create a sample task in PENDING state."""
    return Task(
        id=fixed_uuid,
        description="Test task description",
        status=TaskStatus.PENDING,
        slot=1,
        repo_path=tmp_project,
    )


@pytest.fixture
def running_task(tmp_project: Path, fixed_uuid: str, frozen_time: datetime) -> Task:
    """Create a sample task in RUNNING state."""
    return Task(
        id=fixed_uuid,
        description="Test task description",
        status=TaskStatus.RUNNING,
        slot=1,
        repo_path=tmp_project,
        started_at=frozen_time,
        pid=12345,
    )


@pytest.fixture
def completed_task(tmp_project: Path, fixed_uuid: str, frozen_time: datetime) -> Task:
    """Create a sample task in COMPLETED state."""
    return Task(
        id=fixed_uuid,
        description="Test task description",
        status=TaskStatus.COMPLETED,
        slot=1,
        repo_path=tmp_project,
        started_at=frozen_time,
        completed_at=frozen_time,
        exit_code=0,
    )


@pytest.fixture
def failed_task(tmp_project: Path, fixed_uuid: str, frozen_time: datetime) -> Task:
    """Create a sample task in FAILED state."""
    return Task(
        id=fixed_uuid,
        description="Test task description",
        status=TaskStatus.FAILED,
        slot=1,
        repo_path=tmp_project,
        started_at=frozen_time,
        completed_at=frozen_time,
        exit_code=1,
        error="Task failed with error",
    )


# =============================================================================
# Event Queue Fixtures
# =============================================================================

@pytest.fixture
def event_queue() -> EventQueue:
    """Create a fresh EventQueue instance."""
    return EventQueue()


@pytest.fixture
def sample_completed_event(fixed_uuid: str, frozen_time: datetime) -> TaskEvent:
    """Create a sample completion event."""
    return TaskEvent(
        task_id=fixed_uuid,
        event_type=EventType.COMPLETED,
        timestamp=frozen_time,
        exit_code=0,
        result={"success": True, "summary": "Task completed"},
    )


@pytest.fixture
def sample_failed_event(fixed_uuid: str, frozen_time: datetime) -> TaskEvent:
    """Create a sample failure event."""
    return TaskEvent(
        task_id=fixed_uuid,
        event_type=EventType.FAILED,
        timestamp=frozen_time,
        error="Task execution failed",
    )


# =============================================================================
# Subprocess Mock Fixtures
# =============================================================================

@pytest.fixture
def subprocess_mock() -> Generator[SubprocessMock, None, None]:
    """Provide a SubprocessMock context manager."""
    with SubprocessMock() as mock:
        yield mock


@pytest.fixture
def git_mock(subprocess_mock: SubprocessMock) -> SubprocessMock:
    """SubprocessMock preconfigured with common git responses."""
    # Configure common git command responses
    subprocess_mock.set_git_response(
        ["rev-parse", "HEAD"],
        stdout="abc123def456\n",
    )
    subprocess_mock.set_git_response(
        ["symbolic-ref", "--short", "HEAD"],
        stdout="main\n",
    )
    subprocess_mock.set_git_response(
        ["init", "--quiet"],
    )
    subprocess_mock.set_git_response(
        ["remote", "add", "origin"],
    )
    subprocess_mock.set_git_response(
        ["fetch", "origin", "--quiet"],
    )
    # Default success for other git commands
    subprocess_mock.set_default(returncode=0)
    return subprocess_mock


# =============================================================================
# PTY and Process Mock Fixtures
# =============================================================================

@pytest.fixture
def pty_mock() -> PTYMock:
    """Create a PTYMock for VM process tests."""
    return PTYMock(output_data=b"VM boot log output\n")


@pytest.fixture
def popen_mock() -> PopenMock:
    """Create a PopenMock for VM process tests."""
    return PopenMock(pid=12345, returncode=0)


@pytest.fixture
def mock_vm_start(popen_mock: PopenMock, pty_mock: PTYMock):
    """Mock all dependencies needed for VMProcess.start()."""
    with patch("subprocess.Popen", return_value=popen_mock) as mock_popen, \
         patch("pty.openpty", pty_mock.openpty), \
         patch("os.read", pty_mock.read), \
         patch("os.close", pty_mock.close), \
         patch("select.select", pty_mock.select):
        yield {
            "popen": mock_popen,
            "pty": pty_mock,
            "process": popen_mock,
        }


# =============================================================================
# Git Operation Fixtures
# =============================================================================

@pytest.fixture
def merge_result_success() -> MergeResult:
    """Create a successful fast-forward merge result."""
    return MergeResult(
        merged=True,
        method="fast-forward",
        commits=3,
    )


@pytest.fixture
def merge_result_conflict() -> MergeResult:
    """Create a merge result with conflicts."""
    return MergeResult(
        merged=False,
        reason="conflicts",
        conflicts=["src/main.py", "tests/test_main.py"],
        task_ref="refs/tasks/test-task-id",
        commits=2,
    )


# =============================================================================
# Asyncio Fixtures
# =============================================================================

@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Orchestrator Fixtures
# =============================================================================

@pytest.fixture
def mock_orchestrator_env(tmp_project: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up environment for Orchestrator tests."""
    # Set API key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")

    return tmp_project


@pytest.fixture
def mock_orchestrator_deps():
    """Mock all dependencies needed for Orchestrator methods."""
    mock_vm = MagicMock()
    mock_vm.start_async = AsyncMock(return_value=12345)
    mock_vm.stop = MagicMock()

    with patch("microvm_orchestrator.tools.VMProcess", return_value=mock_vm) as vm_cls, \
         patch("microvm_orchestrator.tools.setup_isolated_repo_async", new_callable=AsyncMock) as setup_repo, \
         patch("microvm_orchestrator.tools.write_task_files") as write_files, \
         patch("microvm_orchestrator.tools.prepare_vm_env") as prep_env, \
         patch("microvm_orchestrator.tools.merge_task_commits") as merge, \
         patch("microvm_orchestrator.tools.cleanup_task_ref") as cleanup_ref:

        setup_repo.return_value = "abc123startref"
        prep_env.return_value = {"DELEGATE_TASK_DIR": "/tmp/task"}
        merge.return_value = MergeResult(merged=True, method="fast-forward", commits=3)

        yield {
            "vm_process_class": vm_cls,
            "vm_process": mock_vm,
            "setup_repo": setup_repo,
            "write_files": write_files,
            "prepare_env": prep_env,
            "merge": merge,
            "cleanup_ref": cleanup_ref,
        }


@pytest.fixture
def orchestrator(tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create Orchestrator with mocked plugin dir and registered test repo.

    Uses isolated temp paths for registry and slot assignments to prevent
    test interference.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    (tmp_project / "default.nix").write_text("{}")

    # Use temp paths for registry and slot assignments (isolated per test)
    from microvm_orchestrator.core.registry import RepoRegistry
    from microvm_orchestrator.core.slots import SlotManager

    registry_path = tmp_path / "allowed-repos.json"
    slots_path = tmp_path / "slot-assignments.json"

    with patch.object(Orchestrator, "_get_plugin_dir", return_value=tmp_project):
        orch = Orchestrator(repo_path=tmp_project)
        # Replace with isolated registry and slot manager
        orch.registry = RepoRegistry(registry_path=registry_path)
        orch.slot_manager = SlotManager(assignments_path=slots_path)
        # Register the tmp_project so tests can use run_task(desc, repo="project")
        orch.registry.allow(tmp_project, alias="project")
        return orch


# =============================================================================
# Integration Test Fixtures
# =============================================================================

@pytest.fixture
def real_git_repo(tmp_path: Path) -> Path:
    """Create a real git repository for integration tests."""
    repo = tmp_path / "real-repo"
    repo.mkdir()

    # Initialize real git repo
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Create initial commit
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    return repo
