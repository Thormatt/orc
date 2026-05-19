"""Domain → verify-mode routing.

Callers can pass `domain="pubmedQA"` (or any other registered domain) to
`verify_claim` and the runtime picks the best mode empirically — derived from
the per-source-ds F1 breakdown in the HaluBench benchmark. The benchmark's
`SOURCE_TO_MODE` is now a thin import from this dict so the runtime and the
benchmark routing can never drift.

In production this lives behind a workspace tag, a manifest hint, or an
explicit `--domain` flag on the verify call. Unknown domains raise rather than
silently fall through — calling code should validate domain values at the
surface (CLI/MCP), not here.
"""

from __future__ import annotations

from orc.errors import OrcError


class UnknownDomainError(OrcError):
    """Raised when a caller passes a domain not present in DOMAIN_TO_MODE."""


# Empirically derived from per-source-ds F1 on the HaluBench 504-item stratified
# subsample. See docs/benchmarks/results-2026-05-19-source-routed.md.
DOMAIN_TO_MODE: dict[str, str] = {
    "covidQA": "evidence",
    "RAGTruth": "evidence",
    "halueval": "judgment",
    "pubmedQA": "binary",
    "FinanceBench": "binary",
    "DROP": "binary",
}


def route_to_mode(domain: str | None) -> str | None:
    """Return the routed mode for `domain`, or None if `domain` is None.

    Raises UnknownDomainError when `domain` is a string not in DOMAIN_TO_MODE.
    Callers must validate at their surface; we don't silently fall through to
    a default — that would mask config typos and make replay non-deterministic.
    """
    if domain is None:
        return None
    try:
        return DOMAIN_TO_MODE[domain]
    except KeyError as exc:
        known = sorted(DOMAIN_TO_MODE.keys())
        raise UnknownDomainError(
            f"unknown domain {domain!r}; known: {known}"
        ) from exc
