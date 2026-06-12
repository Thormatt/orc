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


@eval_group.command("run")
@click.option("--workspace", "-w", default=None)
@click.option("--mode", default="evidence", help="Verify mode to evaluate")
@click.option("--k", type=int, default=10, help="Retrieval depth for recall@k")
@click.option("--json", "as_json", is_flag=True)
def run_command(workspace: str | None, mode: str, k: int, as_json: bool) -> None:
    """Score the gate against the workspace's gold set."""
    from orc.eval.runner import run_eval

    ws = _resolve(workspace)
    try:
        report = run_eval(ws.name, mode=mode, k=k)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(_report_json(report))
        return
    click.echo(f"eval {report.eval_id}  mode={report.mode}  n={report.n}")
    click.echo(f"  judge accuracy : {report.accuracy:.3f}")
    click.echo(
        f"  supported P/R/F1: {report.supported_precision:.3f} / "
        f"{report.supported_recall:.3f} / {report.supported_f1:.3f}"
    )
    click.echo(f"  calibration ECE: {report.calibration_ece:.3f}  (lower = better calibrated)")
    if report.retrieval_recall is not None:
        click.echo(
            f"  retrieval recall: {report.retrieval_recall:.3f}  "
            f"({report.n_retrieval_labeled} labelled)"
        )
    if report.stale_entries:
        click.echo(
            f"  warning: {report.stale_entries} gold entr(ies) have chunk labels "
            f"older than the current corpus — recall measured frozen.",
            err=True,
        )


@eval_group.command("show")
@click.argument("eval_id")
@click.option("--workspace", "-w", default=None)
@click.option("--json", "as_json", is_flag=True)
def show_command(eval_id: str, workspace: str | None, as_json: bool) -> None:
    """Reprint a persisted eval report."""
    from orc.eval.runner import load_eval

    ws = _resolve(workspace)
    try:
        report = load_eval(ws.name, eval_id)
    except KeyError as exc:
        raise click.ClickException(f"No eval {eval_id} in {ws.name}") from exc
    click.echo(_report_json(report) if as_json else
               f"eval {report.eval_id}  mode={report.mode}  n={report.n}  "
               f"accuracy={report.accuracy:.3f}  ECE={report.calibration_ece:.3f}")


def _report_json(report: object) -> str:
    from dataclasses import asdict

    d = asdict(report)
    d["reliability"] = [asdict(b) for b in report.reliability]  # type: ignore[attr-defined]
    return json_lib.dumps(d, indent=2)


@eval_group.command("calibrate")
@click.option("--workspace", "-w", default=None)
@click.option("--target", type=float, default=0.95, show_default=True,
              help="Required Tier-1-accepted accuracy")
@click.option("--tier1-model", default=None, help="Cheap Tier-1 judge model")
@click.option("--tier2-model", default=None, help="Expensive Tier-2 judge model")
@click.option("--top-judge", default=None,
              help="Tier-2 model override (e.g. a cross-family judge via OpenRouter)")
def calibrate_command(
    workspace: str | None,
    target: float,
    tier1_model: str | None,
    tier2_model: str | None,
    top_judge: str | None,
) -> None:
    """Derive the tiered escalation threshold from the gold set and store it."""
    from orc.eval.calibrate import DEFAULT_TIER1_MODEL, DEFAULT_TIER2_MODEL, calibrate
    from orc.eval.policy import save_policy

    ws = _resolve(workspace)
    t1 = tier1_model or DEFAULT_TIER1_MODEL
    t2 = tier2_model or DEFAULT_TIER2_MODEL
    result = calibrate(ws.name, target=target, tier1_model=t1)
    if result.n == 0:
        raise click.ClickException(
            f"{ws.name} has no gold claims to calibrate against — `orc eval import` first"
        )

    save_policy(
        ws.name,
        tier1_model=t1,
        tier2_model=t2,
        top_judge_model=top_judge,
        escalation_threshold=result.threshold,
        target=target,
        calibrated_against_eval_id=None,
        n_gold=result.n,
    )
    if result.achievable:
        click.echo(
            f"Calibrated on {result.n} gold claim(s): escalate below confidence "
            f"{result.threshold:.3f} (Tier-1 accepts {1 - result.escalation_rate:.0%}, "
            f"escalates {result.escalation_rate:.0%}; accepted accuracy "
            f"{result.accepted_accuracy:.3f})."
        )
    else:
        click.echo(
            f"Tier 1 cannot reach {target:.2f} accuracy at any cutoff on this gold "
            f"set (max {result.max_accuracy:.2f}). Stored threshold "
            f"{result.threshold:.3f} (escalates {result.escalation_rate:.0%}); "
            f"lower --target or improve the gold set.",
            err=True,
        )


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
