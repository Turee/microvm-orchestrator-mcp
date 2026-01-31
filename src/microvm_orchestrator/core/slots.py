"""Slot manager for automatic slot assignment with repo affinity."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class AllSlotsBusyError(Exception):
    """Raised when all slots are occupied and no slot can be acquired."""

    def __init__(self, max_slots: int, active_tasks: dict[int, str]):
        self.max_slots = max_slots
        self.active_tasks = active_tasks
        super().__init__(
            f"All {max_slots} slots are busy. Active tasks: {list(active_tasks.values())}"
        )


@dataclass
class SlotManager:
    """
    Manages slot assignment with repo affinity for cache reuse.

    Strategy:
    1. Hash repo path to get "preferred" slot (deterministic)
    2. If preferred slot is free -> use it (cache reuse benefit)
    3. If preferred slot is busy -> use any free slot
    4. If all slots busy -> raise AllSlotsBusyError

    Thread-safe for concurrent slot acquisition/release.

    Attributes:
        max_slots: Maximum number of slots (default: 10)
        assignments_path: Path to persist repo->slot affinity mapping
    """

    max_slots: int = 10
    assignments_path: Path = field(
        default_factory=lambda: Path.home() / ".microvm-orchestrator" / "slot-assignments.json"
    )

    # repo_path_hash -> preferred slot (persisted to disk)
    _repo_to_slot: dict[str, int] = field(default_factory=dict, repr=False)

    # slot -> task_id (in-memory only, tracks active tasks)
    _active_tasks: dict[int, str] = field(default_factory=dict, repr=False)

    # Thread lock for concurrent access
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        """Load persisted affinity mapping from disk."""
        self._load()

    def acquire_slot(self, repo_path: Path, task_id: str) -> int:
        """
        Acquire a slot with repo affinity, falling back to any free slot.

        Args:
            repo_path: Path to the repository (used for affinity hashing)
            task_id: Unique identifier for the task

        Returns:
            The assigned slot number (1 to max_slots)

        Raises:
            AllSlotsBusyError: If all slots are occupied
        """
        repo_hash = self._hash_path(repo_path)

        with self._lock:
            # Try preferred slot first (for cache reuse)
            if repo_hash in self._repo_to_slot:
                preferred = self._repo_to_slot[repo_hash]
                if preferred not in self._active_tasks:
                    self._active_tasks[preferred] = task_id
                    logger.info(
                        "Task %s: acquired preferred slot %d for repo %s",
                        task_id,
                        preferred,
                        repo_path,
                    )
                    return preferred

            # Find any free slot
            for slot in range(1, self.max_slots + 1):
                if slot not in self._active_tasks:
                    self._active_tasks[slot] = task_id
                    self._repo_to_slot[repo_hash] = slot
                    self._persist()
                    logger.info(
                        "Task %s: acquired slot %d (new affinity) for repo %s",
                        task_id,
                        slot,
                        repo_path,
                    )
                    return slot

            # No free slots
            logger.warning(
                "Task %s: all %d slots busy, cannot acquire for repo %s",
                task_id,
                self.max_slots,
                repo_path,
            )
            raise AllSlotsBusyError(self.max_slots, dict(self._active_tasks))

    def release_slot(self, slot: int) -> None:
        """
        Release a slot when a task completes.

        Args:
            slot: The slot number to release
        """
        with self._lock:
            if slot in self._active_tasks:
                task_id = self._active_tasks.pop(slot)
                logger.info("Task %s: released slot %d", task_id, slot)
            else:
                logger.warning("Attempted to release unoccupied slot %d", slot)

    def get_active_tasks(self) -> dict[int, str]:
        """
        Get a copy of currently active tasks.

        Returns:
            Dictionary mapping slot numbers to task IDs
        """
        with self._lock:
            return dict(self._active_tasks)

    def get_available_slots(self) -> list[int]:
        """
        Get list of available slot numbers.

        Returns:
            List of unoccupied slot numbers
        """
        with self._lock:
            return [
                slot
                for slot in range(1, self.max_slots + 1)
                if slot not in self._active_tasks
            ]

    def get_slot_for_task(self, task_id: str) -> Optional[int]:
        """
        Find which slot a task is using.

        Args:
            task_id: The task ID to look up

        Returns:
            Slot number if found, None otherwise
        """
        with self._lock:
            for slot, tid in self._active_tasks.items():
                if tid == task_id:
                    return slot
            return None

    def _hash_path(self, repo_path: Path) -> str:
        """
        Create a stable hash of the canonical repo path for affinity lookup.

        Args:
            repo_path: Path to hash

        Returns:
            Hex string hash of the resolved path
        """
        canonical = str(repo_path.resolve())
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _load(self) -> None:
        """Load persisted affinity mapping from disk."""
        if self.assignments_path.exists():
            try:
                data = json.loads(self.assignments_path.read_text())
                self._repo_to_slot = data.get("repo_to_slot", {})
                # Convert string keys from JSON to int values
                self._repo_to_slot = {k: int(v) for k, v in self._repo_to_slot.items()}
                logger.debug(
                    "Loaded %d slot affinity mappings from %s",
                    len(self._repo_to_slot),
                    self.assignments_path,
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(
                    "Failed to load slot assignments from %s: %s",
                    self.assignments_path,
                    e,
                )
                self._repo_to_slot = {}

    def _persist(self) -> None:
        """Persist affinity mapping to disk."""
        self.assignments_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"repo_to_slot": self._repo_to_slot}
        self.assignments_path.write_text(json.dumps(data, indent=2))
        logger.debug(
            "Persisted %d slot affinity mappings to %s",
            len(self._repo_to_slot),
            self.assignments_path,
        )
