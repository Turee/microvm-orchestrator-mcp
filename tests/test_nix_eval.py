"""Nix evaluation smoke tests.

Validates that the Nix expressions can be evaluated (instantiated) on the
current platform without actually building anything.  `nix-instantiate`
resolves the full derivation graph in ~1-2 s and catches platform-specific
package-availability errors that unit tests (which mock subprocess) miss.

See: https://github.com/anthropics/microvm-orchestrator-mcp/issues/126
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HAS_NIX = shutil.which("nix-instantiate") is not None

# Repo root relative to this file: tests/ -> project root
DEFAULT_NIX = Path(__file__).resolve().parent.parent / "default.nix"


@pytest.mark.nix
@pytest.mark.timeout(30)
@pytest.mark.skipif(not HAS_NIX, reason="nix-instantiate not found on PATH")
class TestNixEvaluation:
    """Smoke tests that nix-instantiate can evaluate our derivations."""

    def test_default_nix_instantiates(self, tmp_path: Path) -> None:
        """Evaluate default.nix with dummy args to catch platform errors.

        This catches issues like virtiofsd (Linux-only) being pulled into the
        derivation graph on macOS/Darwin hosts.  See issue #126.
        """
        assert DEFAULT_NIX.exists(), f"default.nix not found at {DEFAULT_NIX}"

        task_dir = tmp_path / "task"
        task_dir.mkdir()

        result = subprocess.run(
            [
                "nix-instantiate",
                str(DEFAULT_NIX),
                "--argstr", "taskDir", str(task_dir),
                "--argstr", "nixStoreImage", "/tmp/nix-eval-test.img",
                "--argstr", "socketPath", "/tmp/nix-eval-test.sock",
                "--argstr", "slot", "1",
                "-A", "claude-microvm",
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )

        if result.returncode != 0:
            stderr = result.stderr
            if "is not available on the requested hostPlatform" in stderr:
                detail = (
                    "A package in the derivation is not available for this "
                    "platform.  This is likely the virtiofsd issue tracked in "
                    "#126.  See stderr below.\n\n"
                )
            else:
                detail = "nix-instantiate failed (see stderr below).\n\n"

            pytest.fail(
                f"{detail}"
                f"exit code: {result.returncode}\n"
                f"stderr:\n{stderr}"
            )
