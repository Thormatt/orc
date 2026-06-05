"""MCP stdio server.

Exposes four tools:
  orc_verify_claim, orc_research_topic, orc_search_evidence, orc_get_trace.

All tools resolve a workspace, open a Run, dispatch through the directive registry,
and return JSON-friendly dicts. Every call writes a trace.

Workspace resolution: if the caller omits `workspace`, the env-aware default is used
(ORC_DEFAULT_WORKSPACE, falling back to the literal "default" workspace). If the
caller passes a workspace name explicitly — including the string "default" — that
exact name is used.

Use `orc mcp serve` to start the server.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from orc import directives
from orc.directives.research.routing import UnknownDomainError
from orc.errors import TraceNotFoundError, WorkspaceNotFoundError
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace


def _verify_claim_core(
    claim: str, workspace: str | None = None, domain: str | None = None
) -> dict[str, Any]:
    if not claim or not claim.strip():
        return {"error": "claim must be a non-empty string"}
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        return {"error": str(exc)}

    spec = directives.get("research")
    skill = spec.skills["verify_claim"]
    skill_kwargs = {**spec.kwargs_for("verify_claim"), "claim": claim}
    if domain is not None:
        skill_kwargs["domain"] = domain
    with open_run(
        ws,
        directive="research",
        skill="verify_claim",
        inputs={"claim": claim, "workspace": ws.name, "domain": domain},
    ) as run:
        run.record_effective_kwargs(skill_kwargs)
        try:
            result = skill.run(workspace=ws, run=run, **skill_kwargs)
        except UnknownDomainError as exc:
            return {"error": str(exc), "run_id": run.run_id}
        run.close(output=result)
    return {"run_id": run.run_id, **result}


def _search_evidence_core(
    query: str, workspace: str | None = None, k: int = 10
) -> dict[str, Any]:
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
        inputs={"query": query, "workspace": ws.name, "k": k},
    ) as run:
        run.record_effective_kwargs(skill_kwargs)
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)
    return {"run_id": run.run_id, **result}


def _research_topic_core(topic: str, workspace: str | None = None) -> dict[str, Any]:
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
        inputs={"topic": topic, "workspace": ws.name},
    ) as run:
        run.record_effective_kwargs(skill_kwargs)
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

    @mcp.tool(
        description=(
            "Verify a claim against the workspace's evidence corpus. "
            "Omit `workspace` to use ORC_DEFAULT_WORKSPACE (or the literal 'default' workspace). "
            "Optionally pass `domain` (e.g. 'pubmedQA', 'DROP') to route to an empirically "
            "best verify mode for that domain — see DOMAIN_TO_MODE in the runtime."
        )
    )
    def orc_verify_claim(
        claim: str, workspace: str | None = None, domain: str | None = None
    ) -> dict[str, Any]:
        return _verify_claim_core(claim, workspace, domain=domain)

    @mcp.tool(
        description=(
            "Return ranked evidence chunks for a query (no LLM synthesis). "
            "Use this when you want the raw retrieval, not a verdict. "
            "Omit `workspace` for the env-configured default."
        )
    )
    def orc_search_evidence(
        query: str, workspace: str | None = None, k: int = 10
    ) -> dict[str, Any]:
        return _search_evidence_core(query, workspace, k)

    @mcp.tool(
        description=(
            "Research a topic against the workspace, returning a synthesis with citations. "
            "Omit `workspace` for the env-configured default."
        )
    )
    def orc_research_topic(topic: str, workspace: str | None = None) -> dict[str, Any]:
        return _research_topic_core(topic, workspace)

    @mcp.tool(description="Retrieve a full trace JSON by run_id.")
    def orc_get_trace(run_id: str) -> dict[str, Any]:
        return _get_trace_core(run_id)

    return mcp


def serve_stdio() -> None:
    server = build_server()
    server.run()
