"""Tests for SlotManager (core/slots.py)."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from microvm_orchestrator.core.slots import AllSlotsBusyError, SlotManager


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def slot_manager(tmp_path: Path) -> SlotManager:
    """SlotManager with isolated assignments file."""
    return SlotManager(assignments_path=tmp_path / "slot-assignments.json")


@pytest.fixture
def small_slot_manager(tmp_path: Path) -> SlotManager:
    """SlotManager with only 2 slots for boundary testing."""
    return SlotManager(max_slots=2, assignments_path=tmp_path / "slot-assignments.json")


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    """A temporary repo directory."""
    p = tmp_path / "repo-alpha"
    p.mkdir()
    return p


@pytest.fixture
def repo_path_b(tmp_path: Path) -> Path:
    """A second temporary repo directory."""
    p = tmp_path / "repo-beta"
    p.mkdir()
    return p


# =============================================================================
# Acquire Slot Tests
# =============================================================================


class TestAcquireSlot:
    """Tests for SlotManager.acquire_slot()."""

    def test_returns_int_in_range(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Acquired slot is an int in [1, max_slots]."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")

        assert isinstance(slot, int)
        assert 1 <= slot <= slot_manager.max_slots

    def test_different_repos_get_different_slots(
        self, slot_manager: SlotManager, repo_path: Path, repo_path_b: Path
    ):
        """Two different repos get different slot numbers."""
        slot_a = slot_manager.acquire_slot(repo_path, "task-1")
        slot_b = slot_manager.acquire_slot(repo_path_b, "task-2")

        assert slot_a != slot_b

    def test_same_repo_reuses_preferred_slot(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Same repo gets the same preferred slot after release (affinity)."""
        slot_first = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot_first)

        slot_second = slot_manager.acquire_slot(repo_path, "task-2")

        assert slot_second == slot_first

    def test_preferred_slot_busy_falls_back(
        self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path
    ):
        """When preferred slot is busy, a different free slot is used."""
        slot_a = small_slot_manager.acquire_slot(repo_path, "task-1")
        # Acquire for same repo while first is still held -> falls back
        slot_b = small_slot_manager.acquire_slot(repo_path_b, "task-2")

        assert slot_b != slot_a
        assert 1 <= slot_b <= small_slot_manager.max_slots

    def test_all_slots_busy_raises(
        self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path, tmp_path: Path
    ):
        """AllSlotsBusyError raised when no slots available."""
        small_slot_manager.acquire_slot(repo_path, "task-1")
        small_slot_manager.acquire_slot(repo_path_b, "task-2")

        repo_c = tmp_path / "repo-gamma"
        repo_c.mkdir()
        with pytest.raises(AllSlotsBusyError):
            small_slot_manager.acquire_slot(repo_c, "task-3")

    def test_fallback_updates_affinity_in_json(
        self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path
    ):
        """Fallback to a new slot persists the new affinity mapping."""
        small_slot_manager.acquire_slot(repo_path, "task-1")
        small_slot_manager.acquire_slot(repo_path_b, "task-2")

        data = json.loads(small_slot_manager.assignments_path.read_text())
        assert len(data["repo_to_slot"]) == 2

    def test_preferred_reuse_does_not_write_disk(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Reusing preferred slot does NOT call _persist() (no disk write)."""
        slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(
            slot_manager.get_slot_for_task("task-1") or slot_manager.acquire_slot(repo_path, "task-1")
        )

        # Re-read file content after first acquire
        slot_first = slot_manager.acquire_slot(repo_path, "task-1a")
        slot_manager.release_slot(slot_first)
        content_before = slot_manager.assignments_path.read_text()
        mtime_before = slot_manager.assignments_path.stat().st_mtime_ns

        # Preferred reuse should NOT write to disk
        slot_manager.acquire_slot(repo_path, "task-1b")
        content_after = slot_manager.assignments_path.read_text()
        mtime_after = slot_manager.assignments_path.stat().st_mtime_ns

        assert content_before == content_after
        assert mtime_before == mtime_after

    def test_sequential_slots_start_at_one(
        self, slot_manager: SlotManager, tmp_path: Path
    ):
        """First slot assigned is 1 (1-based range)."""
        repo = tmp_path / "repo-first"
        repo.mkdir()
        slot = slot_manager.acquire_slot(repo, "task-first")

        assert slot == 1


# =============================================================================
# Repo Affinity Tests
# =============================================================================


class TestRepoAffinity:
    """Tests for repo path hashing and affinity."""

    def test_same_path_same_hash(self, slot_manager: SlotManager, repo_path: Path):
        """Same path always produces the same hash."""
        h1 = slot_manager._hash_path(repo_path)
        h2 = slot_manager._hash_path(repo_path)

        assert h1 == h2

    def test_different_paths_different_hashes(
        self, slot_manager: SlotManager, repo_path: Path, repo_path_b: Path
    ):
        """Different paths produce different hashes."""
        h1 = slot_manager._hash_path(repo_path)
        h2 = slot_manager._hash_path(repo_path_b)

        assert h1 != h2

    def test_symlink_resolves_to_same_hash(
        self, slot_manager: SlotManager, repo_path: Path, tmp_path: Path
    ):
        """Symlink to a repo resolves to the same hash as the real path."""
        link = tmp_path / "link-to-repo"
        link.symlink_to(repo_path)

        hash_real = slot_manager._hash_path(repo_path)
        hash_link = slot_manager._hash_path(link)

        assert hash_real == hash_link

    def test_hash_is_16_char_hex(self, slot_manager: SlotManager, repo_path: Path):
        """Hash is a 16-character hexadecimal string."""
        h = slot_manager._hash_path(repo_path)

        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_affinity_persists_across_instances(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Affinity mapping is loaded by a new SlotManager instance."""
        slot_first = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot_first)

        # New instance with same assignments_path
        sm2 = SlotManager(assignments_path=slot_manager.assignments_path)
        slot_second = sm2.acquire_slot(repo_path, "task-2")

        assert slot_second == slot_first


