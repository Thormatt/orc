"""`verify_claim` skill — the load-bearing one.

Pipeline (per the plan):
  retrieve K chunks (BM25, optional vector rerank)
  -> ONE LLM call with corpus block prompt-cached
  -> structured verdict via tool use
  -> persist supporting/contradicting/retrieved relations + trace
"""

from __future__ import annotations

import time
from importlib.resources import files
from typing import Any

from orc.core.ids import new_id
from orc.llm.cache import build_verify_messages, format_corpus
from orc.llm.client import get_client, messages_create, resolve_model_for_provider
from orc.llm.models import resolve_verify_model
from orc.retrieval import bm25_search
from orc.runs.runner import Run
from orc.storage.workspace import Workspace

BINARY_VERDICT_TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_binary_verdict",
    "description": (
        "Record a binary faithfulness verdict for the claim. Use this when the "
        "caller has staged a single passage and asks whether the answer follows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "faithful": {
                "type": "boolean",
                "description": "True if the answer follows from the context, false otherwise.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence in the verdict, 0..1.",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief reasoning (≤ 3 sentences).",
            },
        },
        "required": ["faithful", "confidence", "reasoning"],
    },
}


VERDICT_TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_verdict",
    "description": (
        "Record the verification verdict for the claim, citing supporting and "
        "contradicting evidence chunks by their IDs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "enum": ["supported", "contradicted", "not_found", "partial"],
                "description": "Verdict label.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence in the verdict, 0..1.",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief reasoning, citing chunk IDs you used.",
            },
            "supporting_chunk_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Chunk IDs that support the claim. Empty if none.",
            },
            "contradicting_chunk_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Chunk IDs that contradict the claim. Empty if none.",
            },
            "missing_information": {
                "type": "string",
                "description": "What evidence would change the verdict, if any.",
            },
        },
        "required": [
            "label",
            "confidence",
            "reasoning",
            "supporting_chunk_ids",
            "contradicting_chunk_ids",
        ],
    },
}


_PROMPT_FILE = {
    "evidence": "verify_claim.md",
    "judgment": "verify_claim_judgment.md",
    "binary": "verify_claim_binary.md",
}


def _load_system_prompt(mode: str = "evidence") -> str:
    filename = _PROMPT_FILE.get(mode, "verify_claim.md")
    return files("orc.llm.prompts").joinpath(filename).read_text(encoding="utf-8")


def _select_all_chunks(
    conn: Any,
    *,
    corpus_version: int | None,
    evidence_id: str | None,
    limit: int,
) -> list[Any]:
    """Judgment-mode retrieval: return chunks without running a BM25 match.

    Returns chunks in deterministic order (evidence_id, seq) so the corpus block
    is byte-stable across calls — same property prompt-caching relies on for the
    evidence-mode path.

    `evidence_id` restricts to a single evidence record (the common case for
    judgment-mode callers who staged a specific passage). `None` returns all
    workspace chunks at or before `corpus_version`, bounded by `limit`.
    """
    from orc.retrieval.bm25 import RetrievedChunk

    sql = (
        "SELECT chunk.chunk_id AS chunk_id, chunk.evidence_id AS evidence_id, "
        "chunk.seq AS seq, chunk.text AS text, chunk.headings_path AS headings_path, "
        "chunk.token_count AS token_count, evidence.title AS evidence_title, "
        "evidence.source_path AS evidence_source_path "
        "FROM chunk JOIN evidence ON evidence.evidence_id = chunk.evidence_id "
    )
    clauses: list[str] = []
    params: list[Any] = []
    if corpus_version is not None:
        clauses.append("evidence.corpus_version <= ?")
        params.append(corpus_version)
    if evidence_id is not None:
        clauses.append("chunk.evidence_id = ?")
        params.append(evidence_id)
    if clauses:
        sql += "WHERE " + " AND ".join(clauses) + " "
    sql += "ORDER BY chunk.evidence_id ASC, chunk.seq ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            evidence_id=row["evidence_id"],
            seq=row["seq"],
            text=row["text"],
            headings_path=row["headings_path"],
            token_count=row["token_count"],
            rank=i,
            bm25_score=0.0,  # No BM25 score in judgment mode.
            evidence_title=row["evidence_title"],
            evidence_source_path=row["evidence_source_path"],
        )
        for i, row in enumerate(rows)
    ]


