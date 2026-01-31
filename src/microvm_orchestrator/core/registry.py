"""Repository registry for allowed repos allowlist."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class UnknownRepoError(Exception):
    """Raised when a repo alias is not in the registry."""

    def __init__(self, alias: str):
        self.alias = alias
        super().__init__(f"Repo '{alias}' not registered. Run: microvm-orchestrator allow")


class RepoNotGitError(Exception):
    """Raised when a path is not a git repository."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"Not a git repository: {path}")


class AliasCollisionError(Exception):
    """Raised when an alias already exists with a different path."""

    def __init__(self, alias: str, existing_path: Path, new_path: Path):
        self.alias = alias
        self.existing_path = existing_path
        self.new_path = new_path
        super().__init__(
            f"Alias '{alias}' already exists for {existing_path}. "
            f"Use --alias to specify a different alias."
        )


def _default_registry_path() -> Path:
    """Get the default registry path."""
    return Path.home() / ".microvm-orchestrator" / "allowed-repos.json"


@dataclass
class RepoRegistry:
    """
    Manages the allowlist of repositories that can be used with microvm tasks.

    The registry persists to ~/.microvm-orchestrator/allowed-repos.json.
    Repos are identified by alias (e.g., 'myproject') which maps to absolute paths.
    """

    registry_path: Path = field(default_factory=_default_registry_path)
    _repos: dict[str, dict] = field(default_factory=dict, repr=False)
    _loaded: bool = field(default=False, repr=False)

    def __post_init__(self):
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        if self._loaded:
            return

        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text())
                self._repos = data
                logger.debug("Loaded %d repos from registry", len(self._repos))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load registry: %s", e)
                self._repos = {}
        else:
            self._repos = {}

        self._loaded = True

    def _persist(self) -> None:
        """Save registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(self._repos, indent=2))
        logger.debug("Persisted %d repos to registry", len(self._repos))

    def allow(self, path: Path, alias: Optional[str] = None) -> str:
        """
        Register a repo in the allowlist.

        Args:
            path: Path to the git repository
            alias: Optional custom alias. Defaults to directory name.

        Returns:
            The alias used for registration.

        Raises:
            RepoNotGitError: If path is not a git repository.
            AliasCollisionError: If alias exists with a different path.
        """
        path = path.resolve()

        if not (path / ".git").exists():
            raise RepoNotGitError(path)

        # Default alias is the directory name
        if alias is None:
            alias = path.name

        # Check for collision
        if alias in self._repos:
            existing_path = Path(self._repos[alias]["path"])
            if existing_path != path:
                # Try to generate a unique alias
                base_alias = alias
                counter = 2
                while f"{base_alias}-{counter}" in self._repos:
                    existing = Path(self._repos[f"{base_alias}-{counter}"]["path"])
                    if existing == path:
                        # Already registered with this numbered alias
                        return f"{base_alias}-{counter}"
                    counter += 1
                alias = f"{base_alias}-{counter}"
            else:
                # Same path, just update the timestamp
                self._repos[alias]["added"] = datetime.now(timezone.utc).isoformat()
                self._persist()
                return alias

        self._repos[alias] = {
            "path": str(path),
            "added": datetime.now(timezone.utc).isoformat(),
        }
        self._persist()
        logger.info("Registered repo '%s' at %s", alias, path)
        return alias

    def resolve(self, alias: str) -> Path:
        """
        Resolve an alias to its path.

        Args:
            alias: The repo alias.

        Returns:
            The absolute path to the repository.

        Raises:
            UnknownRepoError: If alias is not registered.
        """
        if alias not in self._repos:
            raise UnknownRepoError(alias)
        return Path(self._repos[alias]["path"])

    def list(self) -> dict[str, dict]:
        """
        List all registered repos.

        Returns:
            Dictionary mapping aliases to repo info (path, added timestamp).
        """
        return dict(self._repos)

    def remove(self, alias: str) -> None:
        """
        Remove a repo from the allowlist.

        Args:
            alias: The repo alias to remove.

        Raises:
            UnknownRepoError: If alias is not registered.
        """
        if alias not in self._repos:
            raise UnknownRepoError(alias)

        del self._repos[alias]
        self._persist()
        logger.info("Removed repo '%s' from registry", alias)
