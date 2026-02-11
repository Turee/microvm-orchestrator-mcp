"""CLI for microvm-orchestrator."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import click

from .core.registry import RepoNotGitError, RepoRegistry, UnknownRepoError

# Token pattern: sk-ant- followed by base64url chars and hyphens.
_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

# A continuation line consists entirely of base64url token characters.
_TOKEN_CONTINUATION_RE = re.compile(r"^[A-Za-z0-9_+/=-]+$")


def _extract_token(output: str) -> str | None:
    """Extract an Anthropic API token from noisy CLI output.

    Handles tokens that may be line-wrapped across multiple lines.
    Tokens start with ``sk-ant-`` and contain ``[A-Za-z0-9_-]``.
    """
    lines = output.splitlines()
    token_parts: list[str] = []
    collecting = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Blank lines: skip while collecting, otherwise ignore
            continue

        if not collecting:
            match = _TOKEN_RE.search(stripped)
            if match:
                token_parts.append(match.group(0))
                # If the match extends to end-of-line, the token may
                # continue on the next line.
                if match.end() == len(stripped):
                    collecting = True
                else:
                    break
        else:
            # Accept continuation lines that are purely token characters,
            # but stop if the line starts a new token.
            if _TOKEN_CONTINUATION_RE.match(stripped) and not stripped.startswith("sk-ant-"):
                token_parts.append(stripped)
            else:
                break

    return "".join(token_parts) if token_parts else None


@click.group()
def cli():
    """Microvm orchestrator - run tasks in isolated microVMs."""
    pass


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--alias", "-a", help="Custom alias for the repo")
def allow(path: str, alias: str | None):
    """Register a repository for use with microvm tasks."""
    registry = RepoRegistry()
    try:
        used_alias = registry.allow(Path(path), alias)
        click.echo(f"Registered: {used_alias}")
    except RepoNotGitError as e:
        raise click.ClickException(str(e)) from e


@cli.command("list")
def list_repos():
    """List registered repositories."""
    registry = RepoRegistry()
    repos = registry.list()

    if not repos:
        click.echo("No repositories registered.")
        click.echo("Use 'microvm-orchestrator allow' to register a repo.")
        return

    for alias, info in repos.items():
        click.echo(f"  {alias}: {info['path']}")


@cli.command()
@click.argument("alias")
def remove(alias: str):
    """Remove a repository from the allowlist."""
    registry = RepoRegistry()
    try:
        registry.remove(alias)
        click.echo(f"Removed: {alias}")
    except UnknownRepoError as e:
        raise click.ClickException(str(e)) from e


@cli.command()
def serve():
    """Start the MCP server."""
    from .server import run

    run()


@cli.command("setup-token")
def setup_token():
    """Authenticate with Claude and save the token locally."""
    if not shutil.which("claude"):
        raise click.ClickException(
            "'claude' CLI not found. Install it first: https://docs.anthropic.com/en/docs/claude-code"
        )

    click.echo("Running 'claude setup-token' — follow the prompts to authenticate...")
    result = subprocess.run(
        ["claude", "setup-token"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"'claude setup-token' failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    token = _extract_token(result.stdout)
    if not token:
        raise click.ClickException(
            "Could not find a token in 'claude setup-token' output. "
            "Expected a token starting with 'sk-ant-'."
        )

    token_dir = Path.home() / ".microvm-orchestrator"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_file = token_dir / "token"
    token_file.write_text(token + "\n")
    token_file.chmod(0o600)

    click.echo(f"Token saved to {token_file}")


if __name__ == "__main__":
    cli()
