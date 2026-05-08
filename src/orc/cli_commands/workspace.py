"""`orc workspace ...` commands."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from orc.errors import WorkspaceExistsError
from orc.storage import workspace as ws_module

console = Console()


@click.group("workspace")
def workspace() -> None:
    """Manage Orc workspaces."""


@workspace.command("create")
@click.argument("name")
def create_command(name: str) -> None:
    """Create a new workspace."""
    try:
        ws = ws_module.create(name)
    except WorkspaceExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"[green]Created workspace[/green] [bold]{ws.name}[/bold]")
    console.print(f"  schema_version = {ws.schema_version}")
    console.print(f"  created_at     = {ws.created_at}")


@workspace.command("list")
def list_command() -> None:
    """List all workspaces."""
    items = ws_module.list_all()
    if not items:
        console.print("[dim]No workspaces yet. Try: orc workspace create default[/dim]")
        return
    table = Table(title="Workspaces")
    table.add_column("Name", style="bold")
    table.add_column("Created")
    table.add_column("Embeddings")
    table.add_column("Corpus version", justify="right")
    for w in items:
        table.add_row(
            w.name,
            w.created_at,
            w.embedding_model or "[dim]none[/dim]",
            str(w.corpus_version),
        )
    console.print(table)
