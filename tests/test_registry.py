"""Tests for RepoRegistry (core/registry.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from microvm_orchestrator.core.registry import (
    RepoRegistry,
    RepoNotGitError,
    UnknownRepoError,
)

from .fixtures.mocks import create_git_repo


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def registry(tmp_path: Path) -> RepoRegistry:
    """RepoRegistry with isolated registry file."""
    return RepoRegistry(registry_path=tmp_path / "allowed-repos.json")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A temporary git repository."""
    repo = tmp_path / "my-project"
    create_git_repo(repo)
    return repo


@pytest.fixture
def git_repo_b(tmp_path: Path) -> Path:
    """A second temporary git repository for collision tests."""
    repo = tmp_path / "my-project-b"
    create_git_repo(repo)
    return repo


# =============================================================================
# Allow Tests
# =============================================================================


class TestAllow:
    """Tests for RepoRegistry.allow()."""

    def test_allow_default_alias(self, registry: RepoRegistry, git_repo: Path):
        """Directory name is used as alias when none specified."""
        alias = registry.allow(git_repo)

        assert alias == "my-project"

    def test_allow_custom_alias(self, registry: RepoRegistry, git_repo: Path):
        """Explicit alias is used when provided."""
        alias = registry.allow(git_repo, alias="custom")

        assert alias == "custom"

    def test_allow_returns_alias(self, registry: RepoRegistry, git_repo: Path):
        """Return value is the alias string."""
        result = registry.allow(git_repo, alias="myalias")

        assert isinstance(result, str)
        assert result == "myalias"

    def test_allow_rejects_non_git(self, registry: RepoRegistry, tmp_path: Path):
        """RepoNotGitError raised for path without .git directory."""
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()

        with pytest.raises(RepoNotGitError):
            registry.allow(non_git)

    def test_allow_resolves_path(self, registry: RepoRegistry, git_repo: Path):
        """Path stored as resolved absolute path."""
        registry.allow(git_repo)
        stored = registry.resolve("my-project")

        assert stored.is_absolute()
        assert stored == git_repo.resolve()

    def test_allow_same_path_updates_timestamp(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """Re-allowing same path/alias updates the timestamp."""
        registry.allow(git_repo)
        first_added = registry.list()["my-project"]["added"]

        registry.allow(git_repo)
        second_added = registry.list()["my-project"]["added"]

        assert second_added >= first_added

    def test_allow_collision_generates_suffix(
        self, registry: RepoRegistry, git_repo: Path, tmp_path: Path
    ):
        """Different path with same dir name gets alias-2 suffix."""
        registry.allow(git_repo)

        # Create another repo with the same directory name
        other_parent = tmp_path / "other"
        other_repo = other_parent / "my-project"
        create_git_repo(other_repo)

        alias = registry.allow(other_repo)

        assert alias == "my-project-2"

    def test_allow_collision_increments(
        self, registry: RepoRegistry, git_repo: Path, tmp_path: Path
    ):
        """Third collision gets alias-3."""
        registry.allow(git_repo)

        # Second repo with same name
        second = tmp_path / "dir2" / "my-project"
        create_git_repo(second)
        registry.allow(second)

        # Third repo with same name
        third = tmp_path / "dir3" / "my-project"
        create_git_repo(third)
        alias = registry.allow(third)

        assert alias == "my-project-3"

    def test_allow_collision_finds_existing(
        self, registry: RepoRegistry, git_repo: Path, tmp_path: Path
    ):
        """If numbered alias already has same path, returns it."""
        registry.allow(git_repo)

        # Create a different repo that takes alias-2
        other = tmp_path / "other" / "my-project"
        create_git_repo(other)
        alias2 = registry.allow(other)
        assert alias2 == "my-project-2"

        # Re-allowing the other repo should find existing alias-2
        alias_again = registry.allow(other)
        assert alias_again == "my-project-2"

    def test_allow_persists_to_disk(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """JSON file is written after allow()."""
        registry.allow(git_repo)

        data = json.loads(registry.registry_path.read_text())
        assert "my-project" in data
        assert data["my-project"]["path"] == str(git_repo.resolve())


# =============================================================================
# Resolve Tests
# =============================================================================


class TestResolve:
    """Tests for RepoRegistry.resolve()."""

    def test_resolve_returns_path(self, registry: RepoRegistry, git_repo: Path):
        """Returns Path matching what was registered."""
        registry.allow(git_repo)
        result = registry.resolve("my-project")

        assert isinstance(result, Path)
        assert result == git_repo.resolve()

    def test_resolve_unknown_raises(self, registry: RepoRegistry):
        """UnknownRepoError raised for missing alias."""
        with pytest.raises(UnknownRepoError):
            registry.resolve("nonexistent")


# =============================================================================
# List Tests
# =============================================================================


class TestList:
    """Tests for RepoRegistry.list()."""

    def test_list_empty(self, registry: RepoRegistry):
        """Empty registry returns empty dict."""
        result = registry.list()

        assert result == {}

    def test_list_multiple_repos(
        self, registry: RepoRegistry, git_repo: Path, git_repo_b: Path
    ):
        """Returns all registered repos with path and added keys."""
        registry.allow(git_repo)
        registry.allow(git_repo_b)

        result = registry.list()

        assert len(result) == 2
        assert "my-project" in result
        assert "my-project-b" in result
        for info in result.values():
            assert "path" in info
            assert "added" in info


# =============================================================================
# Remove Tests
# =============================================================================


class TestRemove:
    """Tests for RepoRegistry.remove()."""

    def test_remove_deletes_repo(self, registry: RepoRegistry, git_repo: Path):
        """Alias is gone after remove()."""
        registry.allow(git_repo)
        registry.remove("my-project")

        with pytest.raises(UnknownRepoError):
            registry.resolve("my-project")

    def test_remove_unknown_raises(self, registry: RepoRegistry):
        """UnknownRepoError raised for missing alias."""
        with pytest.raises(UnknownRepoError):
            registry.remove("nonexistent")

    def test_remove_persists_to_disk(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """JSON file is updated after remove()."""
        registry.allow(git_repo)
        registry.remove("my-project")

        data = json.loads(registry.registry_path.read_text())
        assert "my-project" not in data

    def test_remove_then_reallow(self, registry: RepoRegistry, git_repo: Path):
        """A removed repo can be re-allowed with the same alias."""
        registry.allow(git_repo)
        registry.remove("my-project")
        alias = registry.allow(git_repo)

        assert alias == "my-project"
        assert registry.resolve("my-project") == git_repo.resolve()


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases like deleted repos and stale entries."""

    def test_resolve_returns_path_even_if_deleted(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """resolve() returns the stored path even if the repo was deleted from disk."""
        registry.allow(git_repo)
        import shutil
        shutil.rmtree(git_repo)

        # resolve() is a lookup — it doesn't verify the path still exists
        result = registry.resolve("my-project")
        assert result == git_repo.resolve()
        assert not result.exists()

    def test_list_includes_stale_entries(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """list() includes repos whose paths no longer exist on disk."""
        registry.allow(git_repo)
        import shutil
        shutil.rmtree(git_repo)

        result = registry.list()
        assert "my-project" in result

    def test_allow_rejects_deleted_repo(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """allow() rejects a path whose .git was removed after initial allow."""
        registry.allow(git_repo)
        import shutil
        shutil.rmtree(git_repo / ".git")

        with pytest.raises(RepoNotGitError):
            registry.allow(git_repo)

    def test_allow_nonexistent_path(self, registry: RepoRegistry, tmp_path: Path):
        """allow() rejects a path that doesn't exist at all."""
        missing = tmp_path / "does-not-exist"

        with pytest.raises(RepoNotGitError):
            registry.allow(missing)

    def test_remove_does_not_affect_other_repos(
        self, registry: RepoRegistry, git_repo: Path, git_repo_b: Path
    ):
        """Removing one repo leaves other repos intact."""
        registry.allow(git_repo)
        registry.allow(git_repo_b)
        registry.remove("my-project")

        assert "my-project-b" in registry.list()
        assert registry.resolve("my-project-b") == git_repo_b.resolve()

    def test_collision_after_remove_reuses_base_alias(
        self, registry: RepoRegistry, git_repo: Path, tmp_path: Path
    ):
        """After removing base alias, a new repo with same name gets the base alias."""
        registry.allow(git_repo)
        registry.remove("my-project")

        other = tmp_path / "other" / "my-project"
        create_git_repo(other)
        alias = registry.allow(other)

        assert alias == "my-project"


# =============================================================================
# Persistence Tests
# =============================================================================


class TestPersistence:
    """Tests for registry persistence across instances."""

    def test_load_from_existing_file(
        self, registry: RepoRegistry, git_repo: Path
    ):
        """New RepoRegistry instance loads prior data."""
        registry.allow(git_repo)

        # Create a new instance pointing to the same file
        registry2 = RepoRegistry(registry_path=registry.registry_path)

        result = registry2.resolve("my-project")
        assert result == git_repo.resolve()

    def test_load_handles_missing_file(self, tmp_path: Path):
        """No file means empty registry, no error."""
        registry = RepoRegistry(registry_path=tmp_path / "nonexistent.json")

        assert registry.list() == {}

    def test_load_handles_corrupt_json(self, tmp_path: Path):
        """Bad JSON means empty registry, no crash."""
        bad_file = tmp_path / "allowed-repos.json"
        bad_file.write_text("{not valid json!!")

        registry = RepoRegistry(registry_path=bad_file)

        assert registry.list() == {}
