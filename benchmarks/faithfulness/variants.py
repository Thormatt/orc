"""Experimental verification variants for the faithfulness benchmark.

These variants exist to *isolate variables* in the F1 gap between Orc and
specialized faithfulness judges (Lynx, HHEM, RAGAS). They do not change
Orc's production `verify_claim` skill.

Variants:

- `default`     — Orc's stock `verify_claim` with BM25 retrieval, 4-label
                  verdict, chunk-id validation, full trace. The number this
                  produces is what ships in production today.

- `lynx_style`  — Direct Claude Sonnet 4.6 call with Lynx's literal binary
                  prompt template (`Question / Context / Answer / YES|NO`).
                  No retrieval, no chunk-id validation, no structured
                  verdict, no Orc pipeline. This measures the *underlying
                  judge capability* of the model when given the optimal
                  prompt — the ceiling for a non-fine-tuned approach.

The gap between `default` and `lynx_style` tells us how much of the F1
distance to Lynx is attributable to Orc's full pipeline (retrieval +
structured output + 4-label adjudication) vs. the underlying model.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Lynx's evaluation prompt — adapted from arXiv:2407.08488 §4.2.
LYNX_PROMPT_TEMPLATE = (
    "Question: {question}\n"
    "Context: {context}\n"
    "Answer: {answer}\n"
    "Is the answer FAITHFUL to the context? Respond with YES or NO."
)


def run_lynx_style(
    item: dict[str, Any], *, model: str = "claude-sonnet-4-6"
) -> tuple[str, str, float | None, str | None]:
    """Direct LLM call with Lynx's prompt template.

    Returns (binary_label, raw_text, elapsed_seconds, error).
    `binary_label` is "PASS" / "FAIL" / "" (if unparseable).

    Faithful → PASS (consistent with the HaluBench label semantics).
    """
    from orc.llm.client import get_client, messages_create, resolve_model_for_provider
    from orc.llm.models import resolve_verify_model

    resolved = resolve_verify_model(model)
    provider_model = resolve_model_for_provider(resolved)

    prompt = LYNX_PROMPT_TEMPLATE.format(
        question=item["question"], context=item["passage"], answer=item["answer"]
    )

    client = get_client()
    start = time.monotonic()
    try:
        response = messages_create(
            client,
            model=provider_model,
            # Sonnet sometimes starts reasoning before answering. 64 tokens
            # is enough to swallow a short CoT prefix and still parse YES/NO.
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.monotonic() - start
    except Exception as exc:
        return ("", "", None, f"{type(exc).__name__}: {exc}")

    # Extract text. Anthropic SDK returns content as list[ContentBlock]; first
    # text block is what we want.
    raw_text = ""
    for b in getattr(response, "content", []) or []:
        if getattr(b, "type", None) == "text":
            raw_text = getattr(b, "text", "") or ""
            break

    label = _parse_yes_no(raw_text)
    return (label, raw_text, elapsed, None)


def _parse_yes_no(text: str) -> str:
    """Map a YES/NO model response to PASS/FAIL. Empty if neither word found."""
    t = text.strip().upper()
    # Match leading YES or NO, allowing punctuation/markdown noise.
    m = re.match(r"\s*(YES|NO)\b", t)
    if not m:
        # Fall back: if "YES" appears before "NO" in the text, treat as YES.
        yes_pos = t.find("YES")
        no_pos = t.find("NO")
        if yes_pos == -1 and no_pos == -1:
            return ""
        if yes_pos == -1:
            return "FAIL"
        if no_pos == -1:
            return "PASS"
        return "PASS" if yes_pos < no_pos else "FAIL"
    return "PASS" if m.group(1) == "YES" else "FAIL"
