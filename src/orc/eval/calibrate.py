"""Derive a tiered escalation threshold from the gold set.

Runs every gold claim through Tier 1 (the cheap binary judge), then sweeps the
confidence cutoff to find the *lowest* threshold at which Tier-1-accepted claims
still reach the target accuracy — lowest because that accepts the most at Tier 1
and escalates the fewest. If no cutoff reaches the target (Tier 1 caps below it),
the result is reported as unachievable rather than silently writing a policy
that escalates everything; the caller surfaces that and the achievable maximum."""

from __future__ import annotations

from dataclasses import dataclass

from orc import directives
from orc.eval import gold as gold_store
from orc.metrics.calibration import ConfidenceResult
from orc.runs import open_run
from orc.storage import workspace as ws_module

DEFAULT_TIER1_MODEL = "claude-haiku-4-5"
DEFAULT_TIER2_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class CalibrationResult:
    achievable: bool
    threshold: float
    escalation_rate: float
    accepted_accuracy: float
    max_accuracy: float
    n: int


def sweep_threshold(results: list[ConfidenceResult], *, target: float) -> CalibrationResult:
    n = len(results)
    if n == 0:
        return CalibrationResult(False, 1.0, 0.0, 0.0, 0.0, 0)

    thresholds = sorted({r.confidence for r in results})
    max_accuracy = 0.0
    max_at = thresholds[-1]
    meeting: tuple[float, float, float] | None = None

    for t in thresholds:  # ascending: first to meet target is the lowest
        accepted = [r for r in results if r.confidence >= t]
        if not accepted:
            continue
        acc = sum(1 for r in accepted if r.correct) / len(accepted)
        esc = sum(1 for r in results if r.confidence < t) / n
        if acc > max_accuracy:
            max_accuracy = acc
            max_at = t
        if acc >= target and meeting is None:
            meeting = (t, acc, esc)

    if meeting is not None:
        t, acc, esc = meeting
        return CalibrationResult(True, t, esc, acc, max_accuracy, n)

    esc_at_max = sum(1 for r in results if r.confidence < max_at) / n
    return CalibrationResult(False, max_at, esc_at_max, max_accuracy, max_accuracy, n)


def _tier1_results(workspace, *, tier1_model: str) -> list[ConfidenceResult]:
    """Run every gold claim through the cheap binary judge and score it.

    Binary collapses to grounded/ungrounded, so a "supported" verdict is correct
    when the gold label is "supported"; anything else is correct when the gold
    label is not "supported"."""
    ws = ws_module.resolve(workspace)
    items = gold_store.list_gold(ws.name)
    skill = directives.get("research").skills["verify_claim"]
    out: list[ConfidenceResult] = []
    for g in items:
        with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": g.claim}) as run:
            result = skill.run(
                workspace=ws, run=run, claim=g.claim, mode="binary",
                model=tier1_model, corpus_version=g.corpus_version,
            )
            run.close(output=result)
        predicted_supported = result["label"] == "supported"
        expected_supported = g.expected_label == "supported"
        out.append(
            ConfidenceResult(
                confidence=float(result["confidence"]),
                correct=predicted_supported == expected_supported,
            )
        )
    return out


def calibrate(
    workspace: str,
    *,
    target: float = 0.95,
    tier1_model: str = DEFAULT_TIER1_MODEL,
) -> CalibrationResult:
    results = _tier1_results(workspace, tier1_model=tier1_model)
    return sweep_threshold(results, target=target)
