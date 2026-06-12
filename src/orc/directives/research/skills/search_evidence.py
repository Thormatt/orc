"""`search_evidence` skill — pure retrieval, no LLM."""

from __future__ import annotations

from typing import Any

from orc.retrieval import retrieve
from orc.runs.runner import Run
from orc.storage.workspace import Workspace


class _SearchEvidence:
    name = "search_evidence"

    def run(
        self,
        *,
        workspace: Workspace,
        run: Run,
        query: str,
        k: int = 10,
        corpus_version: int | None = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        res = retrieve(run.conn, query, workspace=workspace, limit=k, corpus_version=corpus_version)
        chunks = res.chunks
        run.record_retrieval(
            chunks, method=res.method, candidates_considered=res.candidates_considered
        )
        return {
            "query": query,
            "k": k,
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "evidence_id": c.evidence_id,
                    "evidence_title": c.evidence_title,
                    "evidence_source_path": c.evidence_source_path,
                    "headings_path": c.headings_path,
                    "rank": c.rank,
                    "bm25_score": c.bm25_score,
                    "token_count": c.token_count,
                    "text": c.text,
                }
                for c in chunks
            ],
        }


search_evidence = _SearchEvidence()
