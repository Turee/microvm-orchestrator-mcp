"""VM execution with pty for headless operation."""

from __future__ import annotations

import asyncio
import os
import pty
import select
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .task import Task


@dataclass
class VMConfig:
    """Configuration for VM execution."""

    nix_dir: Path
    package_name: str = "claude-microvm"
    env: dict[str, str] = None

    def __post_init__(self):
        if self.env is None:
            self.env = {}


def build_vm(
    nix_dir: Path,
    package_name: str = "claude-microvm",
    env: dict[str, str] = None,
    slot: int = 1,
) -> Path:
    """
    Build the microVM using nix-build and return the result path.

    Uses --argstr to pass configuration, avoiding flake locks and --impure.
    Each slot gets its own result symlink to enable parallel builds.
    """
    env = env or {}

    # Build nix-build command with --argstr for each configuration option
    cmd = [
        "nix-build",
        "default.nix",
        "-A", package_name,
        "-o", f"result-mcp-{slot}",
    ]

    # Map environment variables to nix-build --argstr arguments
    arg_mapping = {
        "DELEGATE_TASK_DIR": "taskDir",
        "DELEGATE_VAR_DIR": "varDir",
        "MICROVM_CONTAINER_DIR": "containerDir",
        "MICROVM_NIX_STORE_IMAGE": "nixStoreImage",
        "DELEGATE_SOCKET": "socketPath",
        "MICROVM_SLOT": "slot",
        "MICROVM_CONFIG_FILE": "configFile",
    }

    for env_var, arg_name in arg_mapping.items():
        if env_var in env and env[env_var]:
            cmd.extend(["--argstr", arg_name, env[env_var]])

    result = subprocess.run(
        cmd,
        cwd=nix_dir,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"nix-build failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")

    # Get the store path from output
    store_path = result.stdout.strip().split("\n")[-1]
    if store_path and Path(store_path).exists():
        return Path(store_path)

    # Fall back to result symlink
    return nix_dir / f"result-mcp-{slot}"


async def build_vm_async(
    nix_dir: Path,
    package_name: str = "claude-microvm",
    env: dict[str, str] = None,
    slot: int = 1,
) -> Path:
    """
    Async version of build_vm.

    Wraps blocking nix-build subprocess in asyncio.to_thread() to avoid
    blocking the event loop. This is critical as nix-build can take 10-60s.
    """
    return await asyncio.to_thread(build_vm, nix_dir, package_name, env, slot)


def find_runner(build_path: Path) -> Path:
    """Find the microvm-run script in the build output."""
    runner = build_path / "bin" / "microvm-run"
    if not runner.exists():
        raise FileNotFoundError(f"Runner not found at: {runner}")
    return runner


def patch_runner_for_logfile(runner_path: Path, log_path: Path) -> str:
    """
    Read runner script and patch it to use logFilePath instead of stdio.

    Returns the patched script content.
    """
    content = runner_path.read_text()
    patched = content.replace(
        "virtio-serial,stdio",
        f"virtio-serial,logFilePath={log_path}",
    )
    return patched


