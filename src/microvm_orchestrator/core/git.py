"""Git operations for isolated repositories and merging."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MergeResult:
    """Result of merging task commits back to original repo."""

    merged: bool
    method: Optional[str] = None  # "fast-forward", "rebase", "none"
    commits: int = 0
    conflicts: list[str] = None
    reason: Optional[str] = None  # "conflicts", "fetch_failed"
    task_ref: Optional[str] = None

    def __post_init__(self):
        if self.conflicts is None:
            self.conflicts = []

    def to_dict(self) -> dict:
        return {
            "merged": self.merged,
            "method": self.method,
            "commits": self.commits,
            "conflicts": self.conflicts,
            "reason": self.reason,
            "task_ref": self.task_ref,
        }


def run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def get_current_ref(repo_path: Path) -> str:
    """Get current HEAD commit."""
    result = run_git(["rev-parse", "HEAD"], repo_path)
    return result.stdout.strip()


def get_current_branch(repo_path: Path) -> Optional[str]:
    """Get current branch name, or None if detached."""
    result = run_git(["symbolic-ref", "--short", "HEAD"], repo_path, check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def setup_isolated_repo(
    original_repo: Path,
    task_repo: Path,
    task_id: str,
) -> str:
    """
    Create an isolated git repository for a task.

    Returns the starting ref (commit hash).
    """
    task_repo.mkdir(parents=True, exist_ok=True)

    # Get current ref from original repo
    current_ref = get_current_ref(original_repo)

    # Initialize new repository
    run_git(["init", "--quiet"], task_repo)

    # Add original repo as remote and fetch
    run_git(["remote", "add", "origin", str(original_repo)], task_repo)

    try:
        run_git(["fetch", "origin", "--quiet"], task_repo)
        # Create task branch from current ref
        run_git(["checkout", "-b", f"task-{task_id}", current_ref, "--quiet"], task_repo)
    except subprocess.CalledProcessError:
        # Fallback: copy working tree via archive
        archive_result = subprocess.run(
            ["git", "archive", "HEAD"],
            cwd=original_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["tar", "-x"],
            cwd=task_repo,
            input=archive_result.stdout,
            check=True,
        )
        run_git(["add", "-A"], task_repo)
        run_git(["commit", "-m", f"Initial copy from {current_ref}", "--quiet"], task_repo)
        run_git(["checkout", "-b", f"task-{task_id}", "--quiet"], task_repo)

    # Configure git identity for this task
    run_git(["config", "user.email", f"claude-task-{task_id}@microvm.local"], task_repo)
    run_git(["config", "user.name", f"Claude Task ({task_id})"], task_repo)

    return current_ref


async def setup_isolated_repo_async(
    original_repo: Path,
    task_repo: Path,
    task_id: str,
) -> str:
    """
    Async version of setup_isolated_repo.

    Wraps blocking git operations in asyncio.to_thread() to avoid
    blocking the event loop.
    """
    return await asyncio.to_thread(
        setup_isolated_repo, original_repo, task_repo, task_id
    )


def merge_task_commits(
    original_repo: Path,
    task_repo: Path,
    task_id: str,
    start_ref: str,
) -> MergeResult:
    """
    Merge commits from task repository back to original.

    Attempts fast-forward first, then rebase. Returns MergeResult with
    conflict information if merge fails.
    """
    task_ref = f"refs/tasks/{task_id}"

    # Fetch task commits into original repo
    try:
        run_git(
            ["fetch", str(task_repo), f"task-{task_id}:{task_ref}"],
            original_repo,
        )
    except subprocess.CalledProcessError:
        return MergeResult(merged=False, reason="fetch_failed", task_ref=task_ref)

    # Count commits to merge
    count_result = run_git(
        ["rev-list", "--count", f"{start_ref}..{task_ref}"],
        original_repo,
        check=False,
    )
    commit_count = int(count_result.stdout.strip()) if count_result.returncode == 0 else 0

    if commit_count == 0:
        return MergeResult(merged=True, method="none", commits=0)

    # Get current HEAD
    current_head = get_current_ref(original_repo)
    current_branch = get_current_branch(original_repo)

    # Try fast-forward if HEAD hasn't moved
    if current_head == start_ref:
        ff_result = run_git(
            ["merge", "--ff-only", task_ref],
            original_repo,
            check=False,
        )
        if ff_result.returncode == 0:
            # Clean up task ref after successful merge
            run_git(["update-ref", "-d", task_ref], original_repo, check=False)
            return MergeResult(merged=True, method="fast-forward", commits=commit_count)

    # Try rebase
    rebase_branch = f"rebase-{task_id}"
    run_git(["checkout", "-b", rebase_branch, task_ref, "--quiet"], original_repo, check=False)

    rebase_result = run_git(["rebase", current_head], original_repo, check=False)

    if rebase_result.returncode == 0:
        # Rebase succeeded - merge back
        if current_branch:
            run_git(["checkout", current_branch, "--quiet"], original_repo)
        else:
            run_git(["checkout", current_head, "--quiet"], original_repo)

        run_git(["merge", "--ff-only", rebase_branch], original_repo)
        run_git(["branch", "-d", rebase_branch], original_repo, check=False)
        # Clean up task ref after successful merge
        run_git(["update-ref", "-d", task_ref], original_repo, check=False)
        return MergeResult(merged=True, method="rebase", commits=commit_count)

    # Rebase failed - identify conflicts
    diff_result = run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        original_repo,
        check=False,
    )
    conflicts = [f for f in diff_result.stdout.strip().split("\n") if f]

    # Abort rebase and cleanup
    run_git(["rebase", "--abort"], original_repo, check=False)
    if current_branch:
        run_git(["checkout", current_branch, "--quiet"], original_repo, check=False)
    else:
        run_git(["checkout", current_head, "--quiet"], original_repo, check=False)
    run_git(["branch", "-D", rebase_branch], original_repo, check=False)

    return MergeResult(
        merged=False,
        reason="conflicts",
        conflicts=conflicts,
        task_ref=task_ref,
        commits=commit_count,
    )


def cleanup_task_ref(original_repo: Path, task_id: str) -> bool:
    """Delete refs/tasks/<task_id> if it exists."""
    task_ref = f"refs/tasks/{task_id}"
    result = run_git(["update-ref", "-d", task_ref], original_repo, check=False)
    return result.returncode == 0
