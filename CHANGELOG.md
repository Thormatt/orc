# Changelog

All notable changes to `orc` are documented in this file.

Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- `gads` directive (Google Ads agentic analysis: lens-based decomposition,
  read-only MCP integration, evidence-bound recommendation verification).
- `orc eval consistency|perturb|retrieval|regression` reliability commands.
- Voyage-AI or local-`sentence-transformers` embeddings + hybrid retrieval (RRF over BM25 + vector).
- PDF ingestion.
- Hosted runtime (scheduled triggers, web dashboard, team workspaces).

## [0.1.0] — 2026-05-13

First public release. The four-command loop is stable.

### Added

- **Directive runtime** with skill registry. Adding a new directive is dropping a
  package under `src/orc/directives/<name>/` with a manifest and skill modules.
- **`research` directive** with four skills:
  - `verify_claim` — single-claim verification with structured verdict + chunk-level provenance.
  - `search_evidence` — pure BM25 retrieval (no LLM).
  - `research_topic` — corpus-grounded synthesis with citations.
  - `extract_claims` — Haiku-driven extraction from a draft.
- **CLI** (`click`): `workspace`, `ingest`, `search`, `verify`, `research`,
  `trace show/list`, `replay`, `approve list/show/accept/reject`, `mcp serve`.
- **MCP stdio server** (`FastMCP`) exposing four read tools:
  `orc_verify_claim`, `orc_search_evidence`, `orc_research_topic`, `orc_get_trace`.
- **Trace + replay** as first-class. Every CLI / MCP call writes a `run` row +
  full-payload JSON. `orc replay` re-executes deterministically against the
  recorded `corpus_version`, or against the current corpus with `--live`.
- **Approval queue** (`orc.queue.approval`) — the boundary between analysis and
  external action. Pending → approved | rejected | expired (one-way).
- **Orchestration primitive** (`orc.orchestrate.Workflow`) — sequential
  `run_step` and bounded-parallel `fanout`. Each step opens its own Run; parent
  Run records the workflow shape. No free-form agent chat.
- **Hallucinated-citation defense.** Chunk IDs returned by the model are
  validated against the retrieval set; non-matching IDs are dropped before the
  verdict reaches the caller.
- **Prompt-cache discipline** (Anthropic `cache_control: ephemeral`). Corpus
  blocks rendered deterministically (sorted by `chunk_id`) so cache prefix is
  byte-stable across calls.
- **Per-workspace SQLite** (FTS5 + `sqlite-vec`-ready) under
  `~/.orc/workspaces/<name>/orc.db`. WAL mode; writes use `BEGIN IMMEDIATE`.
- **Dual-provider LLM client**: Anthropic SDK pointed at Anthropic's API
  directly, or routed through OpenRouter at `https://openrouter.ai/api` with
  automatic upstream pinning to Anthropic for cache fidelity. `.env` files at
  repo root or `$ORC_HOME` are auto-loaded.
- **Test suite**: 115+ tests, runs in ~1s against a fake Anthropic client.
  Golden tests against a curated `tests/fixtures/test_corpus/` are gated behind
  `ORC_TEST_ALLOW_LIVE_LLM=1` and require a real API key.

### Known limitations

- No PDF ingestion (markdown, text, json, URL only).
- No embeddings yet — BM25 only.
- No scheduled / long-running directives — on-demand only.
- The hosted and enterprise tiers described on the landing page are not yet
  available; only the open-source CLI ships today.
