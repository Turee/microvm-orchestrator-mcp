"""Tests for VM process management (core/vm.py)."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from microvm_orchestrator.core.vm import (
    VMConfig,
    VMProcess,
    build_vm,
    build_vm_async,
    find_runner,
    patch_runner_for_logfile,
    get_slot_dir,
    get_nix_cache_dir,
    ensure_slot_initialized,
    prepare_vm_env,
    write_task_files,
)
from microvm_orchestrator.core.task import Task, TaskStatus


# =============================================================================
# build_vm Tests
# =============================================================================

class TestBuildVM:
    """Tests for build_vm function."""

    def test_build_vm_success(self, tmp_path: Path):
        """nix-build returns path on success."""
        nix_dir = tmp_path / "nix"
        nix_dir.mkdir()
        (nix_dir / "default.nix").write_text("{}")

        store_path = "/nix/store/abc123-microvm"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=f"{store_path}\n",
                stderr="",
            )

            result = build_vm(
                nix_dir=nix_dir,
                package_name="claude-microvm",
                env={
                    "DELEGATE_TASK_DIR": "/tmp/task",
                    "MICROVM_NIX_STORE_IMAGE": "/tmp/nix-store.img",
                    "DELEGATE_SOCKET": "/tmp/socket",
                    "MICROVM_SLOT": "1",
                },
                slot=1,
            )

            # Verify nix-build was called with correct arguments
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]

            assert cmd[0] == "nix-build"
            # Path is now absolute
            assert str(nix_dir / "default.nix") in cmd
            assert "-A" in cmd
            assert "claude-microvm" in cmd
            assert "--argstr" in cmd
            assert "taskDir" in cmd
            assert "/tmp/task" in cmd
            assert "nixStoreImage" in cmd
            assert "/tmp/nix-store.img" in cmd
            # Result symlink is in writable cache directory
            cmd_str = " ".join(cmd)
            assert ".microvm-orchestrator/nix-cache/result-mcp-1" in cmd_str

    def test_build_vm_failure(self, tmp_path: Path):
        """nix-build failure raises RuntimeError."""
        nix_dir = tmp_path / "nix"
        nix_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error: attribute 'foo' missing",
            )

            with pytest.raises(RuntimeError, match="nix-build failed"):
                build_vm(nix_dir=nix_dir, env={}, slot=1)

    def test_build_vm_returns_result_symlink(self, tmp_path: Path):
        """Falls back to result symlink in cache when stdout path doesn't exist."""
        nix_dir = tmp_path / "nix"
        nix_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="/nix/store/nonexistent-path\n",
                stderr="",
            )

            result = build_vm(nix_dir=nix_dir, env={}, slot=1)

            # Result symlink is now in the cache directory
            expected_link = get_nix_cache_dir() / "result-mcp-1"
            assert result == expected_link


@pytest.mark.asyncio
class TestBuildVMAsync:
    """Tests for async build_vm."""

    async def test_build_vm_async_wraps_sync(self, tmp_path: Path):
        """build_vm_async wraps build_vm in thread."""
        nix_dir = tmp_path / "nix"
        nix_dir.mkdir()

        store_path = "/nix/store/abc123-microvm"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=f"{store_path}\n",
                stderr="",
            )
            with patch("pathlib.Path.exists", return_value=True):
                result = await build_vm_async(nix_dir=nix_dir, env={}, slot=1)

                assert str(result) == store_path


# =============================================================================
# find_runner Tests
# =============================================================================

class TestFindRunner:
    """Tests for find_runner function."""

    def test_find_runner_success(self, tmp_path: Path):
        """Locates microvm-run script in build output."""
        build_path = tmp_path / "build"
        runner_path = build_path / "bin" / "microvm-run"
        runner_path.parent.mkdir(parents=True)
        runner_path.write_text("#!/bin/bash\necho 'VM runner'")

        result = find_runner(build_path)

        assert result == runner_path

    def test_find_runner_not_found(self, tmp_path: Path):
        """Raises FileNotFoundError when runner missing."""
        build_path = tmp_path / "build"
        build_path.mkdir()

        with pytest.raises(FileNotFoundError, match="Runner not found"):
            find_runner(build_path)


# =============================================================================
# patch_runner_for_logfile Tests
# =============================================================================

class TestPatchRunnerForLogfile:
    """Tests for patch_runner_for_logfile function."""

    def test_patches_virtio_serial(self, tmp_path: Path):
        """Replaces stdio with logFilePath."""
        runner_path = tmp_path / "runner.sh"
        runner_path.write_text(
            "#!/bin/bash\n"
            "vfkit --device virtio-serial,stdio\n"
        )
        log_path = tmp_path / "serial.log"

        result = patch_runner_for_logfile(runner_path, log_path)

        assert "virtio-serial,stdio" not in result
        assert f"virtio-serial,logFilePath={log_path}" in result


