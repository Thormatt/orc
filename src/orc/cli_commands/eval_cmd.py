"""`orc eval ...` — gold set, gate measurement, and tiered calibration."""

from __future__ import annotations

import json as json_lib
from pathlib import Path

import click
import yaml

from orc.errors import WorkspaceNotFoundError
from orc.eval import gold
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace

_LABELS = ["supported", "contradicted", "not_found", "partial"]


@click.group("eval")
def eval_group() -> None:
    """Measure and calibrate the verification gate against a gold set."""


@eval_group.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--workspace", "-w", default=None, help="Workspace name (env: ORC_DEFAULT_WORKSPACE)")
def import_command(path: Path, workspace: str | None) -> None:
    """Seed gold claims from a YAML file (id/text/expected[/relevant_chunk_ids/note])."""
    ws = _resolve(workspace)
    items = yaml.safe_load(path.read_text()) or []
    n = 0
    for item in items:
        gold.add(
            ws.name,
            claim=item["text"],
            expected_label=item["expected"],
            corpus_version=ws.corpus_version,
            relevant_chunk_ids=item.get("relevant_chunk_ids"),
            source="import",
            note=item.get("note"),
        )
        n += 1
    click.echo(f"Imported {n} gold claim(s) into {ws.name}")


@eval_group.command("label")
@click.argument("run_id")
@click.option("--verdict", required=True, type=click.Choice(_LABELS))
@click.option("--relevant", "relevant", multiple=True, help="Relevant chunk id (repeatable)")
@click.option("--workspace", "-w", default=None)
@click.option("--note", default=None)
def label_command(
    run_id: str,
    verdict: str,
    relevant: tuple[str, ...],
    workspace: str | None,
    note: str | None,
) -> None:
    """Promote/correct a real verdict into the gold set.

    Pulls the claim and corpus_version straight from the run's trace, so a
    promoted label is grounded in exactly what orc verified."""
    try:
        trace = load_trace(run_id)
    except Exception as exc:  # TraceNotFoundError and friends
        raise click.ClickException(f"Run {run_id} not found: {exc}") from exc
    claim = (trace.get("inputs") or {}).get("claim") or (trace.get("output") or {}).get("claim")
    if not claim:
        raise click.ClickException(f"Run {run_id} has no claim to label")
    gold.add(
        trace["workspace"],
        claim=claim,
        expected_label=verdict,
        corpus_version=trace["corpus_version"],
        relevant_chunk_ids=list(relevant) or None,
        source="promoted",
        source_run_id=run_id,
        note=note,
    )
    click.echo(f"Labelled run {run_id} as {verdict} in {trace['workspace']}")


@eval_group.command("gold")
@click.argument("action", type=click.Choice(["list"]))
@click.option("--workspace", "-w", default=None)
@click.option("--json", "as_json", is_flag=True)
def gold_command(action: str, workspace: str | None, as_json: bool) -> None:
    """Inspect the gold set (currently: list)."""
    ws = _resolve(workspace)
    items = gold.list_gold(ws.name)
    stale = {
        g.gold_id
        for g in items
        if g.relevant_chunk_ids and g.corpus_version < ws.corpus_version
    }
    if as_json:
        click.echo(
            json_lib.dumps(
                [
                    {
                        "gold_id": g.gold_id,
                        "claim": g.claim,
                        "expected_label": g.expected_label,
                        "corpus_version": g.corpus_version,
                        "source": g.source,
                        "stale_chunk_labels": g.gold_id in stale,
                    }
                    for g in items
                ],
                indent=2,
            )
        )
        return
    if not items:
        click.echo(f"No gold claims in {ws.name}")
        return
    for g in items:
        flag = "  [stale chunk labels]" if g.gold_id in stale else ""
        click.echo(f"{g.gold_id}  {g.expected_label:<12} {g.claim[:60]}{flag}")


def _resolve(workspace: str | None) -> ws_module.Workspace:
    try:
        return ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
