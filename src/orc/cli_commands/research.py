"""`orc research "<topic>"` — synthesize a corpus-grounded answer with citations."""

from __future__ import annotations

import json as json_lib

import click
from rich.console import Console

from orc import directives
from orc.cli_commands._shared import resolve_workspace
from orc.runs import open_run

console = Console()


@click.command("research")
@click.argument("topic")
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
@click.option("--model", default=None, help="Override the research model")
@click.option("--k", type=int, default=None, help="Number of chunks to retrieve")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON")
def research_command(
    topic: str,
    workspace: str | None,
    model: str | None,
    k: int | None,
    as_json: bool,
) -> None:
    """Research a topic against the workspace's evidence corpus."""
    ws = resolve_workspace(workspace)

    spec = directives.get("research")
    skill = spec.skills["research_topic"]
    kwargs = {**spec.kwargs_for("research_topic"), "topic": topic}
    if model is not None:
        kwargs["model"] = model
    if k is not None:
        kwargs["k"] = k

    with open_run(ws, directive="research", skill="research_topic", inputs=dict(kwargs)) as run:
        run.record_effective_kwargs(kwargs)
        result = skill.run(workspace=ws, run=run, **kwargs)
        run.close(output=result)

    if as_json:
        click.echo(json_lib.dumps(result, indent=2, default=str))
        return

    console.print(f"\n[bold]Research: {topic}[/bold]")
    console.print(f"\n{result['summary']}")

    if result.get("key_points"):
        console.print("\n[bold]Key points:[/bold]")
        for kp in result["key_points"]:
            cites = ", ".join(kp["supporting_chunk_ids"][:3])
            console.print(f"  - {kp['point']}  [dim](chunks: {cites})[/dim]")

    if result.get("gaps"):
        console.print(f"\n[bold]Gaps:[/bold] {result['gaps']}")

    console.print(f"\n[dim]model={result['model']}  run_id={run.run_id}[/dim]")
