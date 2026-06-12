"""Run the verification gate against a workspace's gold set and score it.

Each gold claim is verified frozen against the `corpus_version` it was labeled
on (so retrieval-recall labels stay valid), inside its own traced Run tagged
with the eval id — an eval is therefore inspectable claim-by-claim and
replayable like any other orc run. The aggregate (judge accuracy, confidence
calibration, retrieval recall) is persisted to the eval_run table."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from orc import directives
from orc.core.clock import now_iso
from orc.core.ids import new_id
from orc.eval import gold as gold_store
from orc.metrics.calibration import (
    Bin,
    ConfidenceResult,
    expected_calibration_error,
    reliability_bins,
)
from orc.metrics.scoring import LabeledResult, confusion, scores
from orc.paths import workspace_db_path
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection, transaction


@dataclass(frozen=True)
class EvalReport:
    eval_id: str
    workspace: str
    mode: str
    n: int
    accuracy: float
    supported_precision: float
    supported_recall: float
    supported_f1: float
    calibration_ece: float
    reliability: list[Bin]
    retrieval_recall: float | None
    n_retrieval_labeled: int
    stale_entries: int


def recall(*, retrieved: list[str], relevant: list[str]) -> float | None:
    """Recall@k of relevant chunks among retrieved. None when there is nothing
    to recall (no relevant chunks labeled)."""
    if not relevant:
        return None
    hit = len(set(retrieved) & set(relevant))
    return hit / len(relevant)


def run_eval(workspace: str, *, mode: str = "evidence", k: int = 10) -> EvalReport:
    ws = ws_module.resolve(workspace)
    items = gold_store.list_gold(ws.name)
    if not items:
        raise ValueError(f"workspace {ws.name!r} has no gold claims to evaluate")

    eval_id = new_id()
    skill = directives.get("research").skills["verify_claim"]

    labeled: list[LabeledResult] = []
    confidences: list[ConfidenceResult] = []
    recalls: list[float] = []
    stale = 0

    for g in items:
        with open_run(
            ws,
            directive="research",
            skill="verify_claim",
            inputs={"_eval_id": eval_id, "gold_id": g.gold_id, "claim": g.claim},
        ) as run:
            result = skill.run(
                workspace=ws, run=run, claim=g.claim, mode=mode,
                corpus_version=g.corpus_version,
            )
            run.close(output=result)

        predicted = result["label"]
        correct = predicted == g.expected_label
        labeled.append(LabeledResult(predicted=predicted, expected=g.expected_label))
        confidences.append(ConfidenceResult(confidence=float(result["confidence"]), correct=correct))

        if g.relevant_chunk_ids:
            r = recall(retrieved=result.get("retrieval_chunk_ids", []), relevant=g.relevant_chunk_ids)
            if r is not None:
                recalls.append(r)
            if g.corpus_version < ws.corpus_version:
                stale += 1

    cm = confusion(labeled, positive="supported")
    sc = scores(cm)
    n_correct = sum(1 for r in confidences if r.correct)
    bins = reliability_bins(confidences, n_bins=10)

    report = EvalReport(
        eval_id=eval_id,
        workspace=ws.name,
        mode=mode,
        n=len(items),
        accuracy=n_correct / len(items),
        supported_precision=sc["precision"],
        supported_recall=sc["recall"],
        supported_f1=sc["f1"],
        calibration_ece=expected_calibration_error(bins),
        reliability=bins,
        retrieval_recall=(sum(recalls) / len(recalls)) if recalls else None,
        n_retrieval_labeled=len(recalls),
        stale_entries=stale,
    )
    _persist(ws.name, report, mode=mode, k=k)
    return report


def _persist(workspace: str, report: EvalReport, *, mode: str, k: int) -> None:
    with open_connection(workspace_db_path(workspace)) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO eval_run(eval_id, workspace, created_at, config_json, metrics_json) "
            "VALUES (?,?,?,?,?)",
            (
                report.eval_id,
                workspace,
                now_iso(),
                json.dumps({"mode": mode, "k": k}),
                json.dumps(_metrics_dict(report)),
            ),
        )


def _metrics_dict(report: EvalReport) -> dict:
    d = asdict(report)
    d["reliability"] = [asdict(b) for b in report.reliability]
    return d


def load_eval(workspace: str, eval_id: str) -> EvalReport:
    with open_connection(workspace_db_path(workspace)) as conn:
        row = conn.execute(
            "SELECT metrics_json FROM eval_run WHERE workspace=? AND eval_id=?",
            (workspace, eval_id),
        ).fetchone()
    if row is None:
        raise KeyError(f"no eval_run {eval_id!r} in {workspace!r}")
    m = json.loads(row["metrics_json"])
    return EvalReport(
        eval_id=m["eval_id"],
        workspace=m["workspace"],
        mode=m["mode"],
        n=m["n"],
        accuracy=m["accuracy"],
        supported_precision=m["supported_precision"],
        supported_recall=m["supported_recall"],
        supported_f1=m["supported_f1"],
        calibration_ece=m["calibration_ece"],
        reliability=[Bin(**b) for b in m["reliability"]],
        retrieval_recall=m["retrieval_recall"],
        n_retrieval_labeled=m["n_retrieval_labeled"],
        stale_entries=m["stale_entries"],
    )
