"""Tests for Git operations (core/git.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from microvm_orchestrator.core.git import (
    MergeResult,
    run_git,
    get_current_ref,
    get_current_branch,
    setup_isolated_repo,
    setup_isolated_repo_async,
    merge_task_commits,
    cleanup_task_ref,
)

from .fixtures.mocks import SubprocessMock


# =============================================================================
# run_git Tests
# =============================================================================


class TestRunGit:
    """Tests for the run_git subprocess wrapper."""

    def test_run_git_success(self, tmp_path: Path, subprocess_mock: SubprocessMock):
        """run_git returns CompletedProcess on success."""
        subprocess_mock.set_git_response(
            ["status"],
            returncode=0,
            stdout="On branch main\nnothing to commit\n",
        )

        result = run_git(["status"], tmp_path)

        assert result.returncode == 0
        assert "On branch main" in result.stdout
        assert ["git", "status"] in subprocess_mock.calls

    def test_run_git_failure(self, tmp_path: Path, subprocess_mock: SubprocessMock):
        """run_git raises CalledProcessError on non-zero exit with check=True."""
        subprocess_mock.set_git_response(
            ["push"],
            returncode=1,
            stderr="error: failed to push\n",
        )

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_git(["push"], tmp_path, check=True)

        assert exc_info.value.returncode == 1

    def test_run_git_failure_no_check(self, tmp_path: Path, subprocess_mock: SubprocessMock):
        """run_git returns result without raising when check=False."""
        subprocess_mock.set_git_response(
            ["push"],
            returncode=1,
            stderr="error: failed to push\n",
        )

        result = run_git(["push"], tmp_path, check=False)

        assert result.returncode == 1
        assert "failed to push" in result.stderr


# =============================================================================
# get_current_ref Tests
# =============================================================================


class TestGetCurrentRef:
    """Tests for get_current_ref function."""

    def test_get_current_ref(self, tmp_path: Path, subprocess_mock: SubprocessMock):
        """get_current_ref returns HEAD commit hash."""
        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout="abc123def456789012345678901234567890abcd\n",
        )

        result = get_current_ref(tmp_path)

        assert result == "abc123def456789012345678901234567890abcd"
        assert ["git", "rev-parse", "HEAD"] in subprocess_mock.calls


# =============================================================================
# get_current_branch Tests
# =============================================================================


class TestGetCurrentBranch:
    """Tests for get_current_branch function."""

    def test_get_current_branch(self, tmp_path: Path, subprocess_mock: SubprocessMock):
        """get_current_branch returns branch name when on a branch."""
        subprocess_mock.set_git_response(
            ["symbolic-ref", "--short", "HEAD"],
            stdout="main\n",
        )

        result = get_current_branch(tmp_path)

        assert result == "main"
        assert ["git", "symbolic-ref", "--short", "HEAD"] in subprocess_mock.calls

    def test_get_current_branch_detached(self, tmp_path: Path, subprocess_mock: SubprocessMock):
        """get_current_branch returns None when HEAD is detached."""
        subprocess_mock.set_git_response(
            ["symbolic-ref", "--short", "HEAD"],
            returncode=128,
            stderr="fatal: ref HEAD is not a symbolic ref\n",
        )

        result = get_current_branch(tmp_path)

        assert result is None


# =============================================================================
# setup_isolated_repo Tests
# =============================================================================


class TestSetupIsolatedRepo:
    """Tests for setup_isolated_repo function."""

    def test_setup_isolated_repo_success(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """setup_isolated_repo creates clone with remotes and returns start ref."""
        original_repo, task_repo = tmp_git_repos
        task_id = "test-task-123"

        # Set up mock responses for the git commands
        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout="abc123def456\n",
        )
        subprocess_mock.set_git_response(["init", "--quiet"])
        subprocess_mock.set_git_response(["remote", "add", "origin", str(original_repo)])
        subprocess_mock.set_git_response(["fetch", "origin", "--quiet"])
        subprocess_mock.set_git_response(
            ["checkout", "-b", f"task-{task_id}", "abc123def456", "--quiet"]
        )
        subprocess_mock.set_git_response(
            ["config", "user.email", f"claude-task-{task_id}@microvm.local"]
        )
        subprocess_mock.set_git_response(
            ["config", "user.name", f"Claude Task ({task_id})"]
        )
        subprocess_mock.set_default(returncode=0)

        result = setup_isolated_repo(original_repo, task_repo, task_id)

        assert result == "abc123def456"
        assert task_repo.exists()
        # Verify key git commands were called
        assert ["git", "init", "--quiet"] in subprocess_mock.calls
        assert ["git", "fetch", "origin", "--quiet"] in subprocess_mock.calls

    def test_setup_isolated_repo_fetch_failure_fallback(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """setup_isolated_repo falls back to archive when fetch fails."""
        original_repo, task_repo = tmp_git_repos
        task_id = "test-task-456"

        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout="def789abc123\n",
        )
        subprocess_mock.set_git_response(["init", "--quiet"])
        subprocess_mock.set_git_response(["remote", "add", "origin", str(original_repo)])
        # fetch fails
        subprocess_mock.set_git_response(
            ["fetch", "origin", "--quiet"],
            returncode=128,
            stderr="fatal: Could not read from remote repository.\n",
        )
        # Archive fallback commands
        subprocess_mock.set_response(
            ["git", "archive", "HEAD"],
            stdout="",  # Binary archive data mocked
        )
        subprocess_mock.set_response(["tar", "-x"])
        subprocess_mock.set_git_response(["add", "-A"])
        subprocess_mock.set_git_response(
            ["commit", "-m", f"Initial copy from def789abc123", "--quiet"]
        )
        subprocess_mock.set_git_response(
            ["checkout", "-b", f"task-{task_id}", "--quiet"]
        )
        subprocess_mock.set_git_response(
            ["config", "user.email", f"claude-task-{task_id}@microvm.local"]
        )
        subprocess_mock.set_git_response(
            ["config", "user.name", f"Claude Task ({task_id})"]
        )
        subprocess_mock.set_default(returncode=0)

        result = setup_isolated_repo(original_repo, task_repo, task_id)

        assert result == "def789abc123"
        # Verify archive fallback commands were called
        assert ["git", "archive", "HEAD"] in subprocess_mock.calls
        assert ["tar", "-x"] in subprocess_mock.calls
        assert ["git", "add", "-A"] in subprocess_mock.calls

    async def test_setup_isolated_repo_async(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """setup_isolated_repo_async wraps sync version in asyncio.to_thread."""
        original_repo, task_repo = tmp_git_repos
        task_id = "async-task"

        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout="async123\n",
        )
        subprocess_mock.set_git_response(["init", "--quiet"])
        subprocess_mock.set_git_response(["remote", "add", "origin", str(original_repo)])
        subprocess_mock.set_git_response(["fetch", "origin", "--quiet"])
        subprocess_mock.set_git_response(
            ["checkout", "-b", f"task-{task_id}", "async123", "--quiet"]
        )
        subprocess_mock.set_git_response(
            ["config", "user.email", f"claude-task-{task_id}@microvm.local"]
        )
        subprocess_mock.set_git_response(
            ["config", "user.name", f"Claude Task ({task_id})"]
        )
        subprocess_mock.set_default(returncode=0)

        result = await setup_isolated_repo_async(original_repo, task_repo, task_id)

        assert result == "async123"


# =============================================================================
# merge_task_commits Tests
# =============================================================================


class TestMergeTaskCommits:
    """Tests for merge_task_commits function."""

    def test_merge_fast_forward(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """merge_task_commits uses fast-forward when HEAD hasn't moved."""
        original_repo, task_repo = tmp_git_repos
        task_id = "merge-ff-task"
        start_ref = "abc123start"
        task_ref = f"refs/tasks/{task_id}"

        # Fetch succeeds
        subprocess_mock.set_git_response(
            ["fetch", str(task_repo), f"task-{task_id}:{task_ref}"]
        )
        # 3 commits to merge
        subprocess_mock.set_git_response(
            ["rev-list", "--count", f"{start_ref}..{task_ref}"],
            stdout="3\n",
        )
        # Current HEAD matches start_ref
        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout=f"{start_ref}\n",
        )
        subprocess_mock.set_git_response(
            ["symbolic-ref", "--short", "HEAD"],
            stdout="main\n",
        )
        # Fast-forward succeeds
        subprocess_mock.set_git_response(["merge", "--ff-only", task_ref])
        # Cleanup ref
        subprocess_mock.set_git_response(["update-ref", "-d", task_ref])
        subprocess_mock.set_default(returncode=0)

        result = merge_task_commits(original_repo, task_repo, task_id, start_ref)

        assert result.merged is True
        assert result.method == "fast-forward"
        assert result.commits == 3
        assert result.conflicts == []

    def test_merge_no_new_commits(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """merge_task_commits returns early when no commits to merge."""
        original_repo, task_repo = tmp_git_repos
        task_id = "no-commits-task"
        start_ref = "abc123start"
        task_ref = f"refs/tasks/{task_id}"

        subprocess_mock.set_git_response(
            ["fetch", str(task_repo), f"task-{task_id}:{task_ref}"]
        )
        # Zero commits
        subprocess_mock.set_git_response(
            ["rev-list", "--count", f"{start_ref}..{task_ref}"],
            stdout="0\n",
        )
        subprocess_mock.set_default(returncode=0)

        result = merge_task_commits(original_repo, task_repo, task_id, start_ref)

        assert result.merged is True
        assert result.method == "none"
        assert result.commits == 0

    def test_merge_fetch_failure(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """merge_task_commits returns error when fetch fails."""
        original_repo, task_repo = tmp_git_repos
        task_id = "fetch-fail-task"
        start_ref = "abc123start"
        task_ref = f"refs/tasks/{task_id}"

        subprocess_mock.set_git_response(
            ["fetch", str(task_repo), f"task-{task_id}:{task_ref}"],
            returncode=128,
            stderr="fatal: Could not read from remote repository.\n",
        )

        result = merge_task_commits(original_repo, task_repo, task_id, start_ref)

        assert result.merged is False
        assert result.reason == "fetch_failed"
        assert result.task_ref == task_ref

    def test_merge_rebase_required(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """merge_task_commits uses rebase when HEAD has moved."""
        original_repo, task_repo = tmp_git_repos
        task_id = "rebase-task"
        start_ref = "abc123start"
        current_head = "def456moved"
        task_ref = f"refs/tasks/{task_id}"

        subprocess_mock.set_git_response(
            ["fetch", str(task_repo), f"task-{task_id}:{task_ref}"]
        )
        subprocess_mock.set_git_response(
            ["rev-list", "--count", f"{start_ref}..{task_ref}"],
            stdout="2\n",
        )
        # HEAD has moved (different from start_ref)
        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout=f"{current_head}\n",
        )
        subprocess_mock.set_git_response(
            ["symbolic-ref", "--short", "HEAD"],
            stdout="main\n",
        )
        # Create rebase branch
        subprocess_mock.set_git_response(
            ["checkout", "-b", f"rebase-{task_id}", task_ref, "--quiet"]
        )
        # Rebase succeeds
        subprocess_mock.set_git_response(["rebase", current_head])
        # Checkout back to main
        subprocess_mock.set_git_response(["checkout", "main", "--quiet"])
        # Fast-forward merge
        subprocess_mock.set_git_response(["merge", "--ff-only", f"rebase-{task_id}"])
        # Cleanup
        subprocess_mock.set_git_response(["branch", "-d", f"rebase-{task_id}"])
        subprocess_mock.set_git_response(["update-ref", "-d", task_ref])
        subprocess_mock.set_default(returncode=0)

        result = merge_task_commits(original_repo, task_repo, task_id, start_ref)

        assert result.merged is True
        assert result.method == "rebase"
        assert result.commits == 2

    def test_merge_conflict_detected(
        self, tmp_git_repos: tuple[Path, Path], subprocess_mock: SubprocessMock
    ):
        """merge_task_commits detects and reports conflicts."""
        original_repo, task_repo = tmp_git_repos
        task_id = "conflict-task"
        start_ref = "abc123start"
        current_head = "def456moved"
        task_ref = f"refs/tasks/{task_id}"

        subprocess_mock.set_git_response(
            ["fetch", str(task_repo), f"task-{task_id}:{task_ref}"]
        )
        subprocess_mock.set_git_response(
            ["rev-list", "--count", f"{start_ref}..{task_ref}"],
            stdout="1\n",
        )
        subprocess_mock.set_git_response(
            ["rev-parse", "HEAD"],
            stdout=f"{current_head}\n",
        )
        subprocess_mock.set_git_response(
            ["symbolic-ref", "--short", "HEAD"],
            stdout="main\n",
        )
        subprocess_mock.set_git_response(
            ["checkout", "-b", f"rebase-{task_id}", task_ref, "--quiet"]
        )
        # Rebase fails with conflicts
        subprocess_mock.set_git_response(
            ["rebase", current_head],
            returncode=1,
            stderr="CONFLICT (content): Merge conflict in src/main.py\n",
        )
        # List conflicting files
        subprocess_mock.set_git_response(
            ["diff", "--name-only", "--diff-filter=U"],
            stdout="src/main.py\ntests/test_main.py\n",
        )
        # Abort and cleanup
        subprocess_mock.set_git_response(["rebase", "--abort"])
        subprocess_mock.set_git_response(["checkout", "main", "--quiet"])
        subprocess_mock.set_git_response(["branch", "-D", f"rebase-{task_id}"])
        subprocess_mock.set_default(returncode=0)

        result = merge_task_commits(original_repo, task_repo, task_id, start_ref)

        assert result.merged is False
        assert result.reason == "conflicts"
        assert result.conflicts == ["src/main.py", "tests/test_main.py"]
        assert result.task_ref == task_ref
        assert result.commits == 1


# =============================================================================
# cleanup_task_ref Tests
# =============================================================================


class TestCleanupTaskRef:
    """Tests for cleanup_task_ref function."""

    def test_cleanup_task_ref_success(
        self, tmp_path: Path, subprocess_mock: SubprocessMock
    ):
        """cleanup_task_ref removes refs/tasks/<id> and returns True."""
        task_id = "cleanup-task"
        task_ref = f"refs/tasks/{task_id}"

        subprocess_mock.set_git_response(["update-ref", "-d", task_ref])

        result = cleanup_task_ref(tmp_path, task_id)

        assert result is True
        assert ["git", "update-ref", "-d", task_ref] in subprocess_mock.calls

    def test_cleanup_task_ref_not_found(
        self, tmp_path: Path, subprocess_mock: SubprocessMock
    ):
        """cleanup_task_ref returns False when ref doesn't exist."""
        task_id = "nonexistent-task"
        task_ref = f"refs/tasks/{task_id}"

        subprocess_mock.set_git_response(
            ["update-ref", "-d", task_ref],
            returncode=1,
            stderr="error: cannot lock ref\n",
        )

        result = cleanup_task_ref(tmp_path, task_id)

        assert result is False


# =============================================================================
# MergeResult Tests
# =============================================================================


class TestMergeResult:
    """Tests for MergeResult dataclass."""

    def test_merge_result_to_dict_success(self):
        """MergeResult.to_dict serializes successful merge."""
        result = MergeResult(
            merged=True,
            method="fast-forward",
            commits=5,
        )

        d = result.to_dict()

        assert d == {
            "merged": True,
            "method": "fast-forward",
            "commits": 5,
            "conflicts": [],
            "reason": None,
            "task_ref": None,
        }

    def test_merge_result_to_dict_conflict(self):
        """MergeResult.to_dict serializes conflict result."""
        result = MergeResult(
            merged=False,
            reason="conflicts",
            conflicts=["file1.py", "file2.py"],
            task_ref="refs/tasks/abc123",
            commits=2,
        )

        d = result.to_dict()

        assert d == {
            "merged": False,
            "method": None,
            "commits": 2,
            "conflicts": ["file1.py", "file2.py"],
            "reason": "conflicts",
            "task_ref": "refs/tasks/abc123",
        }

    def test_merge_result_defaults(self):
        """MergeResult initializes with proper defaults."""
        result = MergeResult(merged=True)

        assert result.merged is True
        assert result.method is None
        assert result.commits == 0
        assert result.conflicts == []
        assert result.reason is None
        assert result.task_ref is None

    def test_merge_result_conflicts_not_shared(self):
        """MergeResult conflicts list is not shared between instances."""
        result1 = MergeResult(merged=False)
        result2 = MergeResult(merged=False)

        result1.conflicts.append("file.py")

        assert result1.conflicts == ["file.py"]
        assert result2.conflicts == []
