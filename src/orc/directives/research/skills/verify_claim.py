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


def _load_system_prompt() -> str:
    return files("orc.llm.prompts").joinpath("verify_claim.md").read_text(encoding="utf-8")


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
        **_unused: Any,
    ) -> dict[str, Any]:
        if not claim or not claim.strip():
            raise ValueError("claim must be a non-empty string")

        resolved_model = resolve_verify_model(model)

        # 1. Retrieve. corpus_version pins the snapshot used by `orc replay` (frozen mode).
        candidates = bm25_search(
            run.conn, claim, limit=retrieval_pool, corpus_version=corpus_version
        )[:k]
        run.record_retrieval(candidates, method="bm25", candidates_considered=len(candidates))

        if not candidates:
            return _make_not_found(claim=claim, model=resolved_model, run=run)

        # 2. Build prompt with cache discipline.
        system_prompt = _load_system_prompt()
        corpus_block = format_corpus(candidates)
        payload = build_verify_messages(
            system_prompt=system_prompt, corpus_block=corpus_block, claim=claim
        )

        # 3. LLM call.
        anthropic_client = client or get_client()
        provider_model = resolve_model_for_provider(resolved_model)
        start = time.monotonic()
        response = messages_create(
            anthropic_client,
            model=provider_model,
            max_tokens=max_tokens,
            tools=[VERDICT_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "record_verdict"},
            **payload,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 4. Extract verdict from the tool_use block.
        tool_use = next(
            (
                b
                for b in response.content
                if getattr(b, "type", None) == "tool_use"
                and getattr(b, "name", None) == "record_verdict"
            ),
            None,
        )
        if tool_use is None:
            raise RuntimeError(
                "LLM did not call record_verdict; "
                f"stop_reason={getattr(response, 'stop_reason', None)!r}"
            )
        verdict_input = dict(tool_use.input)
        candidate_ids = {c.chunk_id for c in candidates}

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
                "tool_name": "record_verdict",
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
