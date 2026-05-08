"""`research_topic` skill — corpus-grounded topic synthesis."""

from __future__ import annotations

import time
from importlib.resources import files
from typing import Any

from orc.core.ids import new_id
from orc.llm.cache import build_verify_messages, format_corpus
from orc.llm.client import get_client, messages_create, resolve_model_for_provider
from orc.llm.models import resolve_research_model
from orc.retrieval import bm25_search
from orc.runs.runner import Run
from orc.storage.workspace import Workspace

SYNTHESIS_TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_synthesis",
    "description": ("Record a topic synthesis grounded in the corpus, with chunk-level citations."),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2–4 short paragraphs surveying what the corpus says about the topic.",
            },
            "key_points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "point": {"type": "string"},
                        "supporting_chunk_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["point", "supporting_chunk_ids"],
                },
            },
            "gaps": {
                "type": "string",
                "description": "Questions about the topic the corpus does not address.",
            },
        },
        "required": ["summary", "key_points"],
    },
}


def _load_system_prompt() -> str:
    return files("orc.llm.prompts").joinpath("research_topic.md").read_text(encoding="utf-8")


class _ResearchTopic:
    name = "research_topic"

    def run(
        self,
        *,
        workspace: Workspace,
        run: Run,
        topic: str,
        model: str | None = None,
        k: int = 12,
        retrieval_pool: int = 60,
        max_tokens: int = 4096,
        client: Any = None,
        corpus_version: int | None = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        if not topic or not topic.strip():
            raise ValueError("topic must be a non-empty string")

        resolved_model = resolve_research_model(model)
        candidates = bm25_search(
            run.conn, topic, limit=retrieval_pool, corpus_version=corpus_version
        )[:k]
        run.record_retrieval(candidates, method="bm25", candidates_considered=len(candidates))

        if not candidates:
            return {
                "topic": topic,
                "summary": "Corpus is silent on this topic.",
                "key_points": [],
                "gaps": "All relevant evidence is missing.",
                "model": resolved_model,
                "retrieval_chunk_ids": [],
            }

        system_prompt = _load_system_prompt()
        corpus_block = format_corpus(candidates)
        payload = build_verify_messages(
            system_prompt=system_prompt, corpus_block=corpus_block, claim=topic
        )

        anthropic_client = client or get_client()
        provider_model = resolve_model_for_provider(resolved_model)
        start = time.monotonic()
        response = messages_create(
            anthropic_client,
            model=provider_model,
            max_tokens=max_tokens,
            tools=[SYNTHESIS_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "record_synthesis"},
            **payload,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        tool_use = next(
            (
                b
                for b in response.content
                if getattr(b, "type", None) == "tool_use"
                and getattr(b, "name", None) == "record_synthesis"
            ),
            None,
        )
        if tool_use is None:
            raise RuntimeError(
                "LLM did not call record_synthesis; "
                f"stop_reason={getattr(response, 'stop_reason', None)!r}"
            )
        synth = dict(tool_use.input)
        candidate_ids = {c.chunk_id for c in candidates}

        cleaned_points: list[dict[str, Any]] = []
        all_supporting: set[str] = set()
        for kp in synth.get("key_points", []) or []:
            ids = [cid for cid in kp.get("supporting_chunk_ids", []) if cid in candidate_ids]
            if not ids:
                continue
            cleaned_points.append({"point": kp.get("point", ""), "supporting_chunk_ids": ids})
            all_supporting.update(ids)

        usage = response.usage
        run.record_llm_call(
            call_id=new_id(),
            model=resolved_model,
            request={
                "tool_name": "record_synthesis",
                "max_tokens": max_tokens,
                "topic_chars": len(topic),
                "corpus_chunks": len(candidates),
            },
            response={
                "stop_reason": getattr(response, "stop_reason", None),
                "key_points": len(cleaned_points),
            },
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            elapsed_ms=elapsed_ms,
        )

        if all_supporting:
            run.record_supporting(sorted(all_supporting))

        return {
            "topic": topic,
            "summary": synth.get("summary", ""),
            "key_points": cleaned_points,
            "gaps": synth.get("gaps", "") or None,
            "model": resolved_model,
            "retrieval_chunk_ids": [c.chunk_id for c in candidates],
        }


research_topic = _ResearchTopic()
