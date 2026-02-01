"""Integration tests for nix develop environment feature."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from microvm_orchestrator.tools import Orchestrator


BUN_FLAKE_NIX = """\
{
  description = "Test project with bun runtime";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: {
    devShells.aarch64-linux.default =
      nixpkgs.legacyPackages.aarch64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.aarch64-linux.bun ];
      };
    devShells.x86_64-linux.default =
      nixpkgs.legacyPackages.x86_64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.x86_64-linux.bun ];
      };
  };
}
"""

COWSAY_BASE_FLAKE_NIX = """\
{
  description = "Minimal test project for dynamic dependency addition";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }: {
    devShells.aarch64-linux.default =
      nixpkgs.legacyPackages.aarch64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.aarch64-linux.coreutils ];
      };
    devShells.x86_64-linux.default =
      nixpkgs.legacyPackages.x86_64-linux.mkShell {
        buildInputs = [ nixpkgs.legacyPackages.x86_64-linux.coreutils ];
      };
  };
}
"""

TASK_BUN_VERSION = """
Run 'bun --version' and write the output to version.txt.
Then write result.json with {"success": true, "version": "<the version you found>"}.
"""

TASK_ADD_COWSAY = """
Add 'cowsay' to the flake.nix buildInputs, then run 'cowsay hello' and save the
output to cowsay.txt.