class _VerifyClaim:
    name = "verify_claim"

    def run(
        self,
        *,
        workspace: Workspace,
        run: Run,
        claim: str,
        model: str | None = None,
        k: int = 10,
        retrieval_pool: int = 50,
        max_tokens: int = 2048,
        client: Any = None,
        corpus_version: int | None = None,
        mode: str = "evidence",
        evidence_id: str | None = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        """Verify a claim against the workspace's corpus.

        Modes:
          - "evidence" (default): BM25 retrieval over the workspace + structured
            4-label adjudication. The right call when the claim must be verified
            against a curated corpus and chunk-level citations matter.
          - "judgment": skip BM25 — use all workspace chunks (or chunks for a
            single `evidence_id` if provided) and a lighter binary-leaning
            prompt. Still produces a 4-label structured verdict with chunk
            citations. The right call when the caller has pre-staged the
            relevant passage and the question is "is this internally consistent."
          - "binary": skip BM25 and emit a simple faithful/unfaithful verdict
            via the `record_binary_verdict` tool. Citations not enforced at
            the per-claim level (the audit trail still records every input
            chunk via `record_retrieval`). Best F1 on tabular/numeric tasks
            where the 4-label structure adds noise.
        """
        if not claim or not claim.strip():
            raise ValueError("claim must be a non-empty string")
        if mode not in {"evidence", "judgment", "binary"}:
            raise ValueError(f"unknown verify mode: {mode!r}")

        resolved_model = resolve_verify_model(model)

        # 1. Retrieve. corpus_version pins the snapshot used by `orc replay` (frozen mode).
        if mode in {"judgment", "binary"}:
            candidates = _select_all_chunks(
                run.conn,
                corpus_version=corpus_version,
                evidence_id=evidence_id,
                limit=max(k, retrieval_pool),
            )
            run.record_retrieval(
                candidates, method=f"{mode}_all", candidates_considered=len(candidates)
            )
        else:
            pool = bm25_search(
                run.conn, claim, limit=retrieval_pool, corpus_version=corpus_version
            )
            candidates = pool[:k]
            run.record_retrieval(candidates, method="bm25", candidates_considered=len(pool))

        if not candidates:
            return _make_not_found(claim=claim, model=resolved_model, run=run)

        # 2. Build prompt with cache discipline.
        system_prompt = _load_system_prompt(mode=mode)
        corpus_block = format_corpus(candidates)
        payload = build_verify_messages(
            system_prompt=system_prompt, corpus_block=corpus_block, claim=claim
        )

        # 3. LLM call. Binary mode uses a different tool schema.
        tool_schema = BINARY_VERDICT_TOOL_SCHEMA if mode == "binary" else VERDICT_TOOL_SCHEMA
        tool_name = tool_schema["name"]
        anthropic_client = client or get_client()
        provider_model = resolve_model_for_provider(resolved_model)
        start = time.monotonic()
        response = messages_create(
            anthropic_client,
            model=provider_model,
            max_tokens=max_tokens,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_name},
            **payload,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 4. Extract verdict from the tool_use block.
        tool_use = next(
            (
                b
                for b in response.content
                if getattr(b, "type", None) == "tool_use"
                and getattr(b, "name", None) == tool_name
            ),
            None,
        )
        if tool_use is None:
            raise RuntimeError(
                f"LLM did not call {tool_name}; "
                f"stop_reason={getattr(response, 'stop_reason', None)!r}"
            )
        verdict_input = dict(tool_use.input)
        candidate_ids = {c.chunk_id for c in candidates}

        if mode == "binary":
            # Map the boolean back into the 4-label vocabulary so downstream
            # callers and the trace format stay uniform. Citations not enforced.
            faithful = bool(verdict_input.get("faithful"))
            verdict_input = {
                "label": "supported" if faithful else "contradicted",
                "confidence": float(verdict_input.get("confidence", 1.0)),
                "reasoning": verdict_input.get("reasoning", ""),
                "supporting_chunk_ids": [],
                "contradicting_chunk_ids": [],
                "missing_information": "",
            }

        supporting = [
            cid for cid in verdict_input.get("supporting_chunk_ids", []) if cid in candidate_ids
        ]
        contradicting = [
            cid for cid in verdict_input.get("contradicting_chunk_ids", []) if cid in candidate_ids
        ]
        # Drop hallucinated IDs silently — the run_evidence FK relies on real chunk IDs.

        # 5. Record LLM call usage.
        usage = response.usage
        run.record_llm_call(
            call_id=new_id(),
            model=resolved_model,
            request={
                "tool_name": tool_name,
                "mode": mode,
                "max_tokens": max_tokens,
                "system_blocks": 2,
                "claim_chars": len(claim),
                "corpus_chunks": len(candidates),
            },
            response={
                "stop_reason": getattr(response, "stop_reason", None),
                "tool_input_keys": sorted(verdict_input.keys()),
            },
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            elapsed_ms=elapsed_ms,
        )

        if supporting:
            run.record_supporting(supporting)
        if contradicting:
            run.record_contradicting(contradicting)

        return {
            "claim": claim,
            "label": verdict_input["label"],
            "confidence": float(verdict_input["confidence"]),
            "reasoning": verdict_input["reasoning"],
            "supporting_chunks": [_chunk_view(c) for c in candidates if c.chunk_id in supporting],
            "contradicting_chunks": [
                _chunk_view(c) for c in candidates if c.chunk_id in contradicting
            ],
            "missing_information": verdict_input.get("missing_information") or None,
            "model": resolved_model,
            "retrieval_chunk_ids": [c.chunk_id for c in candidates],
        }


def _make_not_found(*, claim: str, model: str, run: Run) -> dict[str, Any]:
    """Short-circuit when retrieval returns no chunks. No LLM call needed."""
    run.record(
        "skipped_llm",
        {"reason": "empty_retrieval", "claim_chars": len(claim)},
    )
    return {
        "claim": claim,
        "label": "not_found",
        "confidence": 1.0,
        "reasoning": "Corpus contains no chunks matching the claim's terms.",
        "supporting_chunks": [],
        "contradicting_chunks": [],
        "missing_information": "Any evidence relevant to the claim.",
        "model": model,
        "retrieval_chunk_ids": [],
    }


def _chunk_view(c) -> dict[str, Any]:
    return {
        "chunk_id": c.chunk_id,
        "evidence_id": c.evidence_id,
        "evidence_title": c.evidence_title,
        "evidence_source_path": c.evidence_source_path,
        "headings_path": c.headings_path,
        "text": c.text,
    }


verify_claim = _VerifyClaim()
