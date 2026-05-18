"""MCP server smoke tests.

Two layers of coverage:
- Core-function tests exercise the Python functions the MCP tool decorators wrap.
  Fast, cover most regressions, no protocol overhead.
- Wire-protocol tests at the bottom of the file run an in-memory MCP client/server
  pair and exchange real JSON-RPC. They catch breaks in tool-schema generation,
  tool registration, and result encoding that core-function tests can't see.

True stdio (process-spawned) smoke still happens manually via `claude mcp add`.
"""

from __future__ import annotations

import asyncio
import json
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


def test_omitted_workspace_uses_env_default(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the MCP caller omits `workspace`, ORC_DEFAULT_WORKSPACE should win,
    not a hard-coded 'default' string. Regression for MCP-workspace-default bug.
    """
    name = _setup_corpus(orc_home, tmp_path)
    monkeypatch.setenv("ORC_DEFAULT_WORKSPACE", name)

    result = _search_evidence_core("skills api", k=3)  # workspace omitted
    assert "run_id" in result, result
    assert result["chunks"], "should have routed to env-default workspace"


def test_omitted_workspace_without_env_uses_literal_default(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ORC_DEFAULT_WORKSPACE isn't set, omitted workspace falls back to literal 'default'."""
    monkeypatch.delenv("ORC_DEFAULT_WORKSPACE", raising=False)
    ws_module.create("default")
    result = _search_evidence_core("anything", k=3)  # workspace omitted
    assert "run_id" in result, result


# ───────────── wire-protocol smoke tests ─────────────────────
#
# These exercise the FastMCP server through the real JSON-RPC client session,
# catching breaks in tool-schema generation / registration / result encoding
# that the core-function tests above cannot see.


def _extract_text_payload(call_result) -> dict:
    """Pull the JSON dict out of an MCP CallToolResult.

    FastMCP returns tool results as a list of content blocks; the JSON body
    is encoded as a TextContent block whose `.text` is the JSON string."""
    blocks = call_result.content
    assert blocks, "tool result had no content blocks"
    for b in blocks:
        text = getattr(b, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no text block in result: {blocks!r}")


async def test_wire_protocol_lists_tools_with_schemas(
    orc_home: Path, tmp_path: Path
) -> None:
    """Connect a real ClientSession to the FastMCP server and confirm tools are
    discoverable with proper schemas. Regression: a broken @mcp.tool decorator
    or rename would silently disappear here, but pass the registry test above."""
    from mcp.shared.memory import create_connected_server_and_client_session

    _setup_corpus(orc_home, tmp_path)
    server = build_server()
    async with create_connected_server_and_client_session(server) as client:
        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        assert names == {
            "orc_verify_claim",
            "orc_search_evidence",
            "orc_research_topic",
            "orc_get_trace",
        }
        # Each tool must publish an input schema the client can introspect.
        for t in listed.tools:
            assert t.inputSchema is not None
            assert t.description, f"tool {t.name} missing description"


async def test_wire_protocol_call_search_evidence(
    orc_home: Path, tmp_path: Path
) -> None:
    """Call orc_search_evidence over the wire and parse the JSON result. No LLM
    needed — pure retrieval — so this stays fast and deterministic."""
    from mcp.shared.memory import create_connected_server_and_client_session

    name = _setup_corpus(orc_home, tmp_path)
    server = build_server()
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(
            "orc_search_evidence",
            {"query": "skills api", "workspace": name, "k": 3},
        )
        assert result.isError is False, result
        payload = _extract_text_payload(result)
        assert "run_id" in payload
        assert payload["chunks"], "expected at least one chunk for 'skills api'"
        assert payload["chunks"][0]["evidence_title"] == "Skills API"


async def test_wire_protocol_get_trace_roundtrip(
    orc_home: Path, tmp_path: Path
) -> None:
    """Two tool calls: first search creates a trace, second tool retrieves it
    by run_id. Exercises the full JSON-RPC request/response/serialization path
    for both args and large structured results."""
    from mcp.shared.memory import create_connected_server_and_client_session

    name = _setup_corpus(orc_home, tmp_path)
    server = build_server()
    async with create_connected_server_and_client_session(server) as client:
        search = await client.call_tool(
            "orc_search_evidence",
            {"query": "skills api", "workspace": name, "k": 2},
        )
        search_payload = _extract_text_payload(search)
        run_id = search_payload["run_id"]

        trace = await client.call_tool("orc_get_trace", {"run_id": run_id})
        trace_payload = _extract_text_payload(trace)
        assert trace_payload["run_id"] == run_id
        assert trace_payload["skill"] == "search_evidence"
        # Wire path must preserve the freshly-added effective_kwargs field.
        assert trace_payload.get("effective_kwargs") is not None
        assert trace_payload["effective_kwargs"]["query"] == "skills api"


async def test_wire_protocol_unknown_workspace_returns_error_payload(
    orc_home: Path,
) -> None:
    """Application-level errors come back as JSON payloads with an `error` key,
    not protocol-level exceptions. The MCP client should still see isError=False
    (the tool ran successfully) and the error string lives inside the result."""
    from mcp.shared.memory import create_connected_server_and_client_session

    ws_module.create("placeholder")  # so workspaces dir exists
    server = build_server()
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool(
            "orc_search_evidence", {"query": "x", "workspace": "does-not-exist"}
        )
        payload = _extract_text_payload(result)
        assert "error" in payload
