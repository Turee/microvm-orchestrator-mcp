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
    """Critical launch stages should be logged and timeout-bounded where needed."""
    script = _script_text()

    assert 'CHOWN_TIMEOUT_SEC="${CHOWN_TIMEOUT_SEC:-120}"' in script
    assert 'CLAUDE_LAUNCH_TIMEOUT_SEC="${CLAUDE_LAUNCH_TIMEOUT_SEC:-0}"' in script
    assert "run_with_timeout()" in script
    assert 'log "DEBUG: ownership stage start"' in script
    assert 'log "DEBUG: native devShell probe start"' in script
    assert 'log "DEBUG: Claude launch stage start' in script


def test_task_runner_uses_narrow_ownership_then_fallback() -> None:
    """Repo ownership should use a narrow-first strategy with fallback."""
    script = _script_text()

    assert 'run_with_timeout "$CHOWN_TIMEOUT_SEC" "chown repo root" chown claude:users "$REPO_DIR"' in script
    assert 'run_with_timeout "$CHOWN_TIMEOUT_SEC" "chown .git metadata" chown -R claude:users "$REPO_DIR/.git"' in script
    assert 'ownership probe failed; escalating to recursive chown' in script
    assert 'run_with_timeout "$CHOWN_TIMEOUT_SEC" "recursive repo chown fallback" chown -R claude:users "$REPO_DIR"' in script


def test_task_runner_native_probe_has_no_timeout() -> None:
    """Native nix develop probe should run without a watchdog timeout."""
    script = _script_text()

    assert 'if su -s @bash@/bin/bash claude -c "cd \\"$REPO_DIR\\" && @nix@/bin/nix develop path:. --command true"' in script
    assert 'run_with_timeout "$NIX_DEVELOP_PROBE_TIMEOUT_SEC" "native nix develop probe"' not in script
    assert 'Timed out during native nix develop probe' not in script


def test_task_runner_writes_launch_timeout_failures_to_result() -> None:
    """Claude launch pipeline timeout should surface explicit failure results."""
    script = _script_text()

    assert 'write_result false "Task launch timeout" "Timed out running Claude launch pipeline' in script
