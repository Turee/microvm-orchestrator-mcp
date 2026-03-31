"""Claude Code harness strategy."""

from __future__ import annotations

from pathlib import Path

from ..harness import Harness, register_harness


@register_harness
class ClaudeCodeHarness(Harness):
    """Harness that runs Claude Code inside the microVM."""

    @property
    def name(self) -> str:
        return "claude-code"

    def write_task_files(self, task_dir: Path, api_key: str, *, model: str = "") -> None:
        """Write .api-key and optional model file for Claude Code."""
        api_key_file = task_dir / ".api-key"
        api_key_file.write_text(api_key)
        api_key_file.chmod(0o600)
        if model:
            (task_dir / "model").write_text(model)

    def nix_packages(self) -> list[str]:
        return ["claude-code"]
