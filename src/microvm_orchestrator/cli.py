"""CLI for microvm-orchestrator."""

from __future__ import annotations

from pathlib import Path

import click

from .core.registry import RepoNotGitError, RepoRegistry, UnknownRepoError


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


if __name__ == "__main__":
    cli()
