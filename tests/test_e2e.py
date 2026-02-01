"""End-to-end integration tests with real microVMs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

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

TASK_DESCRIPTION = """
Create a file named 'hello.txt' with the content 'integration test passed'.
Then write result.json with {"success": true, "summary": "Created hello.txt"}.
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
        """Get API key from environment."""
        key = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            pytest.skip("CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY not set")
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
        # Create orchestrator with temp project
        orchestrator = Orchestrator(repo_path=e2e_project)
        # Register the project so we can use repo alias
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

            # On failure, capture logs before assertions
            if event.get("event") != "completed":
                log_path = orchestrator.get_task_logs(task_id).get("log_path")
                if log_path and Path(log_path).exists():
                    log_content = Path(log_path).read_text()[-5000:]  # Last 5k chars
                    pytest.fail(f"Task failed. Event: {event}\nLogs (last 5k):\n{log_content}")

            # Verify completion
            assert event.get("event") == "completed", f"Expected 'completed', got: {event}"
            assert event.get("exit_code") == 0, f"Expected exit_code 0, got: {event.get('exit_code')}"

            # Verify hello.txt was created
            hello_file = repo_path / "hello.txt"
            if not hello_file.exists():
                # Show what's in the repo for debugging
                files = list(repo_path.rglob("*"))
                result_json = repo_path.parent / "result.json"
                result_content = result_json.read_text() if result_json.exists() else "not found"
                log_path = orchestrator.get_task_logs(task_id).get("log_path")
                log_content = Path(log_path).read_text()[-3000:] if log_path and Path(log_path).exists() else "not found"
                pytest.fail(
                    f"Expected {hello_file} to exist\n"
                    f"Files in repo: {[str(f.relative_to(repo_path)) for f in files if f.is_file()]}\n"
                    f"result.json: {result_content}\n"
                    f"Logs (last 3k):\n{log_content}"
                )
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

            # Create orchestrator and register project
            orchestrator = Orchestrator(repo_path=project)
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

                # On failure, capture logs before assertions
                if event.get("event") != "completed":
                    log_path = orchestrator.get_task_logs(task_id).get("log_path")
                    if log_path and Path(log_path).exists():
                        log_content = Path(log_path).read_text()[-5000:]  # Last 5k chars
                        pytest.fail(f"x86_64 task failed (Rosetta may not be working). Event: {event}\nLogs (last 5k):\n{log_content}")

                # Verify completion
                assert event.get("event") == "completed", f"Expected 'completed', got: {event}"
                assert event.get("exit_code") == 0, f"Expected exit_code 0, got: {event.get('exit_code')}"

                # Verify hello.txt was created
                hello_file = repo_path / "hello.txt"
                if not hello_file.exists():
                    # Show what's in the repo for debugging
                    files = list(repo_path.rglob("*"))
                    result_json = repo_path.parent / "result.json"
                    result_content = result_json.read_text() if result_json.exists() else "not found"
                    log_path = orchestrator.get_task_logs(task_id).get("log_path")
                    log_content = Path(log_path).read_text()[-3000:] if log_path and Path(log_path).exists() else "not found"
                    pytest.fail(
                        f"Expected {hello_file} to exist\n"
                        f"Files in repo: {[str(f.relative_to(repo_path)) for f in files if f.is_file()]}\n"
                        f"result.json: {result_content}\n"
                        f"Logs (last 3k):\n{log_content}"
                    )
                content = hello_file.read_text()
                assert "integration test passed" in content, f"Unexpected content: {content}"

            finally:
                # Always cleanup
                await orchestrator.cleanup_task(task_id)

        finally:
            # Clean up temp directory
            shutil.rmtree(project, ignore_errors=True)
