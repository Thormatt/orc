"""Hybrid retrieval: BM25 + dense vectors fused with Reciprocal Rank Fusion.

Opt-in per workspace via the embedding_model column — workspaces without it
take the plain BM25 path and produce byte-identical results to before.

Residual replay nondeterminism (documented, accepted):
- The QUERY embedding is recomputed at replay time. chunk_vec rows are pinned
  by corpus_version, but torch/SIMD/BLAS variance across machines or library
  versions can perturb the query vector in the last few ulps, which can flip
  near-tie KNN orderings. Frozen replay is therefore best-effort for the
  vector leg; the trace records the method actually used.
- If embedding deps are absent at replay time, retrieve() falls back to BM25
  and records method="bm25" honestly rather than failing the replay. The
  replay engine warns when the method differs from the original trace.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import warnings
from dataclasses import dataclass

from orc.errors import EmbeddingsUnavailableError
from orc.retrieval.bm25 import RetrievedChunk, bm25_search
from orc.retrieval.embedder import get_embedder
from orc.storage.embeddings_store import (
    knn_chunk_ids,
    load_vec_extension,
    vec_extension_available,
)
from orc.storage.workspace import Workspace


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    method: str
    candidates_considered: int


# Same column set as bm25._SELECT minus the FTS score: vector hits hydrate into
# the same RetrievedChunk shape so downstream consumers can't tell legs apart.
_HYDRATE_SELECT = """
SELECT
    chunk.chunk_id      AS chunk_id,
    chunk.evidence_id   AS evidence_id,
    chunk.seq           AS seq,
    chunk.text          AS text,
    chunk.headings_path AS headings_path,
    chunk.token_count   AS token_count,
    evidence.title      AS evidence_title,
    evidence.source_path AS evidence_source_path
FROM chunk
JOIN evidence ON evidence.evidence_id = chunk.evidence_id
WHERE chunk.chunk_id IN ({placeholders})
"""


def vector_search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    limit: int,
    corpus_version: int | None,
) -> list[RetrievedChunk]:
    """KNN over chunk_vec, hydrated to RetrievedChunk in KNN (nearest-first) order.

    bm25_score is 0.0 for vector hits: the field carries the FTS score and a
    vector distance is not comparable, so we keep the sentinel explicit.
    """
    hits = knn_chunk_ids(conn, query_embedding, limit=limit, corpus_version=corpus_version)
    if not hits:
        return []
    ids = [chunk_id for chunk_id, _ in hits]
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(_HYDRATE_SELECT.format(placeholders=placeholders), ids).fetchall()
    by_id = {row["chunk_id"]: row for row in rows}
    out: list[RetrievedChunk] = []
    for i, chunk_id in enumerate(cid for cid in ids if cid in by_id):
        row = by_id[chunk_id]
        out.append(
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                evidence_id=row["evidence_id"],
                seq=row["seq"],
                text=row["text"],
                headings_path=row["headings_path"],
                token_count=row["token_count"],
                rank=i,
                bm25_score=0.0,
                evidence_title=row["evidence_title"],
                evidence_source_path=row["evidence_source_path"],
            )
        )
    return out


def rrf_fuse(
    bm25_results: list[RetrievedChunk],
    vector_results: list[RetrievedChunk],
    *,
    k: int = 60,
    limit: int,
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion over the two legs, rank-only.

    score(chunk) = sum over lists containing it of 1 / (k + rank + 1), with
    0-based ranks. Rank-only fusion sidesteps the incomparability of BM25
    scores and vector distances. For overlapping chunk_ids the BM25 instance
    is kept so the real bm25_score survives into the trace. Ties sort by
    chunk_id for deterministic, replayable output.
    """
    scores: dict[str, float] = {}
    instances: dict[str, RetrievedChunk] = {}
    for rank, chunk in enumerate(vector_results):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank + 1)
        instances[chunk.chunk_id] = chunk
    for rank, chunk in enumerate(bm25_results):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank + 1)
        instances[chunk.chunk_id] = chunk  # BM25 instance wins on overlap
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))[:limit]
    return [dataclasses.replace(instances[cid], rank=i) for i, cid in enumerate(ordered)]


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    workspace: Workspace,
    limit: int = 50,
    corpus_version: int | None = None,
) -> RetrievalResult:
    """Retrieve chunks for a query, hybrid when the workspace opts in.

    The embedding model comes ONLY from workspace.embedding_model — no env var
    override — because that column is the replay-pinned truth. When the model
    is set but the vector leg can't run (deps or chunk_vec missing), retrieval
    degrades to BM25 with a warning instead of failing: a read path must not
    hard-fail on an optional acceleration.
    """
    model = workspace.embedding_model
    if model is None:
        chunks = bm25_search(conn, query, limit=limit, corpus_version=corpus_version)
        return RetrievalResult(chunks=chunks, method="bm25", candidates_considered=len(chunks))

    reason = _vector_leg_unavailable_reason(conn, model)
    if reason is not None:
        warnings.warn(
            f"Workspace {workspace.name!r} has embedding_model={model!r} but {reason}; "
            "falling back to BM25. Run `orc workspace embed` after installing "
            'the embeddings extra (pip install "orc-ai[embeddings]").',
            RuntimeWarning,
            stacklevel=2,
        )
        chunks = bm25_search(conn, query, limit=limit, corpus_version=corpus_version)
        return RetrievalResult(chunks=chunks, method="bm25", candidates_considered=len(chunks))

    embedder = get_embedder(model)
    [query_embedding] = embedder.embed_texts([query])
    bm25_leg = bm25_search(conn, query, limit=limit, corpus_version=corpus_version)
    vector_leg = vector_search(conn, query_embedding, limit=limit, corpus_version=corpus_version)
    fused = rrf_fuse(bm25_leg, vector_leg, limit=limit)
    union = {c.chunk_id for c in bm25_leg} | {c.chunk_id for c in vector_leg}
    return RetrievalResult(chunks=fused, method="hybrid_rrf", candidates_considered=len(union))


def _vector_leg_unavailable_reason(conn: sqlite3.Connection, model: str) -> str | None:
    """None when the vector leg can run; otherwise a short human-readable reason."""
    if not vec_extension_available():
        return "the sqlite-vec extension is unavailable"
    try:
        get_embedder(model)
    except EmbeddingsUnavailableError:
        return "the embedding model dependencies are not installed"
    load_vec_extension(conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'chunk_vec'"
    ).fetchone()
    if row is None:
        return "the chunk_vec table does not exist yet"
    return None
