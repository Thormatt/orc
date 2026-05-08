"""`extract_claims` skill — pulls factual claims out of a document via Haiku."""

from __future__ import annotations

import time
from importlib.resources import files
from typing import Any

from orc.core.ids import new_id
from orc.llm.client import get_client, messages_create, resolve_model_for_provider
from orc.llm.models import resolve_extract_model
from orc.runs.runner import Run
from orc.storage.workspace import Workspace

EXTRACT_CLAIMS_TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_claims",
    "description": "Record the list of distinct factual claims found in the document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The verifiable claim, paraphrased if needed.",
                        },
                        "context": {
                            "type": "string",
                            "description": "A short surrounding sentence from the source.",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        "required": ["claims"],
    },
}


def _load_system_prompt() -> str:
    return files("orc.llm.prompts").joinpath("extract_claims.md").read_text(encoding="utf-8")


class _ExtractClaims:
    name = "extract_claims"

    def run(
        self,
        *,
        workspace: Workspace,
        run: Run,
        document: str,
        model: str | None = None,
        max_tokens: int = 1024,
        client: Any = None,
        **_unused: Any,
    ) -> dict[str, Any]:
        if not document or not document.strip():
            return {"claims": []}
        if len(document) > 200_000:
            document = document[:200_000]  # rough character cap to bound token cost

        resolved_model = resolve_extract_model(model)
        anthropic_client = client or get_client()
        provider_model = resolve_model_for_provider(resolved_model)

        start = time.monotonic()
        response = messages_create(
            anthropic_client,
            model=provider_model,
            max_tokens=max_tokens,
            system=_load_system_prompt(),
            tools=[EXTRACT_CLAIMS_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "record_claims"},
            messages=[{"role": "user", "content": f"<document>\n{document}\n</document>"}],
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        tool_use = next(
            (
                b
                for b in response.content
                if getattr(b, "type", None) == "tool_use"
                and getattr(b, "name", None) == "record_claims"
            ),
            None,
        )
        if tool_use is None:
            raise RuntimeError(
                "LLM did not call record_claims; "
                f"stop_reason={getattr(response, 'stop_reason', None)!r}"
            )
        claims = list(tool_use.input.get("claims", []))

        usage = response.usage
        run.record_llm_call(
            call_id=new_id(),
            model=resolved_model,
            request={
                "tool_name": "record_claims",
                "max_tokens": max_tokens,
                "document_chars": len(document),
            },
            response={
                "stop_reason": getattr(response, "stop_reason", None),
                "claim_count": len(claims),
            },
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            elapsed_ms=elapsed_ms,
        )

        return {"claims": claims, "model": resolved_model}


extract_claims = _ExtractClaims()
