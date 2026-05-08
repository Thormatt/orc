"""MCP stdio server.

Exposes four tools:
  orc_verify_claim, orc_research_topic, orc_search_evidence, orc_get_trace.

All tools resolve a workspace, open a Run, dispatch through the directive registry,
and return JSON-friendly dicts. Every call writes a trace.

Use `orc mcp serve` to start the server.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from orc import directives
from orc.errors import TraceNotFoundError, WorkspaceNotFoundError
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace


def _verify_claim_core(claim: str, workspace: str = "default") -> dict[str, Any]:
    if not claim or not claim.strip():
        return {"error": "claim must be a non-empty string"}
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        return {"error": str(exc)}

    spec = directives.get("research")
    skill = spec.skills["verify_claim"]
    skill_kwargs = {**spec.kwargs_for("verify_claim"), "claim": claim}
    with open_run(
        ws,
        directive="research",
        skill="verify_claim",
        inputs={"claim": claim, "workspace": workspace},
    ) as run:
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)
    return {"run_id": run.run_id, **result}


def _search_evidence_core(query: str, workspace: str = "default", k: int = 10) -> dict[str, Any]:
    if not query or not query.strip():
        return {"error": "query must be a non-empty string"}
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        return {"error": str(exc)}

    spec = directives.get("research")
    skill = spec.skills["search_evidence"]
    skill_kwargs = {**spec.kwargs_for("search_evidence"), "query": query, "k": k}
    with open_run(
        ws,
        directive="research",
        skill="search_evidence",
        inputs={"query": query, "workspace": workspace, "k": k},
    ) as run:
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)
    return {"run_id": run.run_id, **result}


def _research_topic_core(topic: str, workspace: str = "default") -> dict[str, Any]:
    if not topic or not topic.strip():
        return {"error": "topic must be a non-empty string"}
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        return {"error": str(exc)}

    spec = directives.get("research")
    skill = spec.skills["research_topic"]
    skill_kwargs = {**spec.kwargs_for("research_topic"), "topic": topic}
    with open_run(
        ws,
        directive="research",
        skill="research_topic",
        inputs={"topic": topic, "workspace": workspace},
    ) as run:
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)
    return {"run_id": run.run_id, **result}


def _get_trace_core(run_id: str) -> dict[str, Any]:
    try:
        return load_trace(run_id)
    except TraceNotFoundError as exc:
        return {"error": str(exc)}


def build_server() -> FastMCP:
    """Construct the MCP server. Importing alone does not auto-register tools."""
    mcp = FastMCP("orc")

    @mcp.tool(description="Verify a claim against the workspace's evidence corpus.")
    def orc_verify_claim(claim: str, workspace: str = "default") -> dict[str, Any]:
        return _verify_claim_core(claim, workspace)

    @mcp.tool(
        description=(
            "Return ranked evidence chunks for a query (no LLM synthesis). "
            "Use this when you want the raw retrieval, not a verdict."
        )
    )
    def orc_search_evidence(query: str, workspace: str = "default", k: int = 10) -> dict[str, Any]:
        return _search_evidence_core(query, workspace, k)

    @mcp.tool(
        description="Research a topic against the workspace, returning a synthesis with citations."
    )
    def orc_research_topic(topic: str, workspace: str = "default") -> dict[str, Any]:
        return _research_topic_core(topic, workspace)

    @mcp.tool(description="Retrieve a full trace JSON by run_id.")
    def orc_get_trace(run_id: str) -> dict[str, Any]:
        return _get_trace_core(run_id)

    return mcp


def serve_stdio() -> None:
    server = build_server()
    server.run()
