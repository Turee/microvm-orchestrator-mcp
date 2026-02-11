"""End-to-end integration tests with real microVMs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from microvm_orchestrator.core.registry import RepoRegistry
from microvm_orchestrator.core.slots import SlotManager
from microvm_orchestrator.tools import Orchestrator


MINIMAL_FLAKE_NIX = """\
{
  description = "Test project for e2e integration";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: {
    devShells.x86_64-linux.default =
      nixpkgs.legacyPackages.x86_64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.x86_64-linux.coreutils ];
      };
    devShells.aarch64-linux.default =
      nixpkgs.legacyPackages.aarch64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.aarch64-linux.coreutils ];
      };
  };
}
"""

X86_ONLY_FLAKE_NIX = """\
{
  description = "Test project with x86_64-only devShell (tests Rosetta)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: {
    devShells.x86_64-linux.default =
      nixpkgs.legacyPackages.x86_64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.x86_64-linux.coreutils ];
      };
  };
}
"""

TASK_DESCRIPTION = """\
Create a file named 'hello.txt' with the exact content 'integration test passed'.
Then git add and git commit the file with message 'Add hello.txt'.
"""


@pytest.fixture
def e2e_project() -> Path:
    """Create a minimal flake project for e2e testing.

    Creates a temp git repo with a valid flake.nix that:
    - Supports both x86_64-linux and aarch64-linux (VMs match host arch)
    - Only depends on coreutils (minimal download)
    - Has no flake-utils to reduce fetch time

    Note: Uses a short temp path (/tmp/e2e-XXXX) to avoid macOS unix socket
    path length limits (104-108 chars max).
    """
    # Use short temp path to avoid macOS unix socket path limits
    project = Path(tempfile.mkdtemp(prefix="e2e-", dir="/tmp"))

    try:
        # Initialize git repo with config
        subprocess.run(
            ["git", "init"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@e2e.test"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "E2E Test"],
            cwd=project,
            check=True,
            capture_output=True,
        )

        # Create flake.nix
        (project / "flake.nix").write_text(MINIMAL_FLAKE_NIX)

        # Create README
        (project / "README.md").write_text("# E2E Test Project\n")

        # Initial commit
        subprocess.run(
            ["git", "add", "."],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=project,
            check=True,
            capture_output=True,
        )

        yield project
    finally:
        # Clean up temp directory
        shutil.rmtree(project, ignore_errors=True)


@pytest.mark.slow
@pytest.mark.timeout(300)
class TestEndToEndIntegration:
    """End-to-end integration tests with real microVMs."""

    @pytest.fixture
    def api_key(self) -> str:
        """Get API key from environment or token file."""
        key = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            token_file = Path.home() / ".microvm-orchestrator" / "token"
            if token_file.exists():
                key = token_file.read_text().strip()
        if not key:
            pytest.skip(
                "No API key found. Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY, "
                "or run 'microvm-orchestrator setup-token'"
            )
        return key

    async def test_smoke_vm_creates_file(
        self,
        e2e_project: Path,
        api_key: str,
    ) -> None:
        """Smoke test: start VM, run task, verify file creation, cleanup.

        This test verifies the basic happy path:
        1. Start a VM with a simple task
        2. Wait for task completion
        3. Verify the expected file was created
        4. Clean up the task
        """
        # Create orchestrator with isolated registry (avoids global registry pollution)
        orchestrator = Orchestrator(repo_path=e2e_project)
        isolated_dir = e2e_project / ".microvm"
        isolated_dir.mkdir(exist_ok=True)
        orchestrator.registry = RepoRegistry(registry_path=isolated_dir / "test-repos.json")
        orchestrator.slot_manager = SlotManager(assignments_path=isolated_dir / "test-slots.json")
        orchestrator.registry.allow(e2e_project, alias="e2e-test")

        # Start task using repo alias (slot assigned automatically)
        result = await orchestrator.run_task(TASK_DESCRIPTION, repo="e2e-test")
        task_id = result["task_id"]

        try:
            # Wait for completion (5 minute timeout)
            event = await orchestrator.wait_next_event(timeout_ms=300_000)

            # Get task info for debugging
            task_info = orchestrator.get_task_info(task_id)
            repo_path = Path(task_info["repo_path"])
            isolated_repo = Path(task_info["isolated_repo_path"])

            # Helper: collect diagnostic info for failure messages
            def _diag() -> str:
                parts = [f"Event: {event}"]
                parts.append(f"Result: {event.get('result')}")
                parts.append(f"Merge result: {event.get('merge_result')}")
                # Check isolated repo for uncommitted files
                if isolated_repo.exists():
                    iso_files = [
                        str(f.relative_to(isolated_repo))
                        for f in isolated_repo.rglob("*")
                        if f.is_file() and ".git" not in f.parts
                    ]
                    parts.append(f"Isolated repo files: {iso_files}")
                try:
                    log_path = orchestrator.get_task_logs(task_id).get("log_path")
                    if log_path and Path(log_path).exists():
                        parts.append(f"Logs (last 3k):\n{Path(log_path).read_text()[-3000:]}")
                except Exception:
                    pass
                return "\n".join(parts)

            # On failure, capture logs before assertions
            if event.get("event") != "completed":
                pytest.fail(f"Task did not complete.\n{_diag()}")

            # Verify completion
            assert event.get("event") == "completed", f"Expected 'completed', got: {event}"
            assert event.get("exit_code") == 0, f"Expected exit_code 0, got: {event.get('exit_code')}"

            # Verify hello.txt was merged back to original repo
            hello_file = repo_path / "hello.txt"
            if not hello_file.exists():
                pytest.fail(f"Expected {hello_file} to exist\n{_diag()}")
            content = hello_file.read_text()
            assert "integration test passed" in content, f"Unexpected content: {content}"

        finally:
            # Always cleanup
            await orchestrator.cleanup_task(task_id)

    async def test_rosetta_x86_translation(
        self,
        api_key: str,
    ) -> None:
        """Test Rosetta x86_64 binary translation on Apple Silicon.

        This test verifies that:
        1. A flake with ONLY x86_64-linux devShell works in the aarch64 VM
        2. Rosetta transparently translates x86_64 binaries to ARM
        3. The task completes successfully despite architecture mismatch
        """
        # Create temp project with x86-only flake
        project = Path(tempfile.mkdtemp(prefix="e2e-x86-", dir="/tmp"))

        try:
            # Initialize git repo with config
            subprocess.run(
                ["git", "init"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@e2e.test"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "E2E Test"],
                cwd=project,
                check=True,
                capture_output=True,
            )

            # Create x86-only flake.nix
            (project / "flake.nix").write_text(X86_ONLY_FLAKE_NIX)

            # Create README
            (project / "README.md").write_text("# E2E x86_64 Test Project\n")

            # Initial commit
            subprocess.run(
                ["git", "add", "."],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=project,
                check=True,
                capture_output=True,
            )

            # Create orchestrator with isolated registry (avoids global registry pollution)
            orchestrator = Orchestrator(repo_path=project)
            isolated_dir = project / ".microvm"
            isolated_dir.mkdir(exist_ok=True)
            orchestrator.registry = RepoRegistry(registry_path=isolated_dir / "test-repos.json")
            orchestrator.slot_manager = SlotManager(assignments_path=isolated_dir / "test-slots.json")
            orchestrator.registry.allow(project, alias="x86-test")

            # Start task using repo alias (slot assigned automatically)
            result = await orchestrator.run_task(TASK_DESCRIPTION, repo="x86-test")
            task_id = result["task_id"]

            try:
                # Wait for completion (5 minute timeout)
                event = await orchestrator.wait_next_event(timeout_ms=300_000)

                # Get task info for debugging
                task_info = orchestrator.get_task_info(task_id)
                repo_path = Path(task_info["repo_path"])
                isolated_repo = Path(task_info["isolated_repo_path"])

                # Helper: collect diagnostic info for failure messages
                def _diag() -> str:
                    parts = [f"Event: {event}"]
                    parts.append(f"Result: {event.get('result')}")
                    parts.append(f"Merge result: {event.get('merge_result')}")
                    if isolated_repo.exists():
                        iso_files = [
                            str(f.relative_to(isolated_repo))
                            for f in isolated_repo.rglob("*")
                            if f.is_file() and ".git" not in f.parts
                        ]
                        parts.append(f"Isolated repo files: {iso_files}")
                    try:
                        log_path = orchestrator.get_task_logs(task_id).get("log_path")
                        if log_path and Path(log_path).exists():
                            parts.append(f"Logs (last 3k):\n{Path(log_path).read_text()[-3000:]}")
                    except Exception:
                        pass
                    return "\n".join(parts)

                # On failure, capture logs before assertions
                if event.get("event") != "completed":
                    pytest.fail(
                        f"x86_64 task failed (Rosetta may not be working).\n{_diag()}"
                    )

                # Verify completion
                assert event.get("event") == "completed", f"Expected 'completed', got: {event}"
                assert event.get("exit_code") == 0, f"Expected exit_code 0, got: {event.get('exit_code')}"

                # Verify hello.txt was merged back to original repo
                hello_file = repo_path / "hello.txt"
                if not hello_file.exists():
                    pytest.fail(f"Expected {hello_file} to exist\n{_diag()}")
                content = hello_file.read_text()
                assert "integration test passed" in content, f"Unexpected content: {content}"

            finally:
                # Always cleanup
                await orchestrator.cleanup_task(task_id)

        finally:
            # Clean up temp directory
            shutil.rmtree(project, ignore_errors=True)
