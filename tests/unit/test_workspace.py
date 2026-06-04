"""Workspace lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from orc.cli import main
from orc.errors import WorkspaceExistsError, WorkspaceNotFoundError
from orc.paths import workspace_db_path, workspace_evidence_dir, workspace_traces_dir
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection


def test_create_makes_dirs_and_db(orc_home: Path) -> None:
    ws = ws_module.create("demo")
    assert ws.name == "demo"
    assert ws.schema_version == 1
    assert ws.corpus_version == 0
    assert ws.embedding_model is None
    assert workspace_db_path("demo").exists()
    assert workspace_evidence_dir("demo").is_dir()
    assert workspace_traces_dir("demo").is_dir()


def test_create_duplicate_errors(orc_home: Path) -> None:
    ws_module.create("demo")
    with pytest.raises(WorkspaceExistsError):
        ws_module.create("demo")


@pytest.mark.parametrize("name", ["", "has space", "has/slash", "has.dot", "x" * 65])
def test_create_rejects_invalid_names(orc_home: Path, name: str) -> None:
    with pytest.raises(ValueError):
        ws_module.create(name)


def test_resolve_roundtrip(orc_home: Path) -> None:
    created = ws_module.create("demo")
    fetched = ws_module.resolve("demo")
    assert fetched == created


def test_resolve_unknown_errors(orc_home: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError):
        ws_module.resolve("nope")


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "../../etc",
        "a/b",
        "foo/../../bar",
        "with space",
        "x" * 65,
        "",
    ],
)
def test_resolve_rejects_traversal_and_invalid_names(orc_home: Path, name: str) -> None:
    # A malicious MCP/LLM-supplied workspace name must not escape the workspaces
    # root or build a path outside ~/.orc/workspaces/. resolve() validates the
    # name the same way create() does, before touching the filesystem, and must
    # not echo a resolved absolute/traversal path back to an untrusted caller
    # (which would be a filesystem-probe oracle).
    with pytest.raises(WorkspaceNotFoundError) as exc_info:
        ws_module.resolve(name)
    # The error may reflect the caller's own input, but must not leak the
    # resolved absolute filesystem path it probed.
    assert str(orc_home) not in str(exc_info.value)


def test_resolve_default_falls_back(orc_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_module.create("default")
    monkeypatch.delenv("ORC_DEFAULT_WORKSPACE", raising=False)
    assert ws_module.resolve(None).name == "default"


def test_resolve_default_respects_env(orc_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_module.create("alt")
    monkeypatch.setenv("ORC_DEFAULT_WORKSPACE", "alt")
    assert ws_module.resolve(None).name == "alt"


def test_list_all_sorted(orc_home: Path) -> None:
    ws_module.create("zeta")
    ws_module.create("alpha")
    ws_module.create("mu")
    names = [w.name for w in ws_module.list_all()]
    assert names == ["alpha", "mu", "zeta"]


def test_schema_has_expected_tables(orc_home: Path) -> None:
    ws_module.create("demo")
    with open_connection(workspace_db_path("demo")) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in rows]
    for required in ("workspace", "evidence", "chunk", "chunk_fts", "run", "run_evidence"):
        assert required in names, f"missing table: {required}"


def test_fts_triggers_keep_index_in_sync(orc_home: Path) -> None:
    ws_module.create("demo")
    with open_connection(workspace_db_path("demo")) as conn:
        conn.execute(
            "INSERT INTO evidence(evidence_id, source_path, stored_path, sha256, mime_type, "
            "ingested_at, corpus_version) VALUES (?,?,?,?,?,?,?)",
            ("ev1", "/x", "/y", "deadbeef", "text/plain", "2026-05-07T00:00:00Z", 1),
        )
        conn.execute(
            "INSERT INTO chunk(chunk_id, evidence_id, seq, text, token_count, headings_path, "
            "start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?)",
            ("c1", "ev1", 0, "the quick brown fox jumps over the lazy dog", 9, None, 0, 43),
        )
        rows = conn.execute(
            "SELECT chunk.chunk_id FROM chunk_fts JOIN chunk ON chunk.rowid = chunk_fts.rowid "
            "WHERE chunk_fts MATCH 'fox'"
        ).fetchall()
        assert [r["chunk_id"] for r in rows] == ["c1"]


def test_cli_workspace_create(orc_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["workspace", "create", "demo"])
    assert result.exit_code == 0, result.output
    assert "Created workspace" in result.output
    assert workspace_db_path("demo").exists()


def test_cli_workspace_list_empty(orc_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["workspace", "list"])
    assert result.exit_code == 0
    assert "No workspaces yet" in result.output


def test_cli_workspace_list_after_create(orc_home: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["workspace", "create", "demo"])
    result = runner.invoke(main, ["workspace", "list"])
    assert result.exit_code == 0
    assert "demo" in result.output
