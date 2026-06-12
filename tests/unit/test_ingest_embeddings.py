"""Embed-at-ingest and backfill tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.errors import IngestError
from orc.ingest.pipeline import ingest as do_ingest
from orc.paths import workspace_db_path
from orc.retrieval.embedder import set_embedder_factory
from orc.storage import embeddings_store
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection
from tests._fake_embedder import FakeEmbedder


def _write_doc(tmp_path: Path, name: str, text: str) -> Path:
    doc = tmp_path / name
    doc.write_text(text)
    return doc


def test_ingest_embeds_chunks_atomically(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    ws = ws_module.create("demo", embedding_model=fake_embedder.model_id)
    doc = _write_doc(tmp_path, "a.md", "# Doc A\n\nThe Skills API ships in October 2025.\n")
    do_ingest(ws, str(doc))

    with open_connection(workspace_db_path(ws.name)) as conn:
        embeddings_store.load_vec_extension(conn)
        chunk_count = conn.execute("SELECT COUNT(*) AS n FROM chunk").fetchone()["n"]
        vec_rows = conn.execute(
            "SELECT chunk_id, corpus_version FROM chunk_vec ORDER BY chunk_id"
        ).fetchall()
        evidence_cv = conn.execute("SELECT corpus_version FROM evidence").fetchone()[
            "corpus_version"
        ]
    assert chunk_count >= 1
    assert len(vec_rows) == chunk_count
    assert all(row["corpus_version"] == evidence_cv for row in vec_rows)


def test_ingest_rolls_back_when_embedding_fails(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")

    class _BoomError(RuntimeError):
        pass

    def _explode(texts: list[str]) -> list[list[float]]:
        raise _BoomError("embedding backend down")

    fake_embedder.embed_texts = _explode  # type: ignore[method-assign]
    ws = ws_module.create("demo", embedding_model=fake_embedder.model_id)
    doc = _write_doc(tmp_path, "a.md", "# Doc A\n\nSome content.\n")
    with pytest.raises(_BoomError):
        do_ingest(ws, str(doc))

    with open_connection(workspace_db_path(ws.name)) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM evidence").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM chunk").fetchone()["n"] == 0


def test_ingest_fails_loud_when_model_set_but_embedder_missing(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orc.retrieval import embedder as embedder_module

    set_embedder_factory(None)
    monkeypatch.setattr(embedder_module, "find_spec", lambda name: None)
    ws = ws_module.create("demo", embedding_model="some-model")
    doc = _write_doc(tmp_path, "a.md", "# Doc A\n\nSome content.\n")
    with pytest.raises(IngestError, match=r'pip install "orc-ai\[embeddings\]"'):
        do_ingest(ws, str(doc))


def _seed_two_versions_unembedded(tmp_path: Path) -> ws_module.Workspace:
    """Two ingests (corpus_version 1 and 2) into a workspace WITHOUT embeddings."""
    ws = ws_module.create("demo")
    do_ingest(ws, str(_write_doc(tmp_path, "a.md", "# Doc A\n\nFirst document body.\n")))
    do_ingest(ws, str(_write_doc(tmp_path, "b.md", "# Doc B\n\nSecond document body.\n")))
    return ws_module.resolve(ws.name)


def test_backfill_preserves_original_corpus_versions(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    ws = _seed_two_versions_unembedded(tmp_path)
    with open_connection(workspace_db_path(ws.name)) as conn:
        embeddings_store.load_vec_extension(conn)
        embeddings_store.ensure_chunk_vec(conn, fake_embedder.dim)
        count = embeddings_store.backfill_embeddings(conn, fake_embedder)
        rows = conn.execute(
            "SELECT chunk_vec.corpus_version AS vec_cv, evidence.corpus_version AS ev_cv "
            "FROM chunk_vec "
            "JOIN chunk ON chunk.chunk_id = chunk_vec.chunk_id "
            "JOIN evidence ON evidence.evidence_id = chunk.evidence_id"
        ).fetchall()
    assert count == len(rows) >= 2
    assert all(row["vec_cv"] == row["ev_cv"] for row in rows)
    assert {row["ev_cv"] for row in rows} == {1, 2}


def test_backfill_is_idempotent(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    ws = _seed_two_versions_unembedded(tmp_path)
    with open_connection(workspace_db_path(ws.name)) as conn:
        embeddings_store.load_vec_extension(conn)
        embeddings_store.ensure_chunk_vec(conn, fake_embedder.dim)
        first = embeddings_store.backfill_embeddings(conn, fake_embedder)
        second = embeddings_store.backfill_embeddings(conn, fake_embedder)
        total = conn.execute("SELECT COUNT(*) AS n FROM chunk_vec").fetchone()["n"]
    assert first >= 2
    assert second == 0
    assert total == first


def test_cli_ingest_prints_embeddings_line_when_active(
    orc_home: Path, tmp_path: Path, fake_embedder: FakeEmbedder
) -> None:
    pytest.importorskip("sqlite_vec")
    from click.testing import CliRunner

    from orc.cli import main

    ws_module.create("demo", embedding_model=fake_embedder.model_id)
    doc = _write_doc(tmp_path, "a.md", "# Doc A\n\nSome content.\n")
    runner = CliRunner()
    result = runner.invoke(main, ["ingest", str(doc), "--workspace", "demo"])
    assert result.exit_code == 0, result.output
    assert f"embeddings: {fake_embedder.model_id}" in result.output


def test_cli_ingest_no_embeddings_line_for_plain_workspace(
    orc_home: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from orc.cli import main

    ws_module.create("demo")
    doc = _write_doc(tmp_path, "a.md", "# Doc A\n\nSome content.\n")
    runner = CliRunner()
    result = runner.invoke(main, ["ingest", str(doc), "--workspace", "demo"])
    assert result.exit_code == 0, result.output
    assert "embeddings:" not in result.output
