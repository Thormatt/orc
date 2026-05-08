# Verification

The `verify_claim` skill is the load-bearing component of the research directive.

## Pipeline

1. Retrieve K=10 chunks via BM25 (FTS5).
2. Optionally rerank with vector embeddings if the workspace has them.
3. Send all retrieved chunks in a single LLM call with prompt-cache discipline:
   the corpus block carries `cache_control: ephemeral` so it stays cached for
   the 5-minute window.
4. Parse a structured verdict via tool use — the `record_verdict` tool is
   declared as `tool_choice` to force exactly one structured output.

## Verdict labels

`supported`, `contradicted`, `partial`, `not_found`. The model is told to prefer
`not_found` over guessing when the corpus is silent on a claim.
