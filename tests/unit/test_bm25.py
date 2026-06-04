"""BM25 retrieval tests."""

from __future__ import annotations

from pathlib import Path

from orc.ingest.pipeline import ingest as do_ingest
from orc.paths import workspace_db_path
from orc.retrieval import bm25_search
from orc.retrieval.bm25 import _fts_query_from_user_text
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection


def _setup_corpus(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "skills.md").write_text(
        "# Skills API\n\n"
        "Anthropic released the Skills API in October 2025. "
        "Skills are versioned auditable capabilities Claude composes at runtime.\n"
    )
    (corpus / "context.md").write_text(
        "# Context engineering\n\n"
        "Context engineering is iterative: each model call curates what context to send.\n"
    )
    (corpus / "prompt_caching.md").write_text(
        "# Prompt caching\n\nAnthropic prompt caching has a 5-minute ephemeral TTL by default.\n"
    )
    do_ingest(ws, str(corpus))
    return ws.name


def test_query_returns_relevant_chunk_first(orc_home: Path, tmp_path: Path) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    with open_connection(workspace_db_path(name)) as conn:
        results = bm25_search(conn, "skills api versioned", limit=5)
    assert results, "expected at least one match"
    assert results[0].evidence_title == "Skills API"
    assert results[0].rank == 0


def test_query_for_unrelated_term_returns_empty_or_low(orc_home: Path, tmp_path: Path) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    with open_connection(workspace_db_path(name)) as conn:
        results = bm25_search(conn, "kubernetes pods", limit=5)
    assert results == []


def test_query_uses_or_semantics(orc_home: Path, tmp_path: Path) -> None:
    """A query with 'context' should still match the context_engineering doc."""
    name = _setup_corpus(orc_home, tmp_path)
    with open_connection(workspace_db_path(name)) as conn:
        results = bm25_search(conn, "context", limit=5)
    titles = {r.evidence_title for r in results}
    assert "Context engineering" in titles


def test_fts_query_sanitizes_special_chars() -> None:
    assert _fts_query_from_user_text("hello world") == '"hello" OR "world"'
    assert _fts_query_from_user_text("AND OR foo*") == '"and" OR "or" OR "foo"'
    assert _fts_query_from_user_text("") == ""
    assert _fts_query_from_user_text("a") == ""  # single ASCII chars dropped (noise)


def test_fts_query_keeps_single_nonascii_tokens() -> None:
    # A single CJK ideograph is a meaningful term, unlike a single ASCII letter.
    # Dropping it would turn the query into "" and short-circuit to a confident
    # not_found over a corpus that may well contain the character.
    assert _fts_query_from_user_text("水") == '"水"'
    assert _fts_query_from_user_text("水 a") == '"水"'
