"""Embeddings store tests: chunk_vec lifecycle, KNN, and availability probing."""

from __future__ import annotations

import sqlite3

import pytest

from orc.storage import embeddings_store
from orc.storage.db import bootstrap_schema


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    bootstrap_schema(conn)
    return conn


def _vec_conn(dim: int) -> sqlite3.Connection:
    pytest.importorskip("sqlite_vec")
    conn = _connect()
    embeddings_store.load_vec_extension(conn)
    embeddings_store.ensure_chunk_vec(conn, dim)
    return conn


def test_store_and_knn_roundtrip() -> None:
    conn = _vec_conn(4)
    embeddings_store.store_chunk_embeddings(
        conn,
        [
            ("c1", 1, [1.0, 0.0, 0.0, 0.0]),
            ("c2", 1, [0.0, 1.0, 0.0, 0.0]),
        ],
    )
    hits = embeddings_store.knn_chunk_ids(conn, [1.0, 0.0, 0.0, 0.0], limit=2)
    assert [cid for cid, _ in hits] == ["c1", "c2"]
    assert hits[0][1] == pytest.approx(0.0)
    assert hits[0][1] < hits[1][1]


def test_knn_corpus_version_filter() -> None:
    conn = _vec_conn(4)
    embeddings_store.store_chunk_embeddings(
        conn,
        [
            ("c1", 1, [1.0, 0.0, 0.0, 0.0]),
            ("c2", 2, [1.0, 0.0, 0.0, 0.0]),
        ],
    )
    hits = embeddings_store.knn_chunk_ids(conn, [1.0, 0.0, 0.0, 0.0], limit=5, corpus_version=1)
    assert [cid for cid, _ in hits] == ["c1"]


def test_knn_equal_distances_tie_break_on_chunk_id() -> None:
    conn = _vec_conn(4)
    # Insert in reverse-lexicographic order to prove ordering is not insertion order.
    embeddings_store.store_chunk_embeddings(
        conn,
        [
            ("c2", 1, [0.0, 1.0, 0.0, 0.0]),
            ("c1", 1, [0.0, 1.0, 0.0, 0.0]),
        ],
    )
    hits = embeddings_store.knn_chunk_ids(conn, [0.0, 1.0, 0.0, 0.0], limit=2)
    assert [cid for cid, _ in hits] == ["c1", "c2"]


def test_ensure_chunk_vec_dim_mismatch_raises() -> None:
    conn = _vec_conn(4)
    with pytest.raises(ValueError, match="dim"):
        embeddings_store.ensure_chunk_vec(conn, 8)


def test_store_rejects_wrong_length_vector() -> None:
    conn = _vec_conn(4)
    with pytest.raises(ValueError, match="dim"):
        embeddings_store.store_chunk_embeddings(conn, [("c1", 1, [1.0, 0.0])])


def test_vec_extension_available_false_when_find_spec_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embeddings_store, "find_spec", lambda name: None)
    assert embeddings_store.vec_extension_available() is False


def test_chunks_missing_embeddings_lists_unembedded_chunks() -> None:
    conn = _vec_conn(4)
    conn.execute(
        "INSERT INTO evidence(evidence_id, source_path, stored_path, sha256, mime_type, "
        "ingested_at, corpus_version) VALUES (?,?,?,?,?,?,?)",
        ("ev1", "/x", "/y", "deadbeef", "text/plain", "2026-06-12T00:00:00Z", 3),
    )
    conn.execute(
        "INSERT INTO chunk(chunk_id, evidence_id, seq, text, token_count, headings_path, "
        "start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?)",
        ("c1", "ev1", 0, "hello world", 2, None, 0, 11),
    )
    missing = embeddings_store.chunks_missing_embeddings(conn)
    assert [(m["chunk_id"], m["text"], m["corpus_version"]) for m in missing] == [
        ("c1", "hello world", 3)
    ]

    embeddings_store.store_chunk_embeddings(conn, [("c1", 3, [1.0, 0.0, 0.0, 0.0])])
    assert embeddings_store.chunks_missing_embeddings(conn) == []
