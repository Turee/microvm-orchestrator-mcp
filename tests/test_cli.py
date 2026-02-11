"""Tests for CLI commands (cli.py)."""

from __future__ import annotations

import random
import string
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from microvm_orchestrator.cli import _extract_token, cli
from microvm_orchestrator.core.registry import RepoNotGitError, UnknownRepoError


def _fake_token(
    prefix: str = "oat01",
    length: int = 60,
    *,
    seed: int = 0,
    underscores: bool = True,
    dashes: bool = True,
) -> str:
    """Generate a deterministic fake sk-ant- token for testing.

    Produces tokens with realistic structure (mixed case, digits,
    and optionally underscores/dashes) without embedding anything
    that could be mistaken for a real credential.
    """
    rng = random.Random(seed)
    alphabet = string.ascii_letters + string.digits
    if underscores:
        alphabet += "_"
    if dashes:
        alphabet += "-"
    body = "".join(rng.choice(alphabet) for _ in range(length))
    return f"sk-ant-{prefix}-{body}"


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


# =============================================================================
# _extract_token Unit Tests
# =============================================================================


class TestExtractToken:
    """Unit tests for _extract_token regex parsing."""

    # -- Basic token formats --------------------------------------------------

    def test_simple_token(self):
        """Plain sk-ant- token on a single line."""
        token = _fake_token(length=20, seed=1, underscores=False, dashes=False)
        assert _extract_token(token + "\n") == token

    def test_token_with_underscores(self):
        """Tokens containing underscores are captured fully."""
        token = _fake_token("api03", length=20, seed=2, dashes=False)
        assert _extract_token(token + "\n") == token

    def test_token_with_dashes(self):
        """Tokens containing internal dashes are captured fully."""
        token = _fake_token(length=20, seed=3, underscores=False)
        assert _extract_token(token + "\n") == token

    def test_token_with_mixed_underscores_and_dashes(self):
        """Tokens with both underscores and dashes."""
        token = _fake_token(length=30, seed=4)
        assert _extract_token(token + "\n") == token

    def test_long_oauth_token(self):
        """Full-length OAuth token with underscores/dashes."""
        token = _fake_token(length=100, seed=5)
        assert _extract_token(token + "\n") == token

    def test_api_key_format(self):
        """API key format (sk-ant-api03-...)."""
        token = _fake_token("api03", length=40, seed=6)
        assert _extract_token(token + "\n") == token

    # -- Noisy output ---------------------------------------------------------

    def test_token_surrounded_by_text(self):
        """Token embedded in a line with surrounding text."""
        token = _fake_token(length=20, seed=7)
        output = f"Your token is: {token} (save it)\n"
        assert _extract_token(output) == token

    def test_noisy_output_with_banner(self):
        """Token extracted from output with banners and decorations."""
        token = _fake_token(length=30, seed=8)
        output = (
            "Welcome to Claude Code v2.1.39\n"
            "===========================\n"
            "Your OAuth token (valid for 1 year):\n"
            f"{token}\n"
            "Store this token securely.\n"
        )
        assert _extract_token(output) == token

    def test_noisy_output_does_not_absorb_trailing_text(self):
        """Trailing English text must NOT be included in the token."""
        token = _fake_token(length=30, seed=9)
        output = (
            "Your token:\n"
            f"{token}\n"
            "Store this token securely.\n"
        )
        result = _extract_token(output)
        assert result == token
        assert "Store" not in result

    def test_token_on_line_with_prefix_text(self):
        """Token preceded by text on the same line."""
        token = _fake_token("api03", length=20, seed=10)
        output = f"Token: {token}\n"
        assert _extract_token(output) == token

    def test_real_claude_output_format(self):
        """Output format matching real 'claude setup-token' structure."""
        part1 = _fake_token(length=80, seed=11)
        part2 = _fake_token(length=12, seed=12).removeprefix("sk-ant-oat01-")
        output = (
            "\u2713 Long-lived authentication token created successfully!\n"
            "\n"
            "Your OAuth token (valid for 1 year):\n"
            "\n"
            f"{part1}\n"
            f"{part2}\n"
            "\n"
            "Store this token securely. You won't be able to see it again.\n"
            "\n"
            "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
        )
        result = _extract_token(output)
        assert result == part1 + part2

    # -- Line-wrapped tokens --------------------------------------------------

    def test_line_wrapped_token(self):
        """Token split across two lines is reassembled."""
        full = _fake_token(length=80, seed=13)
        split_at = 50
        line1 = full[:len("sk-ant-oat01-") + split_at]
        line2 = full[len("sk-ant-oat01-") + split_at:]
        output = f"{line1}\n{line2}\n"
        result = _extract_token(output)
        assert result == full
        assert "\n" not in result
        assert " " not in result

    def test_line_wrapped_token_with_surrounding_text(self):
        """Line-wrapped token inside noisy output."""
        full = _fake_token(length=80, seed=14)
        split_at = 50
        line1 = full[:len("sk-ant-oat01-") + split_at]
        line2 = full[len("sk-ant-oat01-") + split_at:]
        output = (
            "Welcome to Claude Code v2.1.39\n"
            "some ASCII art here\n"
            "Your OAuth token (valid for 1 year):\n"
            "\n"
            "\n"
            f"{line1}\n"
            f"{line2}\n"
            "Store this token securely.\n"
        )
        result = _extract_token(output)
        assert result == full

    def test_line_wrapped_three_lines(self):
        """Token split across three lines."""
        full = _fake_token(length=60, seed=15)
        prefix_len = len("sk-ant-oat01-")
        p1 = full[:prefix_len + 20]
        p2 = full[prefix_len + 20:prefix_len + 40]
        p3 = full[prefix_len + 40:]
        output = f"{p1}\n{p2}\n{p3}\nDone.\n"
        result = _extract_token(output)
        assert result == full

    def test_blank_lines_between_token_start_and_continuation(self):
        """Blank lines between token lines are skipped."""
        full = _fake_token(length=40, seed=16)
        prefix_len = len("sk-ant-oat01-")
        line1 = full[:prefix_len + 20]
        line2 = full[prefix_len + 20:]
        output = f"Your token:\n\n{line1}\n\n{line2}\nDone.\n"
        result = _extract_token(output)
        assert result == full

    # -- Edge cases -----------------------------------------------------------

    def test_no_token_returns_none(self):
        """Returns None when no sk-ant- token is present."""
        assert _extract_token("No token here\n") is None

    def test_empty_string_returns_none(self):
        """Returns None for empty input."""
        assert _extract_token("") is None

    def test_only_whitespace_returns_none(self):
        """Returns None for whitespace-only input."""
        assert _extract_token("  \n  \n  \n") is None

    def test_partial_prefix_not_matched(self):
        """sk-ant without trailing dash+chars is not a valid token."""
        assert _extract_token("sk-ant\n") is None

    def test_sk_ant_dash_with_chars(self):
        """Minimal valid token: sk-ant- followed by at least one char."""
        assert _extract_token("sk-ant-x\n") == "sk-ant-x"

    def test_only_first_token_returned(self):
        """If multiple tokens appear, only the first is returned."""
        t1 = _fake_token(length=10, seed=17)
        t2 = _fake_token(length=10, seed=18)
        output = f"{t1}\n{t2}\n"
        assert _extract_token(output) == t1

    def test_continuation_stops_at_sentence(self):
        """Continuation line with spaces/punctuation is not absorbed."""
        token = _fake_token(length=20, seed=19)
        output = f"{token}\nPlease save this token.\n"
        assert _extract_token(output) == token

    def test_continuation_stops_at_mixed_content(self):
        """Line starting with token chars but containing spaces is not absorbed."""
        token = _fake_token(length=20, seed=20)
        output = f"{token}\nDone with setup\n"
        assert _extract_token(output) == token

    def test_token_ending_mid_line(self):
        """Token that ends mid-line does not collect continuations."""
        token = _fake_token(length=20, seed=21)
        output = f"Token: {token} is your key\nDEADBEEF\n"
        assert _extract_token(output) == token

    def test_windows_line_endings(self):
        """Handles \\r\\n line endings."""
        token = _fake_token(length=20, seed=22)
        output = f"Your token:\r\n{token}\r\nDone.\r\n"
        assert _extract_token(output) == token

    def test_token_with_leading_whitespace(self):
        """Leading whitespace on token line is stripped."""
        token = _fake_token(length=20, seed=23)
        output = f"   {token}\n"
        assert _extract_token(output) == token

    def test_token_with_trailing_whitespace(self):
        """Trailing whitespace on token line does not break extraction."""
        token = _fake_token(length=20, seed=24)
        output = f"{token}   \nDone.\n"
        assert _extract_token(output) == token

    # -- ANSI escape codes ----------------------------------------------------

    def test_ansi_color_codes_stripped(self):
        """ANSI color codes around token are stripped before matching."""
        token = _fake_token(length=30, seed=25)
        output = f"\x1b[33m{token}\x1b[0m\n"
        assert _extract_token(output) == token

    def test_ansi_codes_on_line_wrapped_token(self):
        """ANSI codes don't prevent continuation line collection."""
        full = _fake_token(length=80, seed=26)
        split_at = 50
        line1 = full[:len("sk-ant-oat01-") + split_at]
        line2 = full[len("sk-ant-oat01-") + split_at:]
        output = (
            f"\x1b[33m{line1}\x1b[0m\n"
            f"\x1b[33m{line2}\x1b[0m\n"
            "Store this token securely.\n"
        )
        assert _extract_token(output) == full

    def test_ansi_codes_in_real_claude_output(self):
        """Full claude setup-token output with ANSI escape codes throughout."""
        part1 = _fake_token(length=80, seed=27)
        part2 = _fake_token(length=12, seed=28).removeprefix("sk-ant-oat01-")
        output = (
            "\x1b[32m\u2713\x1b[0m Long-lived authentication token created successfully!\n"
            "\n"
            "Your OAuth token (valid for 1 year):\n"
            "\n"
            f"\x1b[33m{part1}\x1b[0m\n"
            f"\x1b[33m{part2}\x1b[0m\n"
            "\n"
            "Store this token securely. You won't be able to see it again.\n"
            "\n"
            "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
        )
        assert _extract_token(output) == part1 + part2