Steps:
1. Edit flake.nix to add cowsay package to buildInputs (for both aarch64-linux and x86_64-linux)
2. Commit your changes
3. Run 'cowsay hello' and save output to cowsay.txt
4. Write result.json with {"success": true, "summary": "Added cowsay and ran it"}
"""


@pytest.fixture
def bun_flake_project() -> Path:
    """Create a flake project with bun in buildInputs.

    Creates a temp git repo with a valid flake.nix that:
    - Supports both x86_64-linux and aarch64-linux (VMs match host arch)
    - Includes bun runtime in buildInputs
    - Has no flake-utils to reduce fetch time

    Note: Uses a short temp path (/tmp/nix-bun-XXXX) to avoid macOS unix socket
    path length limits (104-108 chars max).
    """
    # Use short temp path to avoid macOS unix socket path limits
    project = Path(tempfile.mkdtemp(prefix="nix-bun-", dir="/tmp"))

    try:
        # Initialize git repo with config
        subprocess.run(
            ["git", "init"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@nixdev.test"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Nix Develop Test"],
            cwd=project,
            check=True,
            capture_output=True,
        )

        # Create flake.nix with bun
        (project / "flake.nix").write_text(BUN_FLAKE_NIX)

        # Create README
        (project / "README.md").write_text("# Nix Develop Bun Test Project\n")

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


@pytest.fixture
def minimal_flake_project() -> Path:
    """Create a minimal flake project without cowsay.

    Creates a temp git repo with a valid flake.nix that:
    - Supports both x86_64-linux and aarch64-linux
    - Only has coreutils (no cowsay initially)
    - Has no flake-utils to reduce fetch time

    Note: Uses a short temp path (/tmp/nix-min-XXXX) to avoid macOS unix socket
    path length limits.
    """
    # Use short temp path to avoid macOS unix socket path limits
    project = Path(tempfile.mkdtemp(prefix="nix-min-", dir="/tmp"))

    try:
        # Initialize git repo with config
        subprocess.run(
            ["git", "init"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@nixdev.test"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Nix Develop Test"],
            cwd=project,
            check=True,
            capture_output=True,
        )

        # Create minimal flake.nix (no cowsay)
        (project / "flake.nix").write_text(COWSAY_BASE_FLAKE_NIX)

        # Create README
        (project / "README.md").write_text("# Nix Develop Minimal Test Project\n")

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
class TestNixDevelopIntegration:
    """Integration tests for nix develop environment feature."""

    @pytest.fixture
    def api_key(self) -> str:
        """Get API key from environment."""
        key = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            pytest.skip("CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY not set")
        return key

    async def test_bun_version_from_flake(
        self,
        bun_flake_project: Path,
        api_key: str,
    ) -> None:
        """Test running bun from flake.nix buildInputs.

        This test verifies that:
        1. VM enters nix develop environment with bun available
        2. Claude can run bun --version successfully
        3. The version output is captured correctly
        """
        # Create orchestrator and register project
        orchestrator = Orchestrator(repo_path=bun_flake_project)
        orchestrator.registry.allow(bun_flake_project, alias="bun-test")

        # Start task using repo alias (slot assigned automatically)
        result = await orchestrator.run_task(TASK_BUN_VERSION, repo="bun-test")
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

            # Verify version.txt was created with version pattern
            version_file = repo_path / "version.txt"
            if not version_file.exists():
                # Show what's in the repo for debugging
                files = list(repo_path.rglob("*"))
                result_json = repo_path.parent / "result.json"
                result_content = result_json.read_text() if result_json.exists() else "not found"
                log_path = orchestrator.get_task_logs(task_id).get("log_path")
                log_content = Path(log_path).read_text()[-3000:] if log_path and Path(log_path).exists() else "not found"
                pytest.fail(
                    f"Expected {version_file} to exist\n"
                    f"Files in repo: {[str(f.relative_to(repo_path)) for f in files if f.is_file()]}\n"
                    f"result.json: {result_content}\n"
                    f"Logs (last 3k):\n{log_content}"
                )

            # Verify version format (e.g., "1.0.23")
            version_content = version_file.read_text()
            version_pattern = r"\d+\.\d+\.\d+"
            if not re.search(version_pattern, version_content):
                pytest.fail(f"Expected version pattern {version_pattern} in version.txt, got: {version_content}")

        finally:
            # Always cleanup
            await orchestrator.cleanup_task(task_id)

    async def test_dynamic_dependency_addition(
        self,
        minimal_flake_project: Path,
        api_key: str,
    ) -> None:
        """Test dynamically adding cowsay to flake.nix and using it.

        This test verifies that:
        1. Claude can edit flake.nix to add a new package (cowsay)
        2. Claude commits the changes
        3. The writable Nix store allows rebuilding the environment
        4. The new tool becomes available after modification
        """
        # Create orchestrator and register project
        orchestrator = Orchestrator(repo_path=minimal_flake_project)
        orchestrator.registry.allow(minimal_flake_project, alias="cowsay-test")

        # Start task using repo alias (slot assigned automatically)
        result = await orchestrator.run_task(TASK_ADD_COWSAY, repo="cowsay-test")
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

            # Verify flake.nix was modified to include cowsay
            flake_path = repo_path / "flake.nix"
            if not flake_path.exists():
                pytest.fail(f"Expected {flake_path} to exist")
            flake_content = flake_path.read_text()
            assert "cowsay" in flake_content, f"Expected 'cowsay' in flake.nix, got:\n{flake_content}"

            # Verify cowsay.txt was created
            cowsay_file = repo_path / "cowsay.txt"
            if not cowsay_file.exists():
                # Show what's in the repo for debugging
                files = list(repo_path.rglob("*"))
                result_json = repo_path.parent / "result.json"
                result_content = result_json.read_text() if result_json.exists() else "not found"
                log_path = orchestrator.get_task_logs(task_id).get("log_path")
                log_content = Path(log_path).read_text()[-3000:] if log_path and Path(log_path).exists() else "not found"
                pytest.fail(
                    f"Expected {cowsay_file} to exist\n"
                    f"Files in repo: {[str(f.relative_to(repo_path)) for f in files if f.is_file()]}\n"
                    f"result.json: {result_content}\n"
                    f"Logs (last 3k):\n{log_content}"
                )

            # Verify cowsay output contains "hello"
            cowsay_content = cowsay_file.read_text()
            assert "hello" in cowsay_content.lower(), f"Expected 'hello' in cowsay.txt, got: {cowsay_content}"

            # Verify commits were made
            assert task_info["commit_count"] > 0, f"Expected commits to be made, got: {task_info['commit_count']}"

        finally:
            # Always cleanup
            await orchestrator.cleanup_task(task_id)
