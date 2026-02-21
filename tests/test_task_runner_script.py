"""Regression tests for the in-VM task runner shell script."""

from __future__ import annotations

from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "microvm_orchestrator"
    / "nix"
    / "scripts"
    / "run-claude-task.sh"
)


def _script_text() -> str:
    return SCRIPT_PATH.read_text()


def test_task_runner_has_stage_logging_and_timeouts() -> None:
    """Critical launch stages should be logged and timeout-bounded."""
    script = _script_text()

    assert 'CHOWN_TIMEOUT_SEC="${CHOWN_TIMEOUT_SEC:-120}"' in script
    assert 'NIX_DEVELOP_PROBE_TIMEOUT_SEC="${NIX_DEVELOP_PROBE_TIMEOUT_SEC:-180}"' in script
    assert 'CLAUDE_LAUNCH_TIMEOUT_SEC="${CLAUDE_LAUNCH_TIMEOUT_SEC:-7200}"' in script
    assert "run_with_timeout()" in script
    assert 'log "DEBUG: ownership stage start"' in script
    assert 'log "DEBUG: native devShell probe start' in script
    assert 'log "DEBUG: Claude launch stage start' in script


def test_task_runner_uses_narrow_ownership_then_fallback() -> None:
    """Repo ownership should use a narrow-first strategy with fallback."""
    script = _script_text()

    assert 'run_with_timeout "$CHOWN_TIMEOUT_SEC" "chown repo root" chown claude:users "$REPO_DIR"' in script
    assert 'run_with_timeout "$CHOWN_TIMEOUT_SEC" "chown .git metadata" chown -R claude:users "$REPO_DIR/.git"' in script
    assert 'ownership probe failed; escalating to recursive chown' in script
    assert 'run_with_timeout "$CHOWN_TIMEOUT_SEC" "recursive repo chown fallback" chown -R claude:users "$REPO_DIR"' in script


def test_task_runner_writes_timeout_failures_to_result() -> None:
    """Launch/probe timeouts should surface explicit failure results."""
    script = _script_text()

    assert 'write_result false "Task launch timeout" "Timed out during native nix develop probe' in script
    assert 'write_result false "Task launch timeout" "Timed out running Claude launch pipeline' in script
