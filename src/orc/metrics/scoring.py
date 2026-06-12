"""Confusion matrix and precision/recall/F1 over exact-label predictions.

Positive class is caller-chosen (e.g. "supported"); everything else is the
negative class. Predictions of None (the claim errored) are skipped, not
counted as wrong — an eval distinguishes "judged incorrectly" from "could not
judge"."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabeledResult:
    predicted: str | None
    expected: str


def confusion(results: list[LabeledResult], *, positive: str) -> dict[str, int]:
    tp = fp = tn = fn = 0
    for r in results:
        if r.predicted is None:
            continue
        pred_pos = r.predicted == positive
        exp_pos = r.expected == positive
        if pred_pos and exp_pos:
            tp += 1
        elif pred_pos and not exp_pos:
            fp += 1
        elif not pred_pos and not exp_pos:
            tn += 1
        else:
            fn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def scores(cm: dict[str, int]) -> dict[str, float]:
    tp, fp, tn, fn = cm["tp"], cm["fp"], cm["tn"], cm["fn"]
    n = tp + fp + tn + fn
    if n == 0:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"accuracy": (tp + tn) / n, "precision": precision, "recall": recall, "f1": f1}