# =============================================================================
# write_task_files Tests
# =============================================================================

class TestWriteTaskFiles:
    """Tests for write_task_files function."""

    def test_write_task_files_creates_all(self, tmp_path: Path, sample_task: Task):
        """Creates task.md, start-ref, task-id, and api_key files."""
        sample_task._task_dir = tmp_path / "task"

        write_task_files(sample_task, api_key="test-key", start_ref="abc123")

        assert (sample_task.task_dir / "task.md").read_text() == sample_task.description
        assert (sample_task.task_dir / "start-ref").read_text() == "abc123"
        assert (sample_task.task_dir / "task-id").read_text() == sample_task.id
        assert sample_task.api_key_path.read_text() == "test-key"

    def test_api_key_file_permissions(self, tmp_path: Path, sample_task: Task):
        """API key file has 0o600 permissions."""
        sample_task._task_dir = tmp_path / "task"

        write_task_files(sample_task, api_key="secret-key", start_ref="abc123")

        mode = sample_task.api_key_path.stat().st_mode
        # Check only user has read/write
        assert stat.S_IMODE(mode) == 0o600


# =============================================================================
# VMProcess Tests
# =============================================================================

class TestVMProcess:
    """Tests for VMProcess class."""

    @pytest.fixture
    def vm_config(self, tmp_path: Path) -> VMConfig:
        """Create a VMConfig for testing."""
        nix_dir = tmp_path / "nix"
        nix_dir.mkdir()
        (nix_dir / "default.nix").write_text("{}")

        return VMConfig(
            nix_dir=nix_dir,
            package_name="claude-microvm",
            env={
                "DELEGATE_TASK_DIR": str(tmp_path / "task"),
                "MICROVM_NIX_STORE_IMAGE": str(tmp_path / "nix-store.img"),
                "DELEGATE_SOCKET": str(tmp_path / "socket"),
                "MICROVM_SLOT": "1",
            },
        )

    @pytest.fixture
    def vm_task(self, tmp_path: Path, sample_task: Task) -> Task:
        """Create a task for VM testing."""
        task_dir = tmp_path / "tasks" / sample_task.id
        task_dir.mkdir(parents=True)
        sample_task._task_dir = task_dir
        return sample_task

    def test_vmprocess_start(
        self,
        vm_config: VMConfig,
        vm_task: Task,
        popen_mock,
        pty_mock,
        tmp_path: Path,
    ):
        """Spawns process with PTY."""
        # Create mock runner
        build_path = tmp_path / "build"
        runner_path = build_path / "bin" / "microvm-run"
        runner_path.parent.mkdir(parents=True)
        runner_path.write_text("#!/bin/bash\necho 'run'")

        process = VMProcess(vm_task, vm_config)

        with patch("microvm_orchestrator.core.vm.build_vm", return_value=build_path), \
             patch("subprocess.Popen", return_value=popen_mock) as mock_popen, \
             patch("pty.openpty", return_value=(pty_mock.master_fd, pty_mock.slave_fd)), \
             patch("os.close"):

            pid = process.start()

            assert pid == popen_mock.pid
            mock_popen.assert_called_once()
            # Verify bash -c is used to run patched script
            call_args = mock_popen.call_args
            cmd = call_args[0][0]
            assert cmd[0] == "bash"
            assert cmd[1] == "-c"
            assert isinstance(cmd[2], str)  # The patched script content

    def test_vmprocess_stop(self, vm_config: VMConfig, vm_task: Task, popen_mock):
        """Terminates process cleanly."""
        process = VMProcess(vm_task, vm_config)
        process._process = popen_mock
        process._stop_event = threading.Event()

        process.stop()

        assert popen_mock._terminated
        assert process._stop_event.is_set()

    def test_vmprocess_stop_force_kill(self, vm_config: VMConfig, vm_task: Task):
        """Force kills process if terminate times out."""
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Still running
        # First wait (with timeout) raises, second wait (after kill) succeeds
        mock_process.wait.side_effect = [
            subprocess.TimeoutExpired("cmd", 10),
            0,  # Return code after kill
        ]

        process = VMProcess(vm_task, vm_config)
        process._process = mock_process
        process._stop_event = threading.Event()

        process.stop()

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    def test_vmprocess_on_exit_callback(
        self,
        vm_config: VMConfig,
        vm_task: Task,
        popen_mock,
        pty_mock,
        tmp_path: Path,
    ):
        """Fires callback with exit code on process exit."""
        exit_codes = []

        def on_exit(code: int):
            exit_codes.append(code)

        build_path = tmp_path / "build"
        runner_path = build_path / "bin" / "microvm-run"
        runner_path.parent.mkdir(parents=True)
        runner_path.write_text("#!/bin/bash")

        # Configure popen to exit immediately
        popen_mock._returncode = 0
        popen_mock._terminated = True

        process = VMProcess(vm_task, vm_config, on_exit=on_exit)

        with patch("microvm_orchestrator.core.vm.build_vm", return_value=build_path), \
             patch("subprocess.Popen", return_value=popen_mock), \
             patch("pty.openpty", return_value=(pty_mock.master_fd, pty_mock.slave_fd)), \
             patch("os.close"), \
             patch("select.select", return_value=([], [], [])), \
             patch("os.read", side_effect=OSError("closed")):

            process.start()
            # Wait for monitor thread to detect exit
            time.sleep(0.2)

        assert 0 in exit_codes

    def test_vmprocess_is_running(self, vm_config: VMConfig, vm_task: Task, popen_mock):
        """is_running returns True while process active."""
        process = VMProcess(vm_task, vm_config)
        process._process = popen_mock

        # Process running
        assert process.is_running is True

        # Process terminated
        popen_mock._terminated = True
        assert process.is_running is False

    def test_vmprocess_exit_code(self, vm_config: VMConfig, vm_task: Task, popen_mock):
        """exit_code returns code after process finishes."""
        process = VMProcess(vm_task, vm_config)
        process._process = popen_mock

        # Still running
        assert process.exit_code is None

        # Finished
        popen_mock._returncode = 42
        popen_mock._terminated = True
        assert process.exit_code == 42