# =============================================================================
# Setup-Token CLI Integration Tests
# =============================================================================


class TestSetupToken:
    """Tests for the 'setup-token' command."""

    def test_setup_token_success(self, cli_runner: CliRunner, tmp_path: Path):
        """Successful run saves token to file with 0o600."""
        token_dir = tmp_path / ".microvm-orchestrator"
        token_file = token_dir / "token"

        token = _fake_token(length=20, seed=100)
        mock_result = MagicMock(returncode=0, stdout=f"{token}\n", stderr="")
        with patch("microvm_orchestrator.cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("microvm_orchestrator.cli.subprocess.run", return_value=mock_result), \
             patch("microvm_orchestrator.cli.Path.home", return_value=tmp_path):
            result = cli_runner.invoke(cli, ["setup-token"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "Token saved" in result.output
        assert token_file.read_text() == f"{token}\n"
        assert token_file.stat().st_mode & 0o777 == 0o600

    def test_setup_token_extracts_from_noisy_output(self, cli_runner: CliRunner, tmp_path: Path):
        """Token is extracted from real claude setup-token output format."""
        token_dir = tmp_path / ".microvm-orchestrator"
        token_file = token_dir / "token"

        # Simulate claude setup-token output with checkmark, line wrapping, and instructions
        part1 = _fake_token(length=80, seed=101)
        part2 = _fake_token(length=12, seed=102).removeprefix("sk-ant-oat01-")
        noisy_output = (
            "\u2713 Long-lived authentication token created successfully!\n"
            "\n"
            "Your OAuth token (valid for 1 year):\n"
            "\n"
            f"{part1}\n"
            f"{part2}\n"
            "\n"
            "Store this token securely. You won't be able to see it again.\n"
            "\n"
            "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
        )
        mock_result = MagicMock(returncode=0, stdout=noisy_output, stderr="")
        with patch("microvm_orchestrator.cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("microvm_orchestrator.cli.subprocess.run", return_value=mock_result), \
             patch("microvm_orchestrator.cli.Path.home", return_value=tmp_path):
            result = cli_runner.invoke(cli, ["setup-token"], catch_exceptions=False)

        assert result.exit_code == 0
        saved = token_file.read_text().strip()
        assert saved == part1 + part2

    def test_setup_token_with_underscored_token(self, cli_runner: CliRunner, tmp_path: Path):
        """Token with underscores is saved correctly."""
        token_dir = tmp_path / ".microvm-orchestrator"
        token_file = token_dir / "token"

        token = _fake_token("api03", length=30, seed=103, dashes=False)
        mock_result = MagicMock(returncode=0, stdout=f"{token}\n", stderr="")
        with patch("microvm_orchestrator.cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("microvm_orchestrator.cli.subprocess.run", return_value=mock_result), \
             patch("microvm_orchestrator.cli.Path.home", return_value=tmp_path):
            result = cli_runner.invoke(cli, ["setup-token"], catch_exceptions=False)

        assert result.exit_code == 0
        assert token_file.read_text().strip() == token

    def test_setup_token_claude_not_found(self, cli_runner: CliRunner):
        """Error when claude CLI not on PATH."""
        with patch("microvm_orchestrator.cli.shutil.which", return_value=None):
            result = cli_runner.invoke(cli, ["setup-token"])

        assert result.exit_code == 1
        assert "'claude' CLI not found" in result.output

    def test_setup_token_claude_fails(self, cli_runner: CliRunner):
        """Error when claude setup-token exits non-zero."""
        mock_result = MagicMock(returncode=1, stdout="", stderr="auth failed")
        with patch("microvm_orchestrator.cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("microvm_orchestrator.cli.subprocess.run", return_value=mock_result):
            result = cli_runner.invoke(cli, ["setup-token"])

        assert result.exit_code == 1
        assert "failed" in result.output

    def test_setup_token_no_token_in_output(self, cli_runner: CliRunner):
        """Error when output contains no recognizable token."""
        mock_result = MagicMock(returncode=0, stdout="Some output with no token\n", stderr="")
        with patch("microvm_orchestrator.cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("microvm_orchestrator.cli.subprocess.run", return_value=mock_result):
            result = cli_runner.invoke(cli, ["setup-token"])

        assert result.exit_code == 1
        assert "Could not find a token" in result.output
