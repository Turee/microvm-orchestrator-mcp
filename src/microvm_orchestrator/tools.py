"""MCP Tool implementations."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from .core.task import Task, TaskStatus
from .core.events import EventQueue, EventType
from .core.git import setup_isolated_repo, setup_isolated_repo_async, merge_task_commits, cleanup_task_ref
from .core.vm import VMConfig, VMProcess, prepare_vm_env, write_task_files


class ToolError(Exception):
    """Error from tool execution."""
    pass


class Orchestrator:
    """
    Main orchestrator managing tasks and VM processes.

    Maintains state across tool calls.
    """

    def __init__(self, repo_path: Optional[Path] = None):
        self.repo_path = repo_path or self._detect_repo_path()
        self.event_queue = EventQueue()
        self._processes: dict[str, VMProcess] = {}
        self._tasks: dict[str, Task] = {}

    def _detect_repo_path(self) -> Path:
        """Detect git root from current directory."""
        cwd = Path.cwd()
        while cwd != cwd.parent:
            if (cwd / ".git").exists():
                return cwd
            cwd = cwd.parent
        raise ToolError("Not in a git repository")

    def _get_plugin_dir(self) -> Path:
        """Get the directory containing nix build files."""
        import importlib.resources

        # When installed as a package, nix files are in package data
        try:
            pkg_nix_dir = importlib.resources.files("microvm_orchestrator") / "nix"
            if pkg_nix_dir.joinpath("default.nix").is_file():
                return Path(str(pkg_nix_dir))
        except (TypeError, FileNotFoundError):
            pass

        # Fallback for development: source layout
        plugin_dir = Path(__file__).parent.parent.parent
        if (plugin_dir / "default.nix").exists():
            return plugin_dir

        raise ToolError(
            "Plugin nix files not found. "
            "Ensure the package is installed correctly."
        )

    def _get_api_key(self) -> str:
        """Get API key from environment or keychain."""
        # Check environment variables
        if api_key := os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return api_key
        if api_key := os.environ.get("ANTHROPIC_API_KEY"):
            return api_key

        # Try macOS keychain
        try:
            import subprocess
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-a", os.environ.get("USER", ""), "-w"],
                capture_output=True,
                text=True,
                check=True,
            )
            keychain_data = result.stdout.strip()

            # Check if it's JSON
            if keychain_data.startswith("{"):
                data = json.loads(keychain_data)
                if token := data.get("claudeAiOauth", {}).get("accessToken"):
                    return token
            return keychain_data
        except Exception:
            pass

        raise ToolError(
            "No API key found. Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, "
            "or login with 'claude /login'"
        )

    def _on_task_exit(self, task: Task, exit_code: int) -> None:
        """Handle task VM exit."""
        # Read results
        result = task.get_result()
        merge_result = None

        # Attempt to merge commits if task succeeded
        if exit_code == 0 and result and result.get("success"):
            start_ref_file = task.task_dir / "start-ref"
            if start_ref_file.exists():
                start_ref = start_ref_file.read_text().strip()
                merge_result_obj = merge_task_commits(
                    original_repo=task.repo_path,
                    task_repo=task.isolated_repo_path,
                    task_id=task.id,
                    start_ref=start_ref,
                )
                merge_result = merge_result_obj.to_dict()

                # Write merge result
                task.merge_result_path.write_text(json.dumps(merge_result, indent=2))

        # Update task status
        task.mark_completed(exit_code)

        # Emit event
        event = self.event_queue.create_completed_event(
            task_id=task.id,
            exit_code=exit_code,
            result=result,
            merge_result=merge_result,
        )
        self.event_queue.emit(event)

        # Remove from active processes
        self._processes.pop(task.id, None)

    # Tool: run_task
    async def run_task(self, description: str, slot: int = 1) -> dict[str, Any]:
        """
        Start a new task in a microVM.

        This is an async operation. The blocking git and nix-build operations
        are run in thread pools to avoid blocking the event loop.

        Args:
            description: Task description/instructions for Claude.
                If the task involves running Docker containers, include
                instructions to use --network=host (required for networking
                to work correctly inside the microVM).
            slot: Slot number for persistent storage (default 1)

        Returns:
            {"task_id": str}
        """
        # Validate
        plugin_dir = self._get_plugin_dir()
        api_key = self._get_api_key()

        # Create task
        task = Task.create(
            description=description,
            slot=slot,
            repo_path=self.repo_path,
        )
        task.save()
        self._tasks[task.id] = task

        try:
            # Setup isolated git repo (async to avoid blocking on git operations)
            start_ref = await setup_isolated_repo_async(
                original_repo=self.repo_path,
                task_repo=task.isolated_repo_path,
                task_id=task.id,
            )

            # Write task files
            write_task_files(task, api_key, start_ref)

            # Prepare VM environment
            vm_env = prepare_vm_env(task, api_key, start_ref)

            # Create VM config (uses plugin's default.nix)
            config = VMConfig(
                nix_dir=plugin_dir,
                package_name="claude-microvm",
                env=vm_env,
            )

            # Start VM
            def on_exit(exit_code: int):
                self._on_task_exit(task, exit_code)

            process = VMProcess(task, config, on_exit=on_exit)
            # Register BEFORE start to prevent race with fast VM exit
            self._processes[task.id] = process
            # Use async start to avoid blocking on nix-build (10-60s)
            pid = await process.start_async()
            task.mark_running(pid)

            return {"task_id": task.id}

        except Exception as e:
            task.mark_failed(str(e))
            event = self.event_queue.create_failed_event(task.id, str(e))
            self.event_queue.emit(event)
            raise ToolError(f"Failed to start task: {e}")

    # Tool: get_task_info
    def get_task_info(self, task_id: str) -> dict[str, Any]:
        """
        Get information about a task.

        Args:
            task_id: Task ID

        Returns:
            Task information including status, result, etc.
        """
        task = self._get_task(task_id)

        # Derive status from actual process state
        if task_id in self._processes:
            status = "running"
        else:
            # No process - task is done, check result to determine success/failure
            result = task.get_result()
            if result and result.get("success"):
                status = "completed"
            else:
                status = "failed"

        info = {
            "task_id": task.id,
            "description": task.description,
            "status": status,
            "slot": task.slot,
            "repo_path": str(task.repo_path),
            "isolated_repo_path": str(task.isolated_repo_path),
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "pid": task.pid,
            "exit_code": task.exit_code,
            "error": task.error,
        }

        # Include results if available
        if result := task.get_result():
            info["result"] = result

        if merge_result := task.get_merge_result():
            info["merge_result"] = merge_result

        return info

    # Tool: get_task_logs
    def get_task_logs(self, task_id: str) -> dict[str, Any]:
        """
        Get path to task's serial console log.

        Args:
            task_id: Task ID

        Returns:
            {"log_path": str}
        """
        task = self._get_task(task_id)

        if not task.log_path.exists():
            raise ToolError(f"Log file not found: {task.log_path}")

        return {"log_path": str(task.log_path)}

    # Tool: wait_next_event
    async def wait_next_event(
        self,
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """
        Wait for the next task completion event.

        This is an async operation that can be cancelled by asyncio.

        Args:
            timeout_ms: Timeout in milliseconds (default 30000)

        Returns:
            Event information, {"no_running_tasks": true}, or {"timeout": true}
        """
        # Return immediately if no tasks are running
        if not self._processes:
            return {"no_running_tasks": True}

        event = await self.event_queue.wait_async(timeout_ms)

        if event is None:
            return {"timeout": True}

        return event.to_dict()

    # Tool: cleanup_task
    async def cleanup_task(
        self,
        task_id: str,
        delete_ref: bool = False,
    ) -> dict[str, Any]:
        """
        Clean up task directory and optionally delete git ref.

        This is an async operation. Blocking file system operations are run
        in thread pools to avoid blocking the event loop.

        Args:
            task_id: Task ID
            delete_ref: Whether to delete refs/tasks/<task_id>

        Returns:
            {"success": bool}
        """
        task = self._get_task(task_id)

        # Stop process if running
        if process := self._processes.get(task_id):
            process.stop()
            self._processes.pop(task_id, None)

        # Delete task directory (async to avoid blocking on large directories)
        if task.task_dir.exists():
            await asyncio.to_thread(shutil.rmtree, task.task_dir)

        # Delete git ref if requested (async for git subprocess)
        if delete_ref:
            await asyncio.to_thread(cleanup_task_ref, task.repo_path, task_id)

        # Remove from tasks dict
        self._tasks.pop(task_id, None)

        return {"success": True}

    def _get_task(self, task_id: str) -> Task:
        """Get task by ID, loading from disk if needed."""
        if task_id in self._tasks:
            return self._tasks[task_id]

        # Try to load from disk
        task_dir = self.repo_path / ".microvm" / "tasks" / task_id
        if not task_dir.exists():
            raise ToolError(f"Task not found: {task_id}")

        task = Task.load(task_dir)
        self._tasks[task_id] = task
        return task

    def list_tasks(self) -> list[dict[str, Any]]:
        """List all tasks (for debugging)."""
        tasks_dir = self.repo_path / ".microvm" / "tasks"
        if not tasks_dir.exists():
            return []

        tasks = []
        for task_dir in tasks_dir.iterdir():
            if task_dir.is_dir() and (task_dir / "task.json").exists():
                try:
                    task = Task.load(task_dir)
                    tasks.append({
                        "task_id": task.id,
                        "status": task.status.value,
                        "description": task.description[:50] + "..." if len(task.description) > 50 else task.description,
                    })
                except Exception:
                    pass

        return tasks
