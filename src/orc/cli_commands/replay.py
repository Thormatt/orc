"""`orc replay <run_id>` command."""

from __future__ import annotations

import json as json_lib

import click
from rich.console import Console

from orc.errors import TraceNotFoundError
from orc.runs.replay import replay as do_replay

console = Console()


@click.command("replay")
@click.argument("run_id")
@click.option(
    "--live",
    is_flag=True,
    help="Re-execute against the current corpus instead of the frozen snapshot.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON.")
def replay_command(run_id: str, live: bool, as_json: bool) -> None:
    """Re-run a previous run by run_id. Default is frozen replay."""
    try:
        out = do_replay(run_id, live=live)
    except TraceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json_lib.dumps(out, indent=2, default=str))
        return

    mode = out["mode"]
    console.print(f"[bold]replay mode:[/bold] {mode}")
    console.print(f"  original_run_id         = {out['original_run_id']}")
    console.print(f"  new_run_id              = {out['new_run_id']}")
    console.print(f"  original_corpus_version = {out['original_corpus_version']}")
    console.print(f"  current_corpus_version  = {out['current_corpus_version']}")
    if "original_schema_version" in out:
        console.print(f"  original_schema_version = {out['original_schema_version']}")
    source = out.get("kwargs_source")
    if source == "effective_kwargs":
        console.print(
            "  kwargs_source           = [green]effective_kwargs[/green] "
            "[dim](pinned snapshot — true reproduction)[/dim]"
        )
    elif source == "legacy_inputs":
        console.print(
            "  kwargs_source           = [yellow]legacy_inputs[/yellow] "
            "[dim](best-effort — manifest defaults read from current spec)[/dim]"
        )
    result = out["result"]
    if "label" in result:
        console.print(f"\n  result.label      = [bold]{result['label']}[/bold]")
        console.print(f"  result.confidence = {result.get('confidence', 0):.2f}")
