"""Hybrid retrieval tests: RRF fusion math, vector hydration, fallbacks."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.retrieval import retrieve, rrf_fuse, vector_search
from orc.retrieval.bm25 import RetrievedChunk, bm25_search
from orc.retrieval.embedder import set_embedder_factory
from orc.storage import embeddings_store
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection
from tests._fake_embedder import FakeEmbedder


def _chunk(chunk_id: str, *, rank: int, bm25_score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        evidence_id=f"ev-{chunk_id}",
        seq=0,
        text=f"text {chunk_id}",
        headings_path=None,
        token_count=3,
        rank=rank,
        bm25_score=bm25_score,
        evidence_title=None,
        evidence_source_path="/x",
    )


def test_rrf_fuse_hand_computed_scores() -> None:
    # k=60, 0-based ranks. Scores:
    #   A: 1/61               (bm25 rank 0)
    #   B: 1/62 + 1/61        (bm25 rank 1, vector rank 0)
    #   C: 1/62               (vector rank 1)
    # B > A > C.
    bm25 = [_chunk("A", rank=0, bm25_score=-5.0), _chunk("B", rank=1, bm25_score=-4.0)]
    vector = [_chunk("B", rank=0), _chunk("C", rank=1)]
    fused = rrf_fuse(bm25, vector, k=60, limit=10)
    assert [c.chunk_id for c in fused] == ["B", "A", "C"]
    assert [c.rank for c in fused] == [0, 1, 2]


def test_rrf_fuse_overlap_keeps_real_bm25_score() -> None:
    bm25 = [_chunk("A", rank=0, bm25_score=-7.5)]
    vector = [_chunk("A", rank=0)]
    [fused] = rrf_fuse(bm25, vector, k=60, limit=10)
    assert fused.bm25_score == -7.5


def test_rrf_fuse_vector_only_chunk_has_zero_bm25_score() -> None:
    fused = rrf_fuse([], [_chunk("V", rank=0)], k=60, limit=10)
    assert [c.chunk_id for c in fused] == ["V"]
    assert fused[0].bm25_score == 0.0


def test_rrf_fuse_ties_order_by_chunk_id() -> None:
    # A appears only in bm25 at rank 0, B only in vector at rank 0: equal RRF
    # scores. Determinism demands the tie-break be chunk_id, not list order.
    fused = rrf_fuse([_chunk("B", rank=0)], [_chunk("A", rank=0)], k=60, limit=10)
    assert [c.chunk_id for c in fused] == ["A", "B"]


def test_rrf_fuse_respects_limit() -> None:
    bm25 = [_chunk("A", rank=0), _chunk("B", rank=1), _chunk("C", rank=2)]
    fused = rrf_fuse(bm25, [], k=60, limit=2)
    assert len(fused) == 2


def _setup_embedded_corpus(tmp_path: Path, fake: FakeEmbedder) -> ws_module.Workspace:
    """Workspace with two docs, chunk_vec populated via the fake embedder."""
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.paths import workspace_db_path

    fake.vocabulary.update({"caching": 0, "skills": 1})
    ws = ws_module.create("demo", embedding_model=fake.model_id)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "caching.md").write_text(
        "# Prompt caching\n\nPrompt caching has a 5-minute ephemeral TTL by default.\n"
    )
    (corpus / "skills.md").write_text(
        "# Skills API\n\nThe Skills API ships versioned auditable capabilities.\n"
    )
    do_ingest(ws, str(corpus))

    with open_connection(workspace_db_path(ws.name)) as conn:
        embeddings_store.load_vec_extension(conn)
        embeddings_store.ensure_chunk_vec(conn, fake.dim)
        missing = embeddings_store.chunks_missing_embeddings(conn)
        if missing:
            vectors = fake.embed_texts([m["text"] for m in missing])
            embeddings_store.store_chunk_embeddings(
                conn,
                [
                    (m["chunk_id"], m["corpus_version"], v)
                    for m, v in zip(missing, vectors, strict=True)
                ],
            )
    return ws_module.resolve(ws.name)


def test_vector_search_hydrates_in_knn_order(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    from orc.paths import workspace_db_path

    ws = _setup_embedded_corpus(tmp_path, fake_embedder)
    # Query the LATER-ingested doc so KNN order differs from insertion order.
    [query_vec] = fake_embedder.embed_texts(["skills"])
    with open_connection(workspace_db_path(ws.name)) as conn:
        embeddings_store.load_vec_extension(conn)
        chunks = vector_search(conn, query_vec, limit=5, corpus_version=None)
    assert chunks[0].evidence_title == "Skills API"
    assert chunks[0].text.startswith("# Skills API")
    assert [c.rank for c in chunks] == list(range(len(chunks)))
    assert all(c.bm25_score == 0.0 for c in chunks)


def test_retrieve_uses_bm25_when_no_embedding_model(
    orc_home: Path, tmp_path: Path
) -> None:
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.paths import workspace_db_path

    ws = ws_module.create("plain")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc\n\nPrompt caching has a 5-minute TTL.\n")
    do_ingest(ws, str(corpus))

    with open_connection(workspace_db_path(ws.name)) as conn:
        res = retrieve(conn, "prompt caching", workspace=ws, limit=5)
        expected = bm25_search(conn, "prompt caching", limit=5)
    assert res.method == "bm25"
    assert [c.chunk_id for c in res.chunks] == [c.chunk_id for c in expected]
    assert res.candidates_considered == len(expected)


def test_retrieve_falls_back_when_vec_extension_missing(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("sqlite_vec")
    from orc.paths import workspace_db_path
    from orc.retrieval import hybrid as hybrid_module

    ws = _setup_embedded_corpus(tmp_path, fake_embedder)
    monkeypatch.setattr(hybrid_module, "vec_extension_available", lambda: False)
    with open_connection(workspace_db_path(ws.name)) as conn:  # noqa: SIM117
        with pytest.warns(RuntimeWarning, match="orc workspace embed"):
            res = retrieve(conn, "skills", workspace=ws, limit=5)
    assert res.method == "bm25"


def test_retrieve_falls_back_when_embedder_missing(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("sqlite_vec")
    from orc.paths import workspace_db_path
    from orc.retrieval import embedder as embedder_module

    ws = _setup_embedded_corpus(tmp_path, fake_embedder)
    # Drop the factory and make sentence-transformers look uninstalled.
    set_embedder_factory(None)
    monkeypatch.setattr(embedder_module, "find_spec", lambda name: None)
    try:
        with open_connection(workspace_db_path(ws.name)) as conn:  # noqa: SIM117
            with pytest.warns(RuntimeWarning, match="orc workspace embed"):
                res = retrieve(conn, "skills", workspace=ws, limit=5)
    finally:
        set_embedder_factory(lambda model_id: fake_embedder)
    assert res.method == "bm25"


def test_retrieve_falls_back_when_chunk_vec_absent(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.paths import workspace_db_path

    # Corpus ingested BEFORE embeddings were enabled: chunk_vec never created.
    # Flipping the model flag afterwards must not break retrieval before
    # `orc workspace embed` has been run.
    ws = ws_module.create("novec")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc\n\nSkills are versioned capabilities.\n")
    do_ingest(ws, str(corpus))
    with open_connection(workspace_db_path(ws.name)) as conn:
        conn.execute(
            "UPDATE workspace SET embedding_model = ? WHERE name = ?",
            (fake_embedder.model_id, ws.name),
        )
        with pytest.warns(RuntimeWarning, match="orc workspace embed"):
            res = retrieve(conn, "skills", workspace=ws_module.resolve(ws.name), limit=5)
    assert res.method == "bm25"


def test_search_evidence_skill_records_hybrid_method(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    from orc.directives.research.skills.search_evidence import search_evidence
    from orc.runs import open_run
    from orc.storage.trace_store import load_trace

    ws = _setup_embedded_corpus(tmp_path, fake_embedder)
    with open_run(ws, directive="research", skill="search_evidence", inputs={}) as run:
        result = search_evidence.run(workspace=ws, run=run, query="skills", k=5)
        run.close(output=result)

    trace = load_trace(run.run_id)
    assert trace["retrieval"]["method"] == "hybrid_rrf"
    assert trace["retrieval"]["candidates_considered"] >= 1
    assert result["chunks"], "expected fused hits"


def test_retrieve_hybrid_fuses_and_reports_union(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    from orc.paths import workspace_db_path

    ws = _setup_embedded_corpus(tmp_path, fake_embedder)
    with open_connection(workspace_db_path(ws.name)) as conn:
        res = retrieve(conn, "skills", workspace=ws, limit=5)
        bm25_ids = {c.chunk_id for c in bm25_search(conn, "skills", limit=5)}
        [query_vec] = fake_embedder.embed_texts(["skills"])
        vec_ids = {c.chunk_id for c in vector_search(conn, query_vec, limit=5, corpus_version=None)}
    assert res.method == "hybrid_rrf"
    assert res.candidates_considered == len(bm25_ids | vec_ids)
    # The semantically scripted doc must be in the fused result.
    assert any(c.evidence_title == "Skills API" for c in res.chunks)
    assert [c.rank for c in res.chunks] == list(range(len(res.chunks)))
