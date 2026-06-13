"""`orc search <query>` — surfaces the `search_evidence` skill for direct CLI use."""

from __future__ import annotations

import json as json_lib

import click
from rich.console import Console
from rich.table import Table

from orc import directives
from orc.cli_commands._shared import resolve_workspace
from orc.runs import open_run

console = Console()


@click.command("search")
@click.argument("query")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--k", type=int, default=10, help="Max chunks returned")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of a table")
def search_command(query: str, workspace: str | None, k: int, as_json: bool) -> None:
    """Retrieve top chunks for a query (no LLM)."""
    ws = resolve_workspace(workspace)

    spec = directives.get("research")
    skill = spec.skills["search_evidence"]
    skill_kwargs = {**spec.kwargs_for("search_evidence"), "query": query, "k": k}
    with open_run(
        ws,
        directive="research",
        skill="search_evidence",
        inputs={"query": query, "k": k},
    ) as run:
        run.record_effective_kwargs(skill_kwargs)
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)

    if as_json:
        click.echo(json_lib.dumps(result, indent=2))
        return

    chunks = result["chunks"]
    if not chunks:
        console.print("[yellow]No chunks matched[/yellow]")
        return

    table = Table(title=f"Retrieval results for '{query}'")
    table.add_column("Rank", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Title")
    table.add_column("Heading")
    table.add_column("Preview")
    for c in chunks:
        preview = c["text"].replace("\n", " ")[:80]
        table.add_row(
            str(c["rank"]),
            f"{c['bm25_score']:.2f}",
            c["evidence_title"] or "[dim]?[/dim]",
            c["headings_path"] or "[dim]?[/dim]",
            preview,
        )
    console.print(table)
