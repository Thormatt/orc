"""Domain → verify-mode routing.

Callers pass a product domain (`domain="clinical"`, `domain="financial"`, ...)
to `verify_claim` and the runtime picks the verify mode that performed best on
the benchmark family that domain generalizes — derived from the per-source-ds
F1 breakdown in the HaluBench benchmark. The HaluBench `source_ds` names stay
accepted as benchmark aliases (`BENCHMARK_SOURCE_TO_MODE`) so the published
benchmark numbers remain reproducible, but the product surface is the domain
map: dataset names are benchmark artifacts, not domains a customer has.

In production this lives behind a workspace tag, a manifest hint, or an
explicit `--domain` flag on the verify call. Unknown domains raise rather than
silently fall through — calling code should validate domain values at the
surface (CLI/MCP), not here.
"""

from __future__ import annotations

# Re-exported for callers that historically imported it from here.
from orc.errors import UnknownDomainError

__all__ = ["BENCHMARK_SOURCE_TO_MODE", "DOMAIN_TO_MODE", "UnknownDomainError", "route_to_mode"]


# Product domains. Each mode is derived from the benchmark family the domain
# generalizes — per-source-ds F1 on the HaluBench 504-item stratified
# subsample (docs/benchmarks/results-2026-05-19-source-routed.md).
DOMAIN_TO_MODE: dict[str, str] = {
    # RAGTruth / covidQA family: prose-heavy retrieval QA where chunk-level
    # citations carry the verdict.
    "general": "evidence",
    # No benchmark evidence for legal yet. Evidence mode is the deliberate
    # default because chunk-level citations matter most in legal review.
    "legal": "evidence",
    # pubmedQA family: yes/no verdicts over a single passage.
    "clinical": "binary",
    # Alias of clinical — same pubmedQA family.
    "biomedical": "binary",
    # FinanceBench family: claims that hinge on derived numbers.
    "financial": "arithmetic",
    # DROP family: reading comprehension over numeric/tabular passages where
    # the answer is a single extracted or computed value.
    "numeric": "binary",
}

# HaluBench source_ds names, pinned exactly as published. The benchmark's
# SOURCE_TO_MODE imports this dict, so reproducibility of the published F1
# numbers cannot drift as product domains evolve. Do not edit without a
# benchmark re-run (docs/benchmarks/results-2026-05-19-source-routed.md).
BENCHMARK_SOURCE_TO_MODE: dict[str, str] = {
    "covidQA": "evidence",
    "RAGTruth": "evidence",
    "halueval": "judgment",
    "pubmedQA": "binary",
    "FinanceBench": "arithmetic",
    "DROP": "binary",
}


def route_to_mode(domain: str | None) -> str | None:
    """Return the routed mode for `domain`, or None if `domain` is None.

    Product domains resolve first; HaluBench source_ds names are accepted as
    benchmark aliases so existing callers and published numbers keep working.
    Raises UnknownDomainError otherwise — we don't silently fall through to a
    default; that would mask config typos and make replay non-deterministic.
    """
    if domain is None:
        return None
    if domain in DOMAIN_TO_MODE:
        return DOMAIN_TO_MODE[domain]
    if domain in BENCHMARK_SOURCE_TO_MODE:
        return BENCHMARK_SOURCE_TO_MODE[domain]
    domains = sorted(DOMAIN_TO_MODE)
    aliases = sorted(BENCHMARK_SOURCE_TO_MODE)
    raise UnknownDomainError(
        f"unknown domain {domain!r}; domains: {domains} "
        f"(benchmark source aliases also accepted: {aliases})"
    )
