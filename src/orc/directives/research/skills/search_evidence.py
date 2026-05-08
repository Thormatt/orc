"""`search_evidence` skill — pure retrieval, no LLM."""

from __future__ import annotations

from typing import Any

from orc.retrieval import bm25_search
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
        chunks = bm25_search(run.conn, query, limit=k, corpus_version=corpus_version)
        run.record_retrieval(chunks, method="bm25", candidates_considered=len(chunks))
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