class VMProcess:
    """
    Manages a running microVM process.

    Uses pty to handle the VM's serial console without requiring a TTY.
    """

    def __init__(
        self,
        task: Task,
        config: VMConfig,
        on_exit: Optional[Callable[[int], None]] = None,
    ):
        self.task = task
        self.config = config
        self.on_exit = on_exit
        self._process: Optional[subprocess.Popen] = None
        self._master_fd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> int:
        """
        Start the VM process.

        Returns the process PID.
        """
        # Build the VM with configuration passed via --argstr
        slot = int(self.config.env.get("MICROVM_SLOT", "1"))
        build_path = build_vm(self.config.nix_dir, self.config.package_name, self.config.env, slot)
        runner_path = find_runner(build_path)

        # Patch runner for log file output
        patched_script = patch_runner_for_logfile(runner_path, self.task.log_path)

        # Ensure log file exists
        self.task.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.task.log_path.touch()

        # Create pty for process
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd

        # Prepare environment
        env = os.environ.copy()
        env.update(self.config.env)

        # Start process with patched script via bash
        self._process = subprocess.Popen(
            ["bash", "-c", patched_script],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.config.nix_dir,
            env=env,
            start_new_session=True,
        )

        # Close slave fd in parent
        os.close(slave_fd)

        # Start monitoring thread
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

        return self._process.pid

    async def start_async(self) -> int:
        """
        Async version of start().

        Wraps blocking operations (nix-build, Popen) in asyncio.to_thread()
        to avoid blocking the event loop. nix-build can take 10-60 seconds.

        Returns the process PID.
        """
        # Build the VM with configuration passed via --argstr (async)
        slot = int(self.config.env.get("MICROVM_SLOT", "1"))
        build_path = await build_vm_async(
            self.config.nix_dir, self.config.package_name, self.config.env, slot
        )
        runner_path = find_runner(build_path)

        # Patch runner for log file output
        patched_script = patch_runner_for_logfile(runner_path, self.task.log_path)

        # Ensure log file exists
        self.task.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.task.log_path.touch()

        # Create pty for process
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd

        # Prepare environment
        env = os.environ.copy()
        env.update(self.config.env)

        # Start process with patched script via bash (in thread to avoid blocking)
        def start_process():
            return subprocess.Popen(
                ["bash", "-c", patched_script],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.config.nix_dir,
                env=env,
                start_new_session=True,
            )

        self._process = await asyncio.to_thread(start_process)

        # Close slave fd in parent
        os.close(slave_fd)

        # Start monitoring thread
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

        return self._process.pid

    def _monitor(self) -> None:
        """Monitor process and write pty output to log file."""
        log_file = open(self.task.log_path, "ab")

        try:
            while not self._stop_event.is_set():
                # Check if process has exited
                if self._process.poll() is not None:
                    break

                # Wait for output with timeout
                if self._master_fd is not None:
                    ready, _, _ = select.select([self._master_fd], [], [], 0.5)
                    if ready:
                        try:
                            data = os.read(self._master_fd, 4096)
                            if data:
                                log_file.write(data)
                                log_file.flush()
                        except OSError:
                            # pty closed
                            break
                else:
                    break

        finally:
            log_file.close()
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None

            # Call exit handler
            if self.on_exit and self._process:
                self.on_exit(self._process.returncode or 0)

    def stop(self) -> None:
        """Stop the VM process."""
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

    @property
    def is_running(self) -> bool:
        """Check if process is still running."""
        return self._process is not None and self._process.poll() is None

    @property
    def exit_code(self) -> Optional[int]:
        """Get exit code if process has finished."""
        if self._process:
            return self._process.poll()
        return None


def get_slot_dir(slot: int) -> Path:
    """Get the directory for a slot's persistent storage."""
    return Path.home() / ".microvm-orchestrator" / "slots" / str(slot)


def ensure_slot_initialized(slot: int) -> Path:
    """Ensure slot directory exists with required subdirectories."""
    slot_dir = get_slot_dir(slot)
    (slot_dir / "var").mkdir(parents=True, exist_ok=True)
    (slot_dir / "container-storage").mkdir(parents=True, exist_ok=True)
    return slot_dir


def prepare_vm_env(task: Task, api_key: str, start_ref: str, config_file: Optional[Path] = None) -> dict[str, str]:
    """Prepare environment variables for VM execution."""
    slot_dir = ensure_slot_initialized(task.slot)

    env = {
        "DELEGATE_GIT_DIR": str(task.repo_path / ".git"),
        "DELEGATE_GIT_ROOT": str(task.repo_path),
        "DELEGATE_TASK_DIR": str(task.task_dir),
        "DELEGATE_ORIGINAL_REPO": str(task.project_root),
        "DELEGATE_VAR_DIR": str(slot_dir / "var"),
        "DELEGATE_SOCKET": str(task.task_dir / "socket"),
        "MICROVM_SLOT": str(task.slot),
        "MICROVM_CONTAINER_DIR": str(slot_dir / "container-storage"),
        "MICROVM_NIX_STORE_IMAGE": str(slot_dir / "nix-store.img"),
        "MICROVM_PACKAGE": "claude-microvm",
    }

    # Add config file path if provided
    if config_file is not None:
        env["MICROVM_CONFIG_FILE"] = str(config_file.absolute())

    return env


def write_task_files(task: Task, api_key: str, start_ref: str) -> None:
    """Write task description and API key files."""
    task.task_dir.mkdir(parents=True, exist_ok=True)

    # Write task description
    (task.task_dir / "task.md").write_text(task.description)

    # Write starting ref
    (task.task_dir / "start-ref").write_text(start_ref)

    # Write task ID
    (task.task_dir / "task-id").write_text(task.id)

    # Write API key (will be deleted by VM after reading)
    api_key_file = task.api_key_path
    api_key_file.write_text(api_key)
    api_key_file.chmod(0o600)
