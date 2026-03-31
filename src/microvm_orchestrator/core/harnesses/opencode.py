"""OpenCode harness strategy."""

from __future__ import annotations

import json
from pathlib import Path

from ..harness import Harness, register_harness


@register_harness
class OpenCodeHarness(Harness):
    """Harness that runs opencode inside the microVM."""

    @property
    def name(self) -> str:
        return "opencode"

    def write_task_files(self, task_dir: Path, api_key: str, *, model: str = "") -> None:
        """Write .api-key, opencode-config.json, and optional model file for opencode."""
        api_key_file = task_dir / ".api-key"
        api_key_file.write_text(api_key)
        api_key_file.chmod(0o600)

        config: dict = {"providers": {"anthropic": {}}}
        if model:
            config["agents"] = {"coder": {"model": model}}
        (task_dir / "opencode-config.json").write_text(json.dumps(config, indent=2))

        if model:
            (task_dir / "model").write_text(model)

    def nix_packages(self) -> list[str]:
        return ["opencode"]
