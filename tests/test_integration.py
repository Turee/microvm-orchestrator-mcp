"""Integration tests for multi-repo orchestration.

Tests that exercise the full orchestration flow with multiple repos registered
simultaneously, verifying slot assignment, repo isolation, and cross-repo lookups.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from microvm_orchestrator.core.git import MergeResult
from microvm_orchestrator.core.registry import RepoRegistry
from microvm_orchestrator.core.slots import SlotManager
from microvm_orchestrator.tools import Orchestrator, ToolError

from .fixtures.mocks import create_git_repo


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def multi_repo_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create an Orchestrator with two registered repos and isolated state."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")

    # Create two separate git repos
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    create_git_repo(repo_a)
    create_git_repo(repo_b)

    # Need a plugin dir with default.nix
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "default.nix").write_text("{}")

    # Isolated registry and slot manager
    registry_path = tmp_path / "allowed-repos.json"
    slots_path = tmp_path / "slot-assignments.json"

    with patch.object(Orchestrator, "_get_plugin_dir", return_value=plugin_dir):
        orch = Orchestrator(repo_path=repo_a)

    orch.registry = RepoRegistry(registry_path=registry_path)
    orch.slot_manager = SlotManager(assignments_path=slots_path)

    orch.registry.allow(repo_a, alias="repo-a")
    orch.registry.allow(repo_b, alias="repo-b")

    return orch, repo_a, repo_b


# =============================================================================
# Multi-Repo Task Tests
# =============================================================================


@pytest.mark.integration
class TestMultiRepoTasks:
    """Tests for running tasks across multiple registered repos."""

    async def test_run_tasks_on_two_repos_assigns_different_slots(
        self, multi_repo_orchestrator, mock_orchestrator_deps
    ):
        """Tasks on different repos get assigned different slots."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        result_a = await orch.run_task("Task A", repo="repo-a")
        result_b = await orch.run_task("Task B", repo="repo-b")

        assert "task_id" in result_a
        assert "task_id" in result_b
        assert result_a["task_id"] != result_b["task_id"]

        info_a = orch.get_task_info(result_a["task_id"])
        info_b = orch.get_task_info(result_b["task_id"])

        assert info_a["slot"] != info_b["slot"]
        assert info_a["repo_path"] == str(repo_a)
        assert info_b["repo_path"] == str(repo_b)

    async def test_tasks_run_in_correct_repos(
        self, multi_repo_orchestrator, mock_orchestrator_deps
    ):
        """Each task's paths point to the correct repo."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        result_a = await orch.run_task("Task A", repo="repo-a")
        result_b = await orch.run_task("Task B", repo="repo-b")

        info_a = orch.get_task_info(result_a["task_id"])
        info_b = orch.get_task_info(result_b["task_id"])

        # repo_path should match the registered repo
        assert info_a["repo_path"] == str(repo_a)
        assert info_b["repo_path"] == str(repo_b)

        # isolated_repo_path should be under the correct repo's .microvm/tasks/
        assert str(repo_a) in info_a["isolated_repo_path"]
        assert str(repo_b) in info_b["isolated_repo_path"]

        # task.json should be saved under the correct repo
        task_a = orch._tasks[result_a["task_id"]]
        task_b = orch._tasks[result_b["task_id"]]
        assert task_a.task_json_path.exists()
        assert task_b.task_json_path.exists()
        assert str(repo_a) in str(task_a.task_json_path)
        assert str(repo_b) in str(task_b.task_json_path)

    async def test_unknown_repo_returns_error(
        self, multi_repo_orchestrator, mock_orchestrator_deps
    ):
        """run_task with unknown repo alias raises ToolError."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        # Capture active tasks before the call
        active_before = orch.slot_manager.get_active_tasks()

        with pytest.raises(ToolError, match="not registered"):
            await orch.run_task("Test", repo="nonexistent")

        # No slot should have been consumed
        active_after = orch.slot_manager.get_active_tasks()
        assert active_after == active_before

    async def test_list_repos_shows_registered_repos(
        self, multi_repo_orchestrator
    ):
        """Registry lists both registered repos with correct aliases and paths."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        repos = orch.registry.list()

        assert "repo-a" in repos
        assert "repo-b" in repos
        assert repos["repo-a"]["path"] == str(repo_a)
        assert repos["repo-b"]["path"] == str(repo_b)

    async def test_list_slots_reflects_active_tasks(
        self, multi_repo_orchestrator, mock_orchestrator_deps
    ):
        """SlotManager correctly reports active and available slots."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        # Initially all slots available
        assert len(orch.slot_manager.get_active_tasks()) == 0
        assert len(orch.slot_manager.get_available_slots()) == orch.slot_manager.max_slots

        result_a = await orch.run_task("Task A", repo="repo-a")
        result_b = await orch.run_task("Task B", repo="repo-b")

        active = orch.slot_manager.get_active_tasks()
        assert len(active) == 2
        assert result_a["task_id"] in active.values()
        assert result_b["task_id"] in active.values()

        available = orch.slot_manager.get_available_slots()
        assert len(available) == orch.slot_manager.max_slots - 2

    async def test_slot_released_after_task_exit(
        self, multi_repo_orchestrator, mock_orchestrator_deps
    ):
        """Slot is released after _on_task_exit and affinity returns the same slot."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        result = await orch.run_task("Task A", repo="repo-a")
        task_id = result["task_id"]
        task = orch._tasks[task_id]
        original_slot = task.slot

        assert len(orch.slot_manager.get_active_tasks()) == 1

        # Simulate task exit
        with patch("microvm_orchestrator.tools.merge_task_commits") as mock_merge:
            mock_merge.return_value = MergeResult(merged=True, method="fast-forward", commits=1)
            orch._on_task_exit(task, exit_code=0)

        assert len(orch.slot_manager.get_active_tasks()) == 0

        # Run another task on the same repo — should get the same slot (affinity)
        result2 = await orch.run_task("Task A2", repo="repo-a")
        task2 = orch._tasks[result2["task_id"]]
        assert task2.slot == original_slot

    async def test_task_lookup_across_repos(
        self, multi_repo_orchestrator, mock_orchestrator_deps
    ):
        """get_task_info correctly finds tasks across different repos."""
        orch, repo_a, repo_b = multi_repo_orchestrator

        result_a = await orch.run_task("Task A", repo="repo-a")
        result_b = await orch.run_task("Task B", repo="repo-b")

        # Both should be retrievable
        info_a = orch.get_task_info(result_a["task_id"])
        info_b = orch.get_task_info(result_b["task_id"])

        assert info_a["task_id"] == result_a["task_id"]
        assert info_b["task_id"] == result_b["task_id"]
        assert info_a["description"] == "Task A"
        assert info_b["description"] == "Task B"

        # Clear in-memory cache — force disk lookup via _get_task
        orch._tasks.clear()

        info_a_disk = orch.get_task_info(result_a["task_id"])
        info_b_disk = orch.get_task_info(result_b["task_id"])

        assert info_a_disk["task_id"] == result_a["task_id"]
        assert info_b_disk["task_id"] == result_b["task_id"]
        assert info_a_disk["repo_path"] == str(repo_a)
        assert info_b_disk["repo_path"] == str(repo_b)
