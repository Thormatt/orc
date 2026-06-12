"""`orc ingest <path-or-url>` command."""

from __future__ import annotations

import click
from rich.console import Console

from orc.errors import IngestError, WorkspaceNotFoundError
from orc.ingest.pipeline import ingest as do_ingest
from orc.storage import workspace as ws_module

console = Console()


@click.command("ingest")
@click.argument("source")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--no-recursive", is_flag=True, help="Skip recursing into subdirectories")
def ingest_command(source: str, workspace: str | None, no_recursive: bool) -> None:
    """Ingest a file, directory, or URL into the workspace's evidence corpus."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ids = do_ingest(ws, source, recursive=not no_recursive)
    except IngestError as exc:
        raise click.ClickException(str(exc)) from exc

    if not ids:
        console.print(
            "[yellow]No new evidence ingested[/yellow] (already in corpus or unsupported types)"
        )
        return
    console.print(
        f"[green]Ingested[/green] {len(ids)} evidence item(s) into [bold]{ws.name}[/bold]"
    )
    if ws.has_embeddings:
        console.print(f"  embeddings: {ws.embedding_model}")
    for eid in ids[:10]:
        console.print(f"  [dim]{eid}[/dim]")
    if len(ids) > 10:
        console.print(f"  [dim]… and {len(ids) - 10} more[/dim]")
