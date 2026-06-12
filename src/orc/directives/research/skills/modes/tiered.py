"""Tiered verification: a cheap pass first, an expensive one only when needed.

Tier 1 is a cheap binary judge on every claim. When its confidence clears the
calibrated escalation threshold, that verdict ships. Otherwise the claim
escalates to Tier 2 — a stronger evidence-mode judge, optionally a different
model *family* (set `top_judge_model` to e.g. a GPT/Gemini/Llama model via
OpenRouter) so the escalation judge doesn't share Tier 1's blind spots.

The threshold comes from `orc eval calibrate` (tuned on the gold set, never
guessed). With no policy, a conservative default routes and a warning fires so
the operator knows tiering is uncalibrated.

`run_tiered` takes the skill instance (`self`) and calls `self.run(...)` for
each tier — the same pattern decomposed/arithmetic use — so this module never
imports verify_claim and there is no import cycle."""

from __future__ import annotations

import warnings
from typing import Any

from orc.eval.policy import load_policy

_DEFAULT_TIER1_MODEL = "claude-haiku-4-5"
_DEFAULT_TIER2_MODEL = "claude-sonnet-4-6"
_DEFAULT_THRESHOLD = 0.9


def run_tiered(
    *,
    self: Any,
    workspace: Any,
    run: Any,
    claim: str,
    model: str | None,
    k: int,
    retrieval_pool: int,
    max_tokens: int,
    client: Any,
    corpus_version: int | None,
    evidence_id: str | None,
) -> dict[str, Any]:
    policy = load_policy(workspace.name)
    if policy is None:
        warnings.warn(
            f"workspace {workspace.name!r} is not calibrated for tiered verify; "
            "run `orc eval calibrate` to tune the threshold. Using default "
            f"{_DEFAULT_THRESHOLD}.",
            UserWarning,
            stacklevel=2,
        )
        tier1_model = _DEFAULT_TIER1_MODEL
        tier2_model = _DEFAULT_TIER2_MODEL
        top_judge = None
        threshold = _DEFAULT_THRESHOLD
    else:
        tier1_model = policy.tier1_model
        tier2_model = policy.tier2_model
        top_judge = policy.top_judge_model
        threshold = policy.escalation_threshold

    # Tier 1: cheap binary judge on every claim.
    tier1 = self.run(
        workspace=workspace, run=run, claim=claim, mode="binary",
        model=tier1_model, k=k, retrieval_pool=retrieval_pool, max_tokens=max_tokens,
        client=client, corpus_version=corpus_version, evidence_id=evidence_id,
    )
    if tier1["confidence"] >= threshold:
        run.record("tiered", {
            "tier": 1, "escalated": False, "threshold": threshold,
            "tier1_confidence": tier1["confidence"], "tier1_model": tier1_model,
        })
        return {**tier1, "tier": 1, "escalated": False}

    # Tier 2: stronger judge (optionally cross-family) decides.
    tier2_judge = top_judge or tier2_model
    tier2 = self.run(
        workspace=workspace, run=run, claim=claim, mode="evidence",
        model=tier2_judge, k=k, retrieval_pool=retrieval_pool, max_tokens=max_tokens,
        client=client, corpus_version=corpus_version, evidence_id=evidence_id,
    )
    run.record("tiered", {
        "tier": 2, "escalated": True, "threshold": threshold,
        "tier1_confidence": tier1["confidence"], "tier1_label": tier1["label"],
        "tier1_model": tier1_model, "tier2_model": tier2_judge,
        "reason": "tier1_confidence_below_threshold",
    })
    return {**tier2, "tier": 2, "escalated": True}
