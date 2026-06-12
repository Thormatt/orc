"""Confidence calibration: do the gate's confidence scores mean what they say?

A well-calibrated judge that reports 0.9 confidence is right ~90% of the time.
reliability_bins groups predictions by confidence and reports actual accuracy
per bin; ECE is the count-weighted average gap between stated confidence and
realized accuracy. This is the signal `orc eval calibrate` uses to choose a
tier-1 escalation threshold."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    correct: bool


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    count: int
    mean_confidence: float
    accuracy: float


def reliability_bins(results: list[ConfidenceResult], *, n_bins: int = 10) -> list[Bin]:
    width = 1.0 / n_bins
    out: list[Bin] = []
    for i in range(n_bins):
        lo = i * width
        hi = 1.0 if i == n_bins - 1 else (i + 1) * width
        # Top bin is closed on the right so confidence==1.0 lands somewhere.
        members = [
            r for r in results
            if r.confidence >= lo and (r.confidence < hi or (hi == 1.0 and r.confidence <= hi))
        ]
        if not members:
            continue
        count = len(members)
        out.append(
            Bin(
                lo=lo,
                hi=hi,
                count=count,
                mean_confidence=sum(r.confidence for r in members) / count,
                accuracy=sum(1 for r in members if r.correct) / count,
            )
        )
    return out


def expected_calibration_error(bins: list[Bin]) -> float:
    total = sum(b.count for b in bins)
    if total == 0:
        return 0.0
    return sum(b.count * abs(b.mean_confidence - b.accuracy) for b in bins) / total
