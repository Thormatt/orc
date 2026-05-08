"""Trace persistence: index in the run table, full payload as JSON files.

Trace JSON layout is the contract — see schema_version=1 in `Run.build_trace_payload`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from orc.errors import TraceNotFoundError
from orc.paths import (
    trace_json_path,
    workspace_db_path,
    workspace_traces_dir,
)
from orc.storage.db import open_connection


def insert_run_row(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    directive: str,
    skill: str,
    workspace: str,
    corpus_version: int,
    started_at: str,
) -> None:
    conn.execute(
        "INSERT INTO run(run_id, directive, skill, workspace, corpus_version, started_at, status) "
        "VALUES (?,?,?,?,?,?, 'running')",
        (run_id, directive, skill, workspace, corpus_version, started_at),
    )


def finalize_run_row(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ended_at: str,
    status: str,
    model: str | None,
    total_input_tokens: int,
    total_output_tokens: int,
    total_cache_read: int,
    total_cache_creation: int,
    output_summary: str | None,
    error_message: str | None,
) -> None:
    conn.execute(
        "UPDATE run SET ended_at = ?, status = ?, model = ?, "
        "total_input_tokens = ?, total_output_tokens = ?, "
        "total_cache_read = ?, total_cache_creation = ?, "
        "output_summary = ?, error_message = ? WHERE run_id = ?",
        (
            ended_at,
            status,
            model,
            total_input_tokens,
            total_output_tokens,
            total_cache_read,
            total_cache_creation,
            output_summary,
            error_message,
            run_id,
        ),
    )


def insert_run_evidence(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    chunk_id: str,
    role: str,
    rank: int | None,
    score: float | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run_evidence(run_id, chunk_id, role, rank, score) "
        "VALUES (?,?,?,?,?)",
        (run_id, chunk_id, role, rank, score),
    )


def write_trace_json(workspace: str, run_id: str, started_at: str, payload: dict[str, Any]) -> Path:
    path = trace_json_path(workspace, run_id, started_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False))
    return path


def load_trace(run_id: str) -> dict[str, Any]:
    """Load a trace JSON by run_id. Searches all workspaces."""
    workspaces_dir = workspace_db_path("__placeholder__").parent.parent
    if not workspaces_dir.exists():
        raise TraceNotFoundError(f"No workspaces — trace {run_id!r} not found")
    for ws_dir in workspaces_dir.iterdir():
        traces_root = workspace_traces_dir(ws_dir.name)
        if not traces_root.exists():
            continue
        for path in traces_root.rglob(f"{run_id}.json"):
            return json.loads(path.read_text())
    raise TraceNotFoundError(f"Trace not found: {run_id}")


def find_trace_path(workspace: str, run_id: str) -> Path:
    traces_root = workspace_traces_dir(workspace)
    for path in traces_root.rglob(f"{run_id}.json"):
        return path
    raise TraceNotFoundError(f"Trace not found in workspace {workspace!r}: {run_id}")


def list_runs(
    workspace: str,
    *,
    skill: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db_path = workspace_db_path(workspace)
    if not db_path.exists():
        return []
    with open_connection(db_path) as conn:
        if skill is None:
            rows = conn.execute(
                "SELECT run_id, directive, skill, started_at, ended_at, status, model, "
                "total_input_tokens, total_output_tokens, output_summary "
                "FROM run ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT run_id, directive, skill, started_at, ended_at, status, model, "
                "total_input_tokens, total_output_tokens, output_summary "
                "FROM run WHERE skill = ? ORDER BY started_at DESC LIMIT ?",
                (skill, limit),
            ).fetchall()
    return [dict(r) for r in rows]
