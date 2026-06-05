"""BM25 retrieval via SQLite FTS5.

Returns chunks ranked by FTS5's BM25 score (which it returns as a *negative* number
where smaller is better). We invert and normalize for downstream reranking convenience.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    evidence_id: str
    seq: int
    text: str
    headings_path: str | None
    token_count: int
    rank: int
    bm25_score: float
    evidence_title: str | None
    evidence_source_path: str

    def to_summary(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "evidence_id": self.evidence_id,
            "rank": self.rank,
            "bm25_score": self.bm25_score,
            "headings_path": self.headings_path,
            "evidence_title": self.evidence_title,
        }


_SELECT = """
SELECT
    chunk.chunk_id      AS chunk_id,
    chunk.evidence_id   AS evidence_id,
    chunk.seq           AS seq,
    chunk.text          AS text,
    chunk.headings_path AS headings_path,
    chunk.token_count   AS token_count,
    evidence.title      AS evidence_title,
    evidence.source_path AS evidence_source_path,
    bm25(chunk_fts)     AS score
FROM chunk_fts
JOIN chunk    ON chunk.rowid = chunk_fts.rowid
JOIN evidence ON evidence.evidence_id = chunk.evidence_id
WHERE chunk_fts MATCH ?
"""


def bm25_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    corpus_version: int | None = None,
) -> list[RetrievedChunk]:
    """Run a BM25 search over chunk_fts. Returns at most `limit` chunks ranked best-first.

    When `corpus_version` is given, only chunks belonging to evidence ingested at or before
    that corpus version are considered — this is the basis of "frozen replay".
    """
    fts_query = _fts_query_from_user_text(query)
    if not fts_query:
        return []

    if corpus_version is None:
        sql = _SELECT + "\nORDER BY bm25(chunk_fts) ASC LIMIT ?"
        params: tuple = (fts_query, limit)
    else:
        sql = _SELECT + "\nAND evidence.corpus_version <= ?\nORDER BY bm25(chunk_fts) ASC LIMIT ?"
        params = (fts_query, corpus_version, limit)

    rows = conn.execute(sql, params).fetchall()

    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            evidence_id=row["evidence_id"],
            seq=row["seq"],
            text=row["text"],
            headings_path=row["headings_path"],
            token_count=row["token_count"],
            rank=i,
            bm25_score=float(row["score"]),
            evidence_title=row["evidence_title"],
            evidence_source_path=row["evidence_source_path"],
        )
        for i, row in enumerate(rows)
    ]


def _fts_query_from_user_text(text: str) -> str:
    """Sanitize free text into an FTS5 MATCH expression.

    FTS5 has special characters (", *, :, AND, OR, NOT) that need quoting.
    For v1 we keep this simple: tokenize on word characters, drop empties, OR-join.
    """
    tokens = re.findall(r"\w+", text.lower())
    # Drop single-character ASCII tokens (English noise like "a"), but keep
    # single non-ASCII tokens — a lone CJK ideograph is a real query term.
    tokens = [t for t in tokens if len(t) > 1 or not t.isascii()]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)
