"""MCP server smoke tests.

The MCP wire protocol is heavy to spin up in a unit test. Instead we exercise:
- the server's tool registry (the four expected tools are registered)
- each tool's underlying core function (which is what the MCP layer routes to)

End-to-end stdio testing happens manually via `claude mcp add` / Claude Code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.mcp.server import (
    _get_trace_core,
    _search_evidence_core,
    _verify_claim_core,
    build_server,
)
from orc.storage import workspace as ws_module
from tests._fake_llm import FakeAnthropic, make_verdict_response


def _setup_corpus(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "skills.md").write_text(
        "# Skills API\n\nAnthropic released the Skills API in October 2025.\n"
    )
    do_ingest(ws, str(corpus))
    return ws.name


def test_server_registers_expected_tools() -> None:
    server = build_server()
    tool_names = {t.name for t in asyncio.run(server.list_tools())}
    assert tool_names == {
        "orc_verify_claim",
        "orc_search_evidence",
        "orc_research_topic",
        "orc_get_trace",
    }


def test_search_evidence_core_returns_chunks(orc_home: Path, tmp_path: Path) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    result = _search_evidence_core("skills api", workspace=name, k=5)
    assert "run_id" in result
    assert result["chunks"]
    assert result["chunks"][0]["evidence_title"] == "Skills API"


def test_search_evidence_core_unknown_workspace_returns_error(orc_home: Path) -> None:
    result = _search_evidence_core("anything", workspace="nope")
    assert "error" in result


def test_verify_claim_core_routes_through_run_and_skill(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.6)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    result = _verify_claim_core("Skills API release", workspace=name)
    assert "run_id" in result
    assert result["label"] == "not_found"


def test_verify_claim_core_rejects_empty_claim(orc_home: Path) -> None:
    ws_module.create("demo")
    assert "error" in _verify_claim_core("", workspace="demo")
    assert "error" in _verify_claim_core("   ", workspace="demo")


def test_get_trace_core_finds_written_trace(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    result = _verify_claim_core("Skills API", workspace=name)
    run_id = result["run_id"]
    trace = _get_trace_core(run_id)
    assert trace["run_id"] == run_id
    assert trace["skill"] == "verify_claim"


def test_get_trace_core_missing_returns_error(orc_home: Path) -> None:
    ws_module.create("demo")  # so workspaces dir exists
    result = _get_trace_core("01HXY-NOT-A-REAL-ID")
    assert "error" in result


def test_research_topic_unknown_workspace_returns_error() -> None:
    from orc.mcp.server import _research_topic_core

    result = _research_topic_core("anything", workspace="nope")
    assert "error" in result