# =============================================================================
# prepare_vm_env Tests
# =============================================================================

class TestPrepareVMEnv:
    """Tests for prepare_vm_env function."""

    def test_prepare_vm_env_correct_vars(self, sample_task: Task, tmp_path: Path):
        """Returns correct environment variables."""
        # Override home directory for slot path
        with patch("microvm_orchestrator.core.vm.get_slot_dir") as mock_slot:
            slot_dir = tmp_path / "slots" / "1"
            slot_dir.mkdir(parents=True)
            (slot_dir / "var").mkdir()
            (slot_dir / "container-storage").mkdir()
            mock_slot.return_value = slot_dir

            with patch("microvm_orchestrator.core.vm.ensure_slot_initialized", return_value=slot_dir):
                env = prepare_vm_env(
                    task=sample_task,
                    api_key="test-key",
                    start_ref="abc123",
                )

        assert env["DELEGATE_TASK_DIR"] == str(sample_task.task_dir)
        assert env["DELEGATE_GIT_ROOT"] == str(sample_task.repo_path)
        assert env["DELEGATE_VAR_DIR"] == str(slot_dir / "var")
        assert env["MICROVM_SLOT"] == str(sample_task.slot)
        assert env["MICROVM_CONTAINER_DIR"] == str(slot_dir / "container-storage")
        assert env["MICROVM_NIX_STORE_IMAGE"] == str(slot_dir / "nix-store.img")
        assert env["MICROVM_PACKAGE"] == "claude-microvm"


# =============================================================================
# Slot Directory Tests
# =============================================================================

class TestSlotDirectory:
    """Tests for slot directory functions."""

    def test_get_slot_dir(self):
        """Returns correct slot directory path."""
        with patch("pathlib.Path.home", return_value=Path("/home/user")):
            result = get_slot_dir(1)

            assert result == Path("/home/user/.microvm-orchestrator/slots/1")

    def test_ensure_slot_initialized(self, tmp_path: Path):
        """Creates slot directory with required subdirectories."""
        with patch("microvm_orchestrator.core.vm.get_slot_dir", return_value=tmp_path / "slot1"):
            slot_dir = ensure_slot_initialized(1)

            assert slot_dir.exists()
            assert (slot_dir / "var").exists()
            assert (slot_dir / "container-storage").exists()

    def test_ensure_slot_initialized_idempotent(self, tmp_path: Path):
        """Can be called multiple times safely."""
        with patch("microvm_orchestrator.core.vm.get_slot_dir", return_value=tmp_path / "slot1"):
            slot_dir1 = ensure_slot_initialized(1)
            slot_dir2 = ensure_slot_initialized(1)

            assert slot_dir1 == slot_dir2
