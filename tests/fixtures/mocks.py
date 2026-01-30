"""Mock helpers for subprocess, PTY, and OS operations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from unittest.mock import MagicMock


@dataclass
class MockCompletedProcess:
    """Mock subprocess.CompletedProcess for git command tests."""

    args: list[str] = field(default_factory=list)
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class SubprocessMock:
    """
    Context manager for mocking subprocess.run with configurable responses.

    Usage:
        with SubprocessMock() as mock:
            mock.set_response(["git", "status"], returncode=0, stdout="clean")
            result = subprocess.run(["git", "status"], ...)
    """

    def __init__(self):
        self._responses: dict[tuple[str, ...], MockCompletedProcess] = {}
        self._default_response = MockCompletedProcess()
        self._call_history: list[list[str]] = []
        self._original_run: Optional[Callable] = None

    def set_response(
        self,
        cmd: list[str],
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Set response for a specific command."""
        key = tuple(cmd)
        self._responses[key] = MockCompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def set_git_response(
        self,
        git_args: list[str],
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Set response for a git command (auto-prepends 'git')."""
        self.set_response(["git"] + git_args, returncode, stdout, stderr)

    def set_default(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Set default response for unmatched commands."""
        self._default_response = MockCompletedProcess(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _mock_run(
        self,
        args: list[str],
        **kwargs: Any,
    ) -> MockCompletedProcess:
        """Mock implementation of subprocess.run."""
        self._call_history.append(args)
        key = tuple(args)

        response = self._responses.get(key, self._default_response)

        # Handle check=True
        if kwargs.get("check") and response.returncode != 0:
            raise subprocess.CalledProcessError(
                response.returncode,
                args,
                response.stdout,
                response.stderr,
            )

        return MockCompletedProcess(
            args=args,
            returncode=response.returncode,
            stdout=response.stdout,
            stderr=response.stderr,
        )

    @property
    def calls(self) -> list[list[str]]:
        """Get list of all commands that were called."""
        return self._call_history

    def __enter__(self) -> SubprocessMock:
        self._original_run = subprocess.run
        subprocess.run = self._mock_run
        return self

    def __exit__(self, *args: Any) -> None:
        if self._original_run:
            subprocess.run = self._original_run


class PopenMock:
    """Mock for subprocess.Popen used by VMProcess."""

    def __init__(
        self,
        pid: int = 12345,
        returncode: Optional[int] = None,
    ):
        self.pid = pid
        self._returncode = returncode
        self._terminated = False
        self._killed = False

    def poll(self) -> Optional[int]:
        """Check if process has finished."""
        if self._terminated or self._killed:
            return self._returncode if self._returncode is not None else 0
        return None

    def terminate(self) -> None:
        """Terminate the process."""
        self._terminated = True

    def kill(self) -> None:
        """Kill the process."""
        self._killed = True

    def wait(self, timeout: Optional[float] = None) -> int:
        """Wait for process to finish."""
        self._terminated = True
        return self._returncode if self._returncode is not None else 0

    @property
    def returncode(self) -> Optional[int]:
        """Get return code."""
        if self._terminated or self._killed:
            return self._returncode if self._returncode is not None else 0
        return None


class PTYMock:
    """Mock for PTY operations (pty.openpty, os.read, select.select)."""

    def __init__(self, output_data: bytes = b""):
        self.output_data = output_data
        self._read_position = 0
        self.master_fd = 100
        self.slave_fd = 101
        self._closed_fds: set[int] = set()

    def openpty(self) -> tuple[int, int]:
        """Mock pty.openpty()."""
        return self.master_fd, self.slave_fd

    def read(self, fd: int, size: int) -> bytes:
        """Mock os.read()."""
        if fd in self._closed_fds:
            raise OSError("PTY closed")

        if self._read_position >= len(self.output_data):
            raise OSError("No more data")

        chunk = self.output_data[self._read_position:self._read_position + size]
        self._read_position += len(chunk)
        return chunk

    def close(self, fd: int) -> None:
        """Mock os.close()."""
        self._closed_fds.add(fd)

    def select(
        self,
        rlist: list[int],
        wlist: list[int],
        xlist: list[int],
        timeout: Optional[float] = None,
    ) -> tuple[list[int], list[int], list[int]]:
        """Mock select.select()."""
        # Return readable if there's data and fd is not closed
        readable = [fd for fd in rlist if fd not in self._closed_fds and self._read_position < len(self.output_data)]
        return readable, [], []


def create_git_repo(path: Path) -> None:
    """Create a minimal git repository for testing."""
    path.mkdir(parents=True, exist_ok=True)
    git_dir = path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
    )


def mock_orchestrator_deps() -> dict[str, MagicMock]:
    """Create mocks for Orchestrator's external dependencies."""
    return {
        "vm_process": MagicMock(spec=["start", "stop", "is_running", "exit_code"]),
        "git_setup": MagicMock(return_value="abc123"),
        "git_merge": MagicMock(),
    }
