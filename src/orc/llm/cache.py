"""Prompt-cache assembly helpers.

Discipline that matters for cache hit rates:
- The corpus block is the variable-but-reusable part. Place it AFTER the small system rules
  with `cache_control: ephemeral`. The rules block is the (small) thing before the breakpoint.
- Render chunks in a deterministic order (by chunk_id), with no run-id or timestamp
  interpolation. A single byte change in the cached prefix collapses cache hit rate to zero.
- The claim varies every call and goes in the user message — after the breakpoint.
"""

from __future__ import annotations

import html
from typing import Any

from orc.retrieval import RetrievedChunk


def format_corpus(chunks: list[RetrievedChunk]) -> str:
    """Deterministic XML-like serialization of retrieved chunks. Sorted by chunk_id."""
    sorted_chunks = sorted(chunks, key=lambda c: c.chunk_id)
    parts: list[str] = ["<corpus>"]
    for c in sorted_chunks:
        title = html.escape(c.evidence_title or "", quote=True)
        headings = html.escape(c.headings_path or "", quote=True)
        parts.append(f'<chunk id="{c.chunk_id}" source="{title}" headings="{headings}">')
        parts.append(c.text.strip())
        parts.append("</chunk>")
    parts.append("</corpus>")
    return "\n".join(parts)


def build_verify_messages(
    *,
    system_prompt: str,
    corpus_block: str,
    claim: str,
) -> dict[str, Any]:
    """Returns the system+messages payload for client.messages.create()."""
    return {
        "system": [
            {"type": "text", "text": system_prompt},
            {
                "type": "text",
                "text": corpus_block,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [
            {"role": "user", "content": f"<claim>{claim}</claim>"},
        ],
    }
