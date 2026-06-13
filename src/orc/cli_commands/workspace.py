"""`orc workspace ...` commands.

The embedding model is pinned in the workspace row — there is deliberately no
env var to override it at retrieval time, because the column is the
replay-pinned truth for which model embedded the corpus.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from orc.cli_commands._shared import resolve_workspace
from orc.errors import EmbeddingsUnavailableError, WorkspaceExistsError
from orc.paths import workspace_db_path
from orc.retrieval.embedder import DEFAULT_EMBEDDING_MODEL, embedder_available, get_embedder
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection, transaction
from orc.storage.embeddings_store import (
    backfill_embeddings,
    ensure_chunk_vec,
    load_vec_extension,
    vec_extension_available,
)

console = Console()

_INSTALL_HINT = 'pip install "orc-ai[embeddings]"'


@click.group("workspace")
def workspace() -> None:
    """Manage Orc workspaces."""


@workspace.command("create")
@click.argument("name")
@click.option(
    "--embeddings",
    "embeddings",
    is_flag=True,
    help="Enable hybrid (BM25 + vector) retrieval for this workspace.",
)
@click.option(
    "--embedding-model",
    "embedding_model",
    default=None,
    help=f"Embedding model id (default: {DEFAULT_EMBEDDING_MODEL}). Requires --embeddings.",
)
def create_command(name: str, embeddings: bool, embedding_model: str | None) -> None:
    """Create a new workspace."""
    if embedding_model is not None and not embeddings:
        raise click.UsageError("--embedding-model requires --embeddings")
    model = (embedding_model or DEFAULT_EMBEDDING_MODEL) if embeddings else None

    # Warn-but-create: the flag records intent in the workspace row; the user
    # can install the extra and run `orc workspace embed` later.
    if model is not None and not (embedder_available() and vec_extension_available()):
        console.print(
            "[yellow]Warning:[/yellow] embedding dependencies are not installed; "
            f"ingest will fail until you run: {escape(_INSTALL_HINT)}"
        )

    try:
        ws = ws_module.create(name, embedding_model=model)
    except WorkspaceExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"[green]Created workspace[/green] [bold]{ws.name}[/bold]")
    console.print(f"  schema_version = {ws.schema_version}")
    console.print(f"  created_at     = {ws.created_at}")
    if ws.has_embeddings:
        console.print(f"  embeddings     = {ws.embedding_model}")


@workspace.command("embed")
@click.argument("name")
@click.option(
    "--model",
    default=None,
    help="Embedding model id. Only needed when the workspace has none set yet.",
)
def embed_command(name: str, model: str | None) -> None:
    """Backfill vector embeddings for all unembedded chunks in a workspace."""
    ws = resolve_workspace(name)

    if ws.embedding_model is None:
        effective_model = model or DEFAULT_EMBEDDING_MODEL
    elif model is not None and model != ws.embedding_model:
        raise click.ClickException(
            f"Workspace {ws.name!r} is pinned to embedding model "
            f"{ws.embedding_model!r}; refusing to embed with {model!r}. "
            "Vectors from different models cannot be mixed."
        )
    else:
        effective_model = ws.embedding_model

    if not vec_extension_available():
        raise click.ClickException(
            f"The sqlite-vec extension is unavailable; run: {_INSTALL_HINT}"
        )
    try:
        embedder = get_embedder(effective_model)
    except EmbeddingsUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc

    with open_connection(workspace_db_path(ws.name)) as conn:
        load_vec_extension(conn)
        try:
            ensure_chunk_vec(conn, embedder.dim)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if ws.embedding_model is None:
            with transaction(conn):
                conn.execute(
                    "UPDATE workspace SET embedding_model = ? WHERE name = ?",
                    (effective_model, ws.name),
                )
        count = backfill_embeddings(conn, embedder)
    console.print(f"[green]Embedded[/green] {count} chunk(s) with [bold]{effective_model}[/bold]")


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