# =============================================================================
# Release Slot Tests
# =============================================================================


class TestReleaseSlot:
    """Tests for SlotManager.release_slot()."""

    def test_released_slot_becomes_available(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Released slot shows up in available slots."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot)

        assert slot in slot_manager.get_available_slots()

    def test_release_unoccupied_logs_warning(
        self, slot_manager: SlotManager, caplog: pytest.LogCaptureFixture
    ):
        """Releasing an unoccupied slot logs a warning."""
        with caplog.at_level(logging.WARNING):
            slot_manager.release_slot(99)

        assert "unoccupied" in caplog.text.lower()

    def test_release_then_reacquire_same_repo(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Release and reacquire the same repo returns the same slot."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot)

        slot_again = slot_manager.acquire_slot(repo_path, "task-2")

        assert slot_again == slot

    def test_release_removes_from_active_tasks(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Released task is no longer in active tasks."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot)

        assert slot not in slot_manager.get_active_tasks()


# =============================================================================
# Get Active Tasks Tests
# =============================================================================


class TestGetActiveTasks:
    """Tests for SlotManager.get_active_tasks()."""

    def test_empty_initially(self, slot_manager: SlotManager):
        """No active tasks on a fresh manager."""
        assert slot_manager.get_active_tasks() == {}

    def test_reflects_acquired_tasks(
        self, slot_manager: SlotManager, repo_path: Path, repo_path_b: Path
    ):
        """Active tasks includes all acquired slots."""
        slot_a = slot_manager.acquire_slot(repo_path, "task-1")
        slot_b = slot_manager.acquire_slot(repo_path_b, "task-2")

        active = slot_manager.get_active_tasks()

        assert active == {slot_a: "task-1", slot_b: "task-2"}

    def test_returns_copy(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Returned dict is a copy; mutation does not affect internal state."""
        slot_manager.acquire_slot(repo_path, "task-1")
        active = slot_manager.get_active_tasks()
        active.clear()

        assert len(slot_manager.get_active_tasks()) == 1


# =============================================================================
# Get Available Slots Tests
# =============================================================================


class TestGetAvailableSlots:
    """Tests for SlotManager.get_available_slots()."""

    def test_all_available_initially(self, slot_manager: SlotManager):
        """All 10 slots available on a fresh manager."""
        available = slot_manager.get_available_slots()

        assert len(available) == 10
        assert available == list(range(1, 11))

    def test_decreases_after_acquire(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """One fewer available slot after an acquire."""
        slot_manager.acquire_slot(repo_path, "task-1")

        assert len(slot_manager.get_available_slots()) == 9

    def test_increases_after_release(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Available count goes back up after release."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot)

        assert len(slot_manager.get_available_slots()) == 10

    def test_empty_when_full(self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path):
        """No available slots when all are occupied."""
        small_slot_manager.acquire_slot(repo_path, "task-1")
        small_slot_manager.acquire_slot(repo_path_b, "task-2")

        assert small_slot_manager.get_available_slots() == []


# =============================================================================
# Get Slot For Task Tests
# =============================================================================


class TestGetSlotForTask:
    """Tests for SlotManager.get_slot_for_task()."""

    def test_returns_slot_for_known_task(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Returns correct slot for an active task."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")

        assert slot_manager.get_slot_for_task("task-1") == slot

    def test_returns_none_for_unknown(self, slot_manager: SlotManager):
        """Returns None when task ID is not found."""
        assert slot_manager.get_slot_for_task("nonexistent") is None

    def test_returns_none_after_release(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """Returns None after the task's slot has been released."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot)

        assert slot_manager.get_slot_for_task("task-1") is None


# =============================================================================
# Persistence Tests
# =============================================================================


class TestPersistence:
    """Tests for JSON file I/O."""

    def test_creates_directory_structure(self, tmp_path: Path):
        """Parent directories are created for a nested assignments path."""
        nested = tmp_path / "deep" / "nested" / "dir" / "slots.json"
        sm = SlotManager(assignments_path=nested)

        repo = tmp_path / "repo"
        repo.mkdir()
        sm.acquire_slot(repo, "task-1")

        assert nested.exists()

    def test_persists_correct_json_structure(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """JSON file contains expected repo_to_slot mapping."""
        slot_manager.acquire_slot(repo_path, "task-1")

        data = json.loads(slot_manager.assignments_path.read_text())

        assert "repo_to_slot" in data
        assert isinstance(data["repo_to_slot"], dict)
        assert len(data["repo_to_slot"]) == 1
        # Value should be an int slot number
        slot_value = list(data["repo_to_slot"].values())[0]
        assert isinstance(slot_value, int)

    def test_loads_affinity_on_init(
        self, slot_manager: SlotManager, repo_path: Path
    ):
        """New instance loads persisted affinity from disk."""
        slot = slot_manager.acquire_slot(repo_path, "task-1")
        slot_manager.release_slot(slot)

        sm2 = SlotManager(assignments_path=slot_manager.assignments_path)
        slot2 = sm2.acquire_slot(repo_path, "task-2")

        assert slot2 == slot

    def test_handles_missing_file(self, tmp_path: Path):
        """Missing file means empty affinity, no error."""
        sm = SlotManager(assignments_path=tmp_path / "nonexistent.json")

        assert sm.get_active_tasks() == {}
        assert len(sm.get_available_slots()) == sm.max_slots

    def test_handles_corrupt_json(self, tmp_path: Path):
        """Corrupt JSON means empty affinity, no crash."""
        bad_file = tmp_path / "slot-assignments.json"
        bad_file.write_text("{not valid json!!")

        sm = SlotManager(assignments_path=bad_file)

        assert sm.get_active_tasks() == {}

    def test_handles_empty_file(self, tmp_path: Path):
        """Empty file means empty affinity, no crash."""
        empty_file = tmp_path / "slot-assignments.json"
        empty_file.write_text("")

        sm = SlotManager(assignments_path=empty_file)

        assert sm.get_active_tasks() == {}

    def test_handles_valid_json_missing_key(self, tmp_path: Path):
        """Valid JSON without repo_to_slot key uses empty mapping."""
        f = tmp_path / "slot-assignments.json"
        f.write_text(json.dumps({"other_key": "value"}))

        sm = SlotManager(assignments_path=f)

        # Should work with empty affinity (data.get("repo_to_slot", {}))
        assert sm.get_available_slots() == list(range(1, 11))


# =============================================================================
# Concurrent Access Tests
# =============================================================================


class TestConcurrentAccess:
    """Tests for thread-safety of SlotManager."""

    def test_10_threads_acquire_10_unique_slots(self, tmp_path: Path):
        """10 threads each acquire a unique slot; no duplicates."""
        sm = SlotManager(
            max_slots=10,
            assignments_path=tmp_path / "slot-assignments.json",
        )
        repos = []
        for i in range(10):
            r = tmp_path / f"repo-{i}"
            r.mkdir()
            repos.append(r)

        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [
                pool.submit(sm.acquire_slot, repos[i], f"task-{i}")
                for i in range(10)
            ]
            for f in as_completed(futures):
                results.append(f.result())

        assert len(results) == 10
        assert len(set(results)) == 10  # all unique

    def test_oversubscribed_threads_get_errors(self, tmp_path: Path):
        """8 threads for 5 slots: 5 succeed, 3 get AllSlotsBusyError."""
        sm = SlotManager(
            max_slots=5,
            assignments_path=tmp_path / "slot-assignments.json",
        )
        repos = []
        for i in range(8):
            r = tmp_path / f"repo-{i}"
            r.mkdir()
            repos.append(r)

        successes = []
        errors = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(sm.acquire_slot, repos[i], f"task-{i}")
                for i in range(8)
            ]
            for f in as_completed(futures):
                try:
                    successes.append(f.result())
                except AllSlotsBusyError:
                    errors.append(True)

        assert len(successes) == 5
        assert len(errors) == 3

    def test_mixed_acquire_release_no_corruption(self, tmp_path: Path):
        """Concurrent acquire and release doesn't corrupt state."""
        sm = SlotManager(
            max_slots=5,
            assignments_path=tmp_path / "slot-assignments.json",
        )
        repos = []
        for i in range(5):
            r = tmp_path / f"repo-{i}"
            r.mkdir()
            repos.append(r)

        # Acquire all 5 slots
        slots = []
        for i in range(5):
            slots.append(sm.acquire_slot(repos[i], f"task-{i}"))

        # Concurrently release all, then reacquire
        def release_and_reacquire(idx: int) -> int:
            sm.release_slot(slots[idx])
            return sm.acquire_slot(repos[idx], f"task-new-{idx}")

        results = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(release_and_reacquire, i) for i in range(5)
            ]
            for f in as_completed(futures):
                results.append(f.result())

        # All should succeed and state should be consistent
        active = sm.get_active_tasks()
        assert len(active) == 5
        assert len(sm.get_available_slots()) == 0


# =============================================================================
# AllSlotsBusyError Tests
# =============================================================================


class TestAllSlotsBusyError:
    """Tests for AllSlotsBusyError exception."""

    def test_has_max_slots_and_active_tasks(
        self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path, tmp_path: Path
    ):
        """Error carries max_slots and active_tasks attributes."""
        small_slot_manager.acquire_slot(repo_path, "task-1")
        small_slot_manager.acquire_slot(repo_path_b, "task-2")

        repo_c = tmp_path / "repo-gamma"
        repo_c.mkdir()
        with pytest.raises(AllSlotsBusyError) as exc_info:
            small_slot_manager.acquire_slot(repo_c, "task-3")

        assert exc_info.value.max_slots == 2
        assert isinstance(exc_info.value.active_tasks, dict)
        assert len(exc_info.value.active_tasks) == 2

    def test_message_includes_task_ids(
        self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path, tmp_path: Path
    ):
        """Error message includes the IDs of active tasks."""
        small_slot_manager.acquire_slot(repo_path, "task-1")
        small_slot_manager.acquire_slot(repo_path_b, "task-2")

        repo_c = tmp_path / "repo-gamma"
        repo_c.mkdir()
        with pytest.raises(AllSlotsBusyError, match="task-1"):
            small_slot_manager.acquire_slot(repo_c, "task-3")

    def test_active_tasks_is_copy(
        self, small_slot_manager: SlotManager, repo_path: Path, repo_path_b: Path, tmp_path: Path
    ):
        """Mutating error's active_tasks doesn't affect the manager."""
        small_slot_manager.acquire_slot(repo_path, "task-1")
        small_slot_manager.acquire_slot(repo_path_b, "task-2")

        repo_c = tmp_path / "repo-gamma"
        repo_c.mkdir()
        with pytest.raises(AllSlotsBusyError) as exc_info:
            small_slot_manager.acquire_slot(repo_c, "task-3")

        exc_info.value.active_tasks.clear()

        # Manager state should be unaffected
        assert len(small_slot_manager.get_active_tasks()) == 2
