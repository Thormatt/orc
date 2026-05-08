# Demo: verify five critique claims against an Anthropic-engineering corpus

Hand this file to a Claude Code (or Codex) session that has Orc registered as an MCP server. The session will run the test end-to-end and report the verdicts.

## What this demonstrates

The exact loop Orc was built for: claims you've seen circulating in long critiques get retrieved against a corpus you trust, and Orc returns supported / contradicted / not_found verdicts with chunk-level citations. Cache hits on the second run prove the prompt-cache discipline.

## Prerequisites

- The Orc repo at `/Users/thormatthiasson/Documents/GitHub/orc/`.
- An LLM API key in the environment. (Currently the SDK is Anthropic-direct; an OpenRouter migration is planned.)
- Either of:
  - **Recommended (CLI):** `uv run --directory /Users/thormatthiasson/Documents/GitHub/orc orc <cmd>` works in any shell.
  - **MCP from Claude Code:** register once with
    ```bash
    claude mcp add orc --scope user -- uv run --directory /Users/thormatthiasson/Documents/GitHub/orc orc mcp serve
    ```
    then start a fresh Claude Code session so the `orc_*` tools load.

## Steps

### 1. Workspace + corpus

```bash
ORC=/Users/thormatthiasson/Documents/GitHub/orc
cd "$ORC"

uv run orc workspace create demo-critique
uv run orc ingest https://www.anthropic.com/engineering/multi-agent-research-system        -w demo-critique
uv run orc ingest https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents -w demo-critique
uv run orc ingest https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills -w demo-critique
uv run orc ingest https://www.anthropic.com/news/prompt-caching                            -w demo-critique
```

(If a URL 404s or returns HTML the loader can't decode cleanly, skip it. The other URLs still produce a usable corpus.)

### 2. Verify five claims

```bash
uv run orc verify "Anthropic's multi-agent research system improved internal evals by 90.2%" -w demo-critique
uv run orc verify "Anthropic prompt caching has a 5-minute ephemeral TTL by default"          -w demo-critique
uv run orc verify "Skills are versioned, auditable capabilities Claude composes at runtime"   -w demo-critique
uv run orc verify "Multi-agent setups always outperform single-agent setups on complex tasks" -w demo-critique
uv run orc verify "Anthropic was acquired by Microsoft in 2026"                                -w demo-critique
```

### 3. Confirm prompt cache is being hit

Re-run claim #2 immediately, then look at the trace:

```bash
uv run orc verify "Anthropic prompt caching has a 5-minute ephemeral TTL by default" -w demo-critique
uv run orc trace list -w demo-critique --skill verify_claim --limit 5
uv run orc trace show <most_recent_run_id> | jq '.llm_calls[0].cache_read_input_tokens'
```

The second run's `cache_read_input_tokens` should be > 0.

## Predicted verdicts

| # | Claim | Predicted | Reasoning |
|---|---|---|---|
| 1 | 90.2% multi-agent eval delta | **supported** | Anthropic's multi-agent research post does cite this number. |
| 2 | 5-min ephemeral cache TTL | **supported** | Stated in the prompt-caching docs. |
| 3 | Skills are versioned + auditable | **supported** | The Agent Skills post affirms this. |
| 4 | Multi-agent *always* outperforms single | **contradicted** or **partial** | The post is more nuanced; "always" is a strong word. |
| 5 | Anthropic acquired by Microsoft 2026 | **not_found** | Sanity-check guardrail — the corpus is silent. |

If actual verdicts disagree with predictions, the supporting_chunks tell you why. That's the point — Orc shows its work.

## What you're looking at when this passes

- 5 verdicts produced, each with non-empty `reasoning`.
- Claims 1–3 cite at least one `supporting_chunks` entry from the matching blog post.
- Claim 5 returns `not_found` with empty supporting/contradicting and a populated `missing_information`.
- The second run of claim 2 shows `cache_read_input_tokens > 0`.
- `~/.orc/workspaces/demo-critique/traces/2026/05/<run_id>.json` exists for every run, with full retrieval + LLM-call payload + structured output.

## Common things to try after

```bash
# Inspect which evidence Orc considered for any run:
uv run orc trace show <run_id> | jq '.retrieval.returned[].evidence_title'

# Re-run a verification against the same corpus snapshot (frozen replay):
uv run orc replay <run_id>

# Or against the current corpus (live replay) to check whether new evidence changed the verdict:
uv run orc replay <run_id> --live

# Use search alone (no LLM, no cost) to sanity-check retrieval:
uv run orc search "multi-agent eval delta" -w demo-critique
```

## Cleanup

```bash
rm -rf ~/.orc/workspaces/demo-critique
```

## Reporting back

After the run, paste:
- The five verdict labels + confidences
- The cache_read_input_tokens from the repeated claim-2 run
- Any verdict that surprised you, with one supporting chunk

That's enough to confirm the loop works and to spot weak prompts or retrieval misses.
