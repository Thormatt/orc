# Orc

Headless directive runtime for evidence-grounded research and claim verification.

Status: v0.1.0.

## What it is

Orc is a directive runtime you invoke from Claude Code, Codex, or directly via CLI. v1 ships one directive — `research` — that verifies claims and synthesises topics against a corpus *you* curate.

The architecture treats reasoning as stateless. Context, evidence, authority, and audit are persistent. Future directives (`marketing`, `code-review`, `db-doctor`) drop in as configs + code modules on the same runtime, without touching the surfaces.

## Install

```bash
uv sync --extra dev
export ANTHROPIC_API_KEY=sk-ant-...
orc --version
```

## The minimal loop

```bash
# 1. Create a workspace
orc workspace create demo

# 2. Ingest evidence (markdown, txt, urls; PDFs deferred to v1.x)
orc ingest ./my_notes -w demo
orc ingest https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents -w demo

# 3. Verify a claim against the corpus
orc verify "Anthropic released the Skills API in October 2025" -w demo

# 4. Inspect what just happened
orc trace list -w demo
orc trace show <run_id>

# 5. Re-run any previous run, frozen against its corpus snapshot
orc replay <run_id>
orc replay <run_id> --live  # against the current corpus
```

## Three behaviors that prove v1 works

These are the load-bearing acceptance tests for the verification loop.

**1. Supported claim.** Ingest a doc that affirms a fact, then verify the fact.

```bash
orc workspace create demo
echo "# Skills API\n\nAnthropic released the Skills API in October 2025." > /tmp/doc.md
orc ingest /tmp/doc.md -w demo
orc verify "Anthropic released the Skills API in October 2025" -w demo
# -> SUPPORTED, confidence ~0.9, supporting_chunk pointing back to /tmp/doc.md
```

**2. Cache hit.** Run the same verify a second time; the prompt-cache breakpoint over the corpus block should produce a cache read.

```bash
orc verify "Anthropic released the Skills API in October 2025" -w demo --json | jq '.model'
# Inspect the trace: trace.llm_calls[0].usage.cache_read_input_tokens > 0 on the second run.
orc trace list -w demo --skill verify_claim
orc trace show <latest_run_id>
```

**3. Not-found claim.** Ask about something not in the corpus.

```bash
orc verify "Orc was acquired by Microsoft for $1B" -w demo
# -> NOT_FOUND, both supporting and contradicting chunks empty,
#    missing_information explains what evidence would change the verdict.
```

## CLI reference

```
orc workspace create <name>                    Create a workspace
orc workspace list                             List workspaces
orc ingest <path-or-url> [-w <name>]           Add evidence (md, txt, json, url)
orc search "<query>" [-w <name>] [--k N]       Pure BM25 retrieval (no LLM)
orc verify "<claim>" [-w <name>] [--model X]   Verify a single claim
orc verify --file <path> [-w <name>] [-y]      Extract+verify all claims from a draft
orc verify --url <url>   [-w <name>] [-y]      Same, fetched from a URL
orc research "<topic>" [-w <name>]             Corpus-grounded synthesis with citations
orc trace show <run_id>                        Print the full trace JSON
orc trace list [-w <name>] [--skill name]      List recent runs
orc replay <run_id> [--live]                   Re-execute (frozen by default)
orc mcp serve                                  Start the MCP stdio server
```

Most commands accept `--json` for machine-readable output and `--workspace/-w` for explicit workspace selection.

## Use it from Claude Code via MCP

```bash
# Inside any directory
claude mcp add orc -- uv run --directory /path/to/orc orc mcp serve
```

The MCP server exposes four tools:

- `orc_verify_claim(claim, workspace="default")`
- `orc_search_evidence(query, workspace="default", k=10)`
- `orc_research_topic(topic, workspace="default")`
- `orc_get_trace(run_id)`

Every MCP tool call writes a trace, identical to the CLI path — so research you do inside Claude Code is replayable from the terminal.

## Architecture

```
~/.orc/
└── workspaces/<name>/
    ├── orc.db                       # workspace, evidence, chunks, runs, run_evidence + chunk_fts
    ├── evidence/<evidence_id>.<ext> # original source files, copied
    └── traces/<YYYY>/<MM>/<run_id>.json   # full per-run trace payloads
```

- **Stateless skills + durable context.** Skills are pure functions. Workspaces, evidence, runs, and traces persist; agents do not.
- **Verification bound to owned evidence.** `verify_claim` retrieves K=10 chunks via BM25 (FTS5), sends them in one LLM call with `cache_control: ephemeral` on the corpus block, and parses a structured verdict via tool use. Cited chunk IDs are validated against retrieval — hallucinated IDs are dropped.
- **Trace-and-replay from day one.** Every CLI/MCP call writes a `run` row + a JSON file that contains the full retrieval, every LLM call's usage (including `cache_read_input_tokens`), and the structured output. `orc replay` re-executes deterministically against the original corpus snapshot.
- **Directive registry.** `directives.get(name).skills[skill_name]` is the only dispatch path. Adding a new directive (`marketing`, `code-review`, …) is a `register(DirectiveSpec(...))` call plus a manifest YAML — no surface code changes.

## Configuration

| Variable | Default | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | LLM auth |
| `ORC_HOME` | `~/.orc` | Where workspaces live |
| `ORC_DEFAULT_WORKSPACE` | `default` | Workspace used when `-w` is omitted |
| `ORC_VERIFY_MODEL` | `claude-sonnet-4-6` | Override the verify model |
| `ORC_RESEARCH_MODEL` | `claude-sonnet-4-6` | Override the research-topic model |
| `ORC_EXTRACT_MODEL` | `claude-haiku-4-5` | Override the claim-extraction model |

Per-call overrides via CLI flags (`--model`, `--k`) take precedence over env vars and manifest defaults.

## Development

```bash
uv sync --extra dev
uv run pytest                                       # full unit suite
uv run pytest tests/golden/ -v                      # framework test (always runs)
ORC_TEST_ALLOW_LIVE_LLM=1 uv run pytest tests/golden/ -v  # real Anthropic calls (~$0.05)
uv run ruff check src tests
uv run ruff format src tests
```

Test layout:
- `tests/unit/` — fast, mocked LLM, ~85 tests
- `tests/golden/` — verify against `tests/fixtures/test_corpus/` + `claims.yaml`. The framework test always runs (uses fakes); the live-LLM threshold test is opt-in.
- `tests/e2e/` — MCP smoke

## What's deliberately not in v1

- HTTP API
- Web app
- Cloud / scheduled execution
- Autonomous publishing (assisted-only — you approve everything)
- Embedding-based retrieval (FTS5 BM25 only; embeddings are a v1.x add behind `--embeddings`)
- PDF ingestion (md, txt, json, urls work; PDF is v1.x)
- Multi-user workspaces

## Roadmap (post-v1)

- Voyage / local embeddings + hybrid retrieval (RRF over BM25 + vectors)
- Long-running directives (cron-scheduled runs, e.g. a marketing autopilot)
- A second directive: `marketing` (assisted drafting + claim review behind approval gates)
- PDF ingestion
- Web app for trace inspection and approval queues
