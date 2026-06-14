"""`orc trace ...` commands."""

from __future__ import annotations

import json as json_lib

import click
from rich.console import Console
from rich.table import Table

from orc.cli_commands._shared import resolve_workspace
from orc.errors import TraceNotFoundError
from orc.storage.trace_store import list_runs, load_trace

console = Console()


@click.group("trace")
def trace_group() -> None:
    """Inspect Orc traces."""


@trace_group.command("show")
@click.argument("run_id")
def show_command(run_id: str) -> None:
    """Print the full trace JSON for a run_id."""
    try:
        trace = load_trace(run_id)
    except TraceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json_lib.dumps(trace, indent=2, default=str))


@trace_group.command("list")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--skill", default=None, help="Filter by skill name")
@click.option("--limit", type=int, default=20, help="Max rows to show")
def list_command(workspace: str | None, skill: str | None, limit: int) -> None:
    """List recent runs in a workspace."""
    ws = resolve_workspace(workspace)
    rows = list_runs(ws.name, skill=skill, limit=limit)
    if not rows:
        console.print("[dim]No runs yet.[/dim]")
        return
    table = Table(title=f"Recent runs in {ws.name}")
    table.add_column("run_id", style="bold")
    table.add_column("skill")
    table.add_column("started")
    table.add_column("status")
    table.add_column("model")
    table.add_column("tokens (in/out)", justify="right")
    for r in rows:
        table.add_row(
            r["run_id"],
            r["skill"],
            r["started_at"],
            r["status"],
            r.get("model") or "[dim]-[/dim]",
            f"{r['total_input_tokens']}/{r['total_output_tokens']}",
        )
    console.print(table)
