"""Tests for CLI commands (cli.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from microvm_orchestrator.cli import cli
from microvm_orchestrator.core.registry import RepoNotGitError, UnknownRepoError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cli_runner() -> CliRunner:
    """Click test runner."""
    return CliRunner()


@pytest.fixture
def mock_registry():
    """Patch RepoRegistry in the cli module, returning a configurable mock."""
    mock_instance = MagicMock()
    mock_instance.list.return_value = {}
    with patch("microvm_orchestrator.cli.RepoRegistry", return_value=mock_instance) as mock_cls:
        mock_cls._instance = mock_instance
        yield mock_instance


# =============================================================================
# Allow Tests
# =============================================================================


class TestAllow:
    """Tests for the 'allow' command."""

    def test_allow_default_path_and_alias(
        self, cli_runner: CliRunner, mock_registry: MagicMock, tmp_project: Path
    ):
        """No args defaults to path='.' and alias=None."""
        mock_registry.allow.return_value = "project"
        result = cli_runner.invoke(cli, ["allow"], catch_exceptions=False)

        assert result.exit_code == 0
        mock_registry.allow.assert_called_once()
        # alias should be None (default)
        _, kwargs = mock_registry.allow.call_args
        assert kwargs.get("alias") is None or mock_registry.allow.call_args[0][1:] == ()

    def test_allow_explicit_path(
        self, cli_runner: CliRunner, mock_registry: MagicMock, tmp_project: Path
    ):
        """Explicit path argument is passed through."""
        mock_registry.allow.return_value = "project"
        result = cli_runner.invoke(
            cli, ["allow", str(tmp_project)], catch_exceptions=False
        )

        assert result.exit_code == 0
        call_args = mock_registry.allow.call_args
        assert call_args[0][0] == Path(str(tmp_project))

    def test_allow_custom_alias(
        self, cli_runner: CliRunner, mock_registry: MagicMock, tmp_project: Path
    ):
        """--alias option is forwarded."""
        mock_registry.allow.return_value = "myalias"
        result = cli_runner.invoke(
            cli, ["allow", str(tmp_project), "--alias", "myalias"], catch_exceptions=False
        )

        assert result.exit_code == 0
        call_args = mock_registry.allow.call_args
        assert call_args[0][1] == "myalias"

    def test_allow_short_alias_flag(
        self, cli_runner: CliRunner, mock_registry: MagicMock, tmp_project: Path
    ):
        """-a short flag works for alias."""
        mock_registry.allow.return_value = "myalias"
        result = cli_runner.invoke(
            cli, ["allow", str(tmp_project), "-a", "myalias"], catch_exceptions=False
        )

        assert result.exit_code == 0
        call_args = mock_registry.allow.call_args
        assert call_args[0][1] == "myalias"

    def test_allow_prints_registered_alias(
        self, cli_runner: CliRunner, mock_registry: MagicMock, tmp_project: Path
    ):
        """Output contains 'Registered: <alias>'."""
        mock_registry.allow.return_value = "my-repo"
        result = cli_runner.invoke(
            cli, ["allow", str(tmp_project)], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert "Registered: my-repo" in result.output

    def test_allow_non_git_error(
        self, cli_runner: CliRunner, mock_registry: MagicMock, tmp_path: Path
    ):
        """RepoNotGitError results in exit_code=1 and error message."""
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()
        mock_registry.allow.side_effect = RepoNotGitError(non_git)

        result = cli_runner.invoke(cli, ["allow", str(non_git)])

        assert result.exit_code == 1
        assert "Not a git repository" in result.output

    def test_allow_nonexistent_path(self, cli_runner: CliRunner, mock_registry: MagicMock):
        """click.Path(exists=True) rejects a missing path."""
        result = cli_runner.invoke(cli, ["allow", "/no/such/path"])

        assert result.exit_code == 2
        assert "does not exist" in result.output


# =============================================================================
# List Tests
# =============================================================================


class TestListRepos:
    """Tests for the 'list' command."""

    def test_list_empty(self, cli_runner: CliRunner, mock_registry: MagicMock):
        """No repos prints helpful message."""
        mock_registry.list.return_value = {}
        result = cli_runner.invoke(cli, ["list"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "No repositories registered." in result.output

    def test_list_shows_repos(self, cli_runner: CliRunner, mock_registry: MagicMock):
        """Repos are listed as 'alias: /path' lines."""
        mock_registry.list.return_value = {
            "proj-a": {"path": "/home/user/proj-a"},
            "proj-b": {"path": "/home/user/proj-b"},
        }
        result = cli_runner.invoke(cli, ["list"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "proj-a: /home/user/proj-a" in result.output
        assert "proj-b: /home/user/proj-b" in result.output


# =============================================================================
# Remove Tests
# =============================================================================


class TestRemove:
    """Tests for the 'remove' command."""

    def test_remove_success(self, cli_runner: CliRunner, mock_registry: MagicMock):
        """Successful remove prints 'Removed: <alias>'."""
        result = cli_runner.invoke(cli, ["remove", "myrepo"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "Removed: myrepo" in result.output
        mock_registry.remove.assert_called_once_with("myrepo")

    def test_remove_unknown_error(self, cli_runner: CliRunner, mock_registry: MagicMock):
        """UnknownRepoError results in exit_code=1 and error message."""
        mock_registry.remove.side_effect = UnknownRepoError("ghost")

        result = cli_runner.invoke(cli, ["remove", "ghost"])

        assert result.exit_code == 1
        assert "not registered" in result.output

    def test_remove_missing_argument(self, cli_runner: CliRunner, mock_registry: MagicMock):
        """Missing alias argument gives usage error."""
        result = cli_runner.invoke(cli, ["remove"])

        assert result.exit_code == 2
        assert "Missing argument" in result.output


# =============================================================================
# Serve Tests
# =============================================================================


class TestServe:
    """Tests for the 'serve' command."""

    def test_serve_calls_run(self, cli_runner: CliRunner):
        """serve command calls server.run()."""
        with patch("microvm_orchestrator.server.run") as mock_run:
            result = cli_runner.invoke(cli, ["serve"], catch_exceptions=False)

        assert result.exit_code == 0
        mock_run.assert_called_once()
