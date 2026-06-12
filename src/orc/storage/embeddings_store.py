"""chunk_vec vector store backed by the sqlite-vec extension.

The table is created lazily (only workspaces that opt into embeddings pay for
it), and its dimension is stamped into schema_meta so a later open with a
different embedding model fails loudly instead of silently mixing vector
spaces.
"""

from __future__ import annotations

import sqlite3
from importlib.util import find_spec
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orc.retrieval.embedder import Embedder

_DIM_META_KEY = "chunk_vec_dim"


def vec_extension_available() -> bool:
    """True when sqlite-vec can actually be loaded into this interpreter.

    Both halves matter: the wheel must be installed AND the sqlite3 build must
    support runtime extension loading (some distro builds compile it out).
    """
    return find_spec("sqlite_vec") is not None and hasattr(
        sqlite3.Connection, "enable_load_extension"
    )


def load_vec_extension(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec into the connection. Idempotent per connection."""
    try:
        conn.execute("SELECT vec_version()")
        return
    except sqlite3.OperationalError:
        pass
    import sqlite_vec

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        # Re-disable immediately: nothing else should load extensions through
        # a connection that also executes retrieval queries over user input.
        conn.enable_load_extension(False)


def ensure_chunk_vec(conn: sqlite3.Connection, dim: int) -> None:
    """Create chunk_vec for `dim`-dimensional vectors, or verify the stamp.

    A dim mismatch means the workspace's embedding model changed under us —
    distances across models are meaningless, so we refuse rather than guess.
    """
    stamped = _stamped_dim(conn)
    if stamped is not None and stamped != dim:
        raise ValueError(
            f"chunk_vec dim mismatch: table was created with dim={stamped}, "
            f"requested dim={dim}. Re-embed the workspace with one model."
        )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0("
        f"chunk_id TEXT PRIMARY KEY, embedding FLOAT[{dim}], corpus_version INTEGER)"
    )
    if stamped is None:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            (_DIM_META_KEY, str(dim)),
        )


def store_chunk_embeddings(
    conn: sqlite3.Connection,
    items: list[tuple[str, int, list[float]]],
) -> None:
    """Insert (chunk_id, corpus_version, vector) rows into chunk_vec.

    No transaction here: the caller owns it, so ingest can commit chunk rows
    and their vectors atomically. Vector lengths are validated up front so a
    bad batch fails before any row is written.
    """
    import sqlite_vec

    dim = _stamped_dim(conn)
    for chunk_id, _, vector in items:
        if dim is not None and len(vector) != dim:
            raise ValueError(
                f"embedding for chunk {chunk_id!r} has dim {len(vector)}, expected {dim}"
            )
    conn.executemany(
        "INSERT INTO chunk_vec(chunk_id, embedding, corpus_version) VALUES (?, ?, ?)",
        [
            (chunk_id, sqlite_vec.serialize_float32(vector), corpus_version)
            for chunk_id, corpus_version, vector in items
        ],
    )


def knn_chunk_ids(
    conn: sqlite3.Connection,
    query_vec: list[float],
    *,
    limit: int,
    corpus_version: int | None = None,
) -> list[tuple[str, float]]:
    """K-nearest chunk_ids for a query vector, nearest first.

    The outer ORDER BY adds chunk_id as a tie-break: sqlite-vec guarantees
    distance order but not tie order, and replayable retrieval needs full
    determinism.
    """
    import sqlite_vec

    inner = "SELECT chunk_id, distance FROM chunk_vec WHERE embedding MATCH ? AND k = ?"
    params: tuple = (sqlite_vec.serialize_float32(query_vec), limit)
    if corpus_version is not None:
        inner += " AND corpus_version <= ?"
        params = (*params, corpus_version)
    # MATERIALIZED stops SQLite from flattening the subquery: vec0 KNN scans
    # only accept a bare ORDER BY distance, so the tie-break must apply outside.
    rows = conn.execute(
        f"WITH knn AS MATERIALIZED ({inner}) "
        "SELECT chunk_id, distance FROM knn ORDER BY distance, chunk_id",
        params,
    ).fetchall()
    return [(row["chunk_id"], float(row["distance"])) for row in rows]


def chunks_missing_embeddings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Chunks with no chunk_vec row, with the corpus_version of their evidence.

    Backfill must stamp each vector with the chunk's ORIGINAL corpus_version
    (not the current one) so frozen replay filtering stays truthful.
    """
    return conn.execute(
        "SELECT chunk.chunk_id AS chunk_id, chunk.text AS text, "
        "evidence.corpus_version AS corpus_version "
        "FROM chunk JOIN evidence ON evidence.evidence_id = chunk.evidence_id "
        "WHERE chunk.chunk_id NOT IN (SELECT chunk_id FROM chunk_vec) "
        "ORDER BY chunk.chunk_id"
    ).fetchall()


def backfill_embeddings(
    conn: sqlite3.Connection, embedder: Embedder, batch_size: int = 64
) -> int:
    """Embed every chunk that has no chunk_vec row yet. Returns rows written.

    Idempotent: only missing chunks are touched, so re-running after a crash
    (or on an already-complete corpus) is safe. Each vector is stamped with
    the chunk's ORIGINAL evidence corpus_version so frozen replay filters
    stay truthful. Batches commit independently — a failure mid-backfill
    keeps completed batches, and the next run picks up the remainder.
    """
    from orc.storage.db import transaction

    missing = chunks_missing_embeddings(conn)
    written = 0
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        vectors = embedder.embed_texts([row["text"] for row in batch])
        with transaction(conn):
            store_chunk_embeddings(
                conn,
                [
                    (row["chunk_id"], row["corpus_version"], vector)
                    for row, vector in zip(batch, vectors, strict=True)
                ],
            )
        written += len(batch)
    return written


def _stamped_dim(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?", (_DIM_META_KEY,)
    ).fetchone()
    return int(row["value"]) if row is not None else None
