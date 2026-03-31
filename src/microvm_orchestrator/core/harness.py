"""Harness strategy interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Harness(ABC):
    """Strategy interface for AI coding harnesses running inside the microVM."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier written to /workspace/harness for VM-side dispatch."""

    @abstractmethod
    def write_task_files(self, task_dir: Path, api_key: str, *, model: str = "") -> None:
        """Write harness-specific files into task_dir.

        Common files (task.md, start-ref, task-id, harness) are written by the caller.
        """

    @abstractmethod
    def nix_packages(self) -> list[str]:
        """Nix packages this harness requires in the VM image."""


_REGISTRY: dict[str, type[Harness]] = {}


def register_harness(cls: type[Harness]) -> type[Harness]:
    """Class decorator to register a harness implementation."""
    _REGISTRY[cls().name] = cls
    return cls


def get_harness(name: str) -> Harness:
    """Look up a harness by name. Raises ValueError if unknown."""
    if name not in _REGISTRY:
        raise ValueError(f"Unknown harness '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def available_harnesses() -> list[str]:
    """Return sorted list of registered harness names."""
    return sorted(_REGISTRY)
