"""Run context manager. Every CLI/MCP invocation goes through Run.

A Run owns:
- the workspace db connection for the duration of the call,
- the trace JSON payload accumulated as the skill executes,
- finalization of the `run` index row + the trace JSON file on close.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from orc.core.clock import now_iso
from orc.core.ids import new_run_id
from orc.paths import workspace_db_path
from orc.retrieval import RetrievedChunk
from orc.runs.trace_schema import LATEST_TRACE_SCHEMA_VERSION
from orc.storage.db import open_connection, transaction
from orc.storage.trace_store import (
    finalize_run_row,
    insert_run_evidence,
    insert_run_row,
    write_trace_json,
)
from orc.storage.workspace import Workspace

TRACE_SCHEMA_VERSION = LATEST_TRACE_SCHEMA_VERSION


@dataclass
class _LLMCallSummary:
    call_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    elapsed_ms: int
    request: dict[str, Any]
    response: dict[str, Any]


@dataclass
class Run:
    run_id: str
    workspace: Workspace
    directive: str
    skill: str
    inputs: dict[str, Any]
    started_at: str
    corpus_version: int
    conn: sqlite3.Connection
    events: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[_LLMCallSummary] = field(default_factory=list)
    retrieval: dict[str, Any] | None = None
    model: str | None = None
    effective_kwargs: dict[str, Any] | None = None
    _ended: bool = False

    def record(self, key: str, value: Any) -> None:
        self.events.append({"at": now_iso(), "key": key, "value": value})

    def record_effective_kwargs(self, kwargs: dict[str, Any]) -> None:
        """Pin the skill kwargs that were actually used (manifest defaults merged with
        caller overrides). Replay reads this so a manifest change between original and
        replay does not silently re-run with new defaults.

        Stores a JSON-safe shallow copy: non-serializable values (e.g. an injected LLM
        client) are stringified so the trace remains loadable.
        """
        safe: dict[str, Any] = {}
        for k, v in kwargs.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = f"<non-serializable {type(v).__name__}>"
        self.effective_kwargs = safe

    def record_retrieval(
        self,
        chunks: list[RetrievedChunk],
        *,
        method: str,
        candidates_considered: int | None = None,
    ) -> None:
        with transaction(self.conn):
            for c in chunks:
                insert_run_evidence(
                    self.conn,
                    run_id=self.run_id,
                    chunk_id=c.chunk_id,
                    role="retrieved",
                    rank=c.rank,
                    score=c.bm25_score,
                )
        self.retrieval = {
            "method": method,
            "candidates_considered": candidates_considered
            if candidates_considered is not None
            else len(chunks),
            "returned": [c.to_summary() for c in chunks],
        }

    def record_supporting(self, chunk_ids: list[str]) -> None:
        with transaction(self.conn):
            for cid in chunk_ids:
                insert_run_evidence(
                    self.conn,
                    run_id=self.run_id,
                    chunk_id=cid,
                    role="supporting",
                    rank=None,
                    score=None,
                )

    def record_contradicting(self, chunk_ids: list[str]) -> None:
        with transaction(self.conn):
            for cid in chunk_ids:
                insert_run_evidence(
                    self.conn,
                    run_id=self.run_id,
                    chunk_id=cid,
                    role="contradicting",
                    rank=None,
                    score=None,
                )

    def record_llm_call(
        self,
        *,
        call_id: str,
        model: str,
        request: dict[str, Any],
        response: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        elapsed_ms: int = 0,
    ) -> None:
        self.llm_calls.append(
            _LLMCallSummary(
                call_id=call_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
                elapsed_ms=elapsed_ms,
                request=request,
                response=response,
            )
        )
        if self.model is None:
            self.model = model

    def close(
        self,
        *,
        output: dict[str, Any] | None = None,
        status: str = "ok",
        error_message: str | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        ended_at = now_iso()
        out = output or {}
        summary = _summarize_output(out)

        total_in = sum(c.input_tokens for c in self.llm_calls)
        total_out = sum(c.output_tokens for c in self.llm_calls)
        total_cache_read = sum(c.cache_read_input_tokens for c in self.llm_calls)
        total_cache_creation = sum(c.cache_creation_input_tokens for c in self.llm_calls)

        with transaction(self.conn):
            finalize_run_row(
                self.conn,
                run_id=self.run_id,
                ended_at=ended_at,
                status=status,
                model=self.model,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
                total_cache_read=total_cache_read,
                total_cache_creation=total_cache_creation,
                output_summary=summary,
                error_message=error_message,
            )

        payload = self.build_trace_payload(
            ended_at=ended_at, status=status, output=out, error_message=error_message
        )
        write_trace_json(self.workspace.name, self.run_id, self.started_at, payload)

    def build_trace_payload(
        self,
        *,
        ended_at: str,
        status: str,
        output: dict[str, Any],
        error_message: str | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "directive": self.directive,
            "skill": self.skill,
            "workspace": self.workspace.name,
            "corpus_version": self.corpus_version,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "status": status,
            "model": self.model,
            "inputs": self.inputs,
            "effective_kwargs": self.effective_kwargs,
            "events": self.events,
            "retrieval": self.retrieval,
            "llm_calls": [vars(c) for c in self.llm_calls],
            "output": output,
            "error_message": error_message,
        }


def _summarize_output(output: dict[str, Any]) -> str | None:
    if not output:
        return None
    s = json.dumps(output, default=str)
    if len(s) <= 240:
        return s
    return s[:237] + "..."


@contextmanager
def open_run(
    workspace: Workspace,
    *,
    directive: str,
    skill: str,
    inputs: dict[str, Any],
) -> Iterator[Run]:
    run_id = new_run_id()
    started_at = now_iso()
    db_path = workspace_db_path(workspace.name)

    with open_connection(db_path) as conn:
        with transaction(conn):
            insert_run_row(
                conn,
                run_id=run_id,
                directive=directive,
                skill=skill,
                workspace=workspace.name,
                corpus_version=workspace.corpus_version,
                started_at=started_at,
            )
        run = Run(
            run_id=run_id,
            workspace=workspace,
            directive=directive,
            skill=skill,
            inputs=inputs,
            started_at=started_at,
            corpus_version=workspace.corpus_version,
            conn=conn,
        )
        try:
            yield run
        except Exception as exc:
            run.close(output={}, status="error", error_message=str(exc))
            raise
        else:
            if not run._ended:
                run.close(output={})
