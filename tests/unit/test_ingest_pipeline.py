"""Ingest pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from orc.cli import main
from orc.errors import IngestError
from orc.ingest.pipeline import ingest as do_ingest
from orc.paths import workspace_db_path
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection


def _make_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Alpha\n\nFirst document body.\n")
    (corpus / "b.md").write_text("# Beta\n\nSecond document body.\n")
    nested = corpus / "sub"
    nested.mkdir()
    (nested / "c.md").write_text("# Gamma\n\nThird, nested document body.\n")
    (corpus / "skip.bin").write_bytes(b"\x00\x01")
    return corpus


def test_ingest_dir_recursively(orc_home: Path, tmp_path: Path) -> None:
    ws = ws_module.create("demo")
    corpus = _make_corpus(tmp_path)
    ids = do_ingest(ws, str(corpus))
    assert len(ids) == 3

    with open_connection(workspace_db_path("demo")) as conn:
        evidence_count = conn.execute("SELECT COUNT(*) AS n FROM evidence").fetchone()["n"]
        chunk_count = conn.execute("SELECT COUNT(*) AS n FROM chunk").fetchone()["n"]
        cv = conn.execute("SELECT corpus_version FROM workspace WHERE name='demo'").fetchone()[
            "corpus_version"
        ]
    assert evidence_count == 3
    assert chunk_count >= 3
    assert cv == 3  # bumped per ingest


def test_ingest_leaves_no_orphan_file_when_db_write_fails(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If anything fails after the evidence bytes are persisted, the file must not
    # be left orphaned on disk with no DB row — that desyncs the corpus and breaks
    # the audit story. The write is committed only if the DB transaction succeeds.
    ws = ws_module.create("demo")
    doc = tmp_path / "a.md"
    doc.write_text("# Alpha\n\nBody.\n")

    import orc.ingest.pipeline as pipeline_mod
    from orc.paths import workspace_evidence_dir

    def boom(_text: str) -> list:
        raise RuntimeError("simulated chunker failure")

    monkeypatch.setattr(pipeline_mod, "chunk_text", boom)

    with pytest.raises(RuntimeError):
        do_ingest(ws, str(doc))

    leftover = list(workspace_evidence_dir("demo").iterdir())
    assert leftover == [], f"orphaned evidence files: {leftover}"

    with open_connection(workspace_db_path("demo")) as conn:
        evidence_count = conn.execute("SELECT COUNT(*) AS n FROM evidence").fetchone()["n"]
    assert evidence_count == 0


def test_ingest_is_idempotent_on_sha(orc_home: Path, tmp_path: Path) -> None:
    ws = ws_module.create("demo")
    corpus = _make_corpus(tmp_path)
    first = do_ingest(ws, str(corpus))
    second = do_ingest(ws, str(corpus))
    assert len(first) == 3
    assert second == []  # all dups


def test_ingest_unknown_path_errors(orc_home: Path) -> None:
    ws = ws_module.create("demo")
    with pytest.raises(IngestError):
        do_ingest(ws, "/no/such/path/here")


def test_cli_ingest_smoke(orc_home: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["workspace", "create", "demo"])
    corpus = _make_corpus(tmp_path)
    result = runner.invoke(main, ["ingest", str(corpus), "--workspace", "demo"])
    assert result.exit_code == 0, result.output
    assert "Ingested" in result.output
    assert "3" in result.output


def test_fts_can_find_ingested_text(orc_home: Path, tmp_path: Path) -> None:
    ws = ws_module.create("demo")
    corpus = _make_corpus(tmp_path)
    do_ingest(ws, str(corpus))
    with open_connection(workspace_db_path("demo")) as conn:
        rows = conn.execute(
            "SELECT chunk.chunk_id, evidence.title FROM chunk_fts "
            "JOIN chunk ON chunk.rowid = chunk_fts.rowid "
            "JOIN evidence ON evidence.evidence_id = chunk.evidence_id "
            "WHERE chunk_fts MATCH 'Alpha'"
        ).fetchall()
    assert any(r["title"] == "Alpha" for r in rows)
