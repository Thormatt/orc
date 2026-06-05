# Changelog

All notable changes to `orc` are documented in this file.

Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Isolated write paths (Phase 1)** — the effect plane that makes the Approval
  invariant enforceable rather than aspirational (see
  `docs/design/0001-isolated-write-paths.md`):
  - `orc.effects` — a typed, schema-validated `Action` envelope; an executor
    registry with a **deny-by-default per-workspace allow-list**; a guarded
    `run_action` enforcing allow-list + params schema + required write credential;
    and the `fs.write_file` reference executor (sandboxed to `<workspace>/out/`).
  - `Run.propose(...)` — skills can *propose* an action (validated + policy-checked
    at enqueue time) but never execute one.
  - Approval **execution lifecycle** — `approval_execution` table with atomic
    leasing, `UNIQUE(idempotency_key)` effectively-once backstop, and
    retry→dead semantics (`lease_one`, `begin_execution`, `mark_executed`,
    `mark_failed`, `get_execution`).
  - **`orc execute <approval_id>`** — the separate effect-plane process that holds
    the write credentials (which the analysis plane never sees), refuses anything
    not human-approved, and records the outcome.
- **Isolated write paths (Phase 2)** — **`orc worker`**, the auto-drain daemon:
  `drain_once` executes every leasable approved action in a pass; failures back off
  (`next_retry_at`) so they retry on a later pass instead of spinning, and exhaust to
  `dead` after `--max-attempts`. `--once` drains a single pass (cron-friendly).

### Fixed (hardening)

- **Replay determinism** — LLM sampling is now pinned to `temperature=0` at the
  `messages_create` chokepoint, so `orc replay` re-issues the recorded decision
  rather than a fresh sample. (`src/orc/llm/client.py`)
- **Citation guard now covers prose and `partial`** — hallucinated chunk IDs are
  stripped from the free-text `reasoning`, not just the structured arrays, and a
  `partial` verdict with no valid grounding is downgraded to `not_found` like
  `supported`/`contradicted`. (`src/orc/directives/research/skills/verify_claim.py`)
- **Atomic writes for traces and ingested evidence** — both use write-temp +
  `os.replace`, so a crash mid-write can never leave a truncated trace or an
  evidence file orphaned without its DB row. (`trace_store.py`, `ingest/pipeline.py`)
- **Workspace path-traversal fix** — `resolve()` validates the workspace name
  before touching the filesystem and no longer echoes the resolved path in its
  error (closes a traversal/probe vector reachable from the MCP server).
  (`src/orc/storage/workspace.py`)
- **SSRF guard on URL ingestion** — `load_url` rejects non-HTTP(S) schemes and
  hosts resolving to private/loopback/link-local/reserved addresses, re-validating
  every redirect hop, with a response-size cap. (`src/orc/ingest/loaders.py`)
- **Decomposed-mode vote fix** — a `0.0`-confidence atom no longer counts as
  `0.5` (falsy-coalescing bug that could flip the majority verdict).
- **CJK retrieval** — single non-ASCII query tokens (e.g. a lone CJK ideograph)
  are kept instead of being dropped as noise. (`src/orc/retrieval/bm25.py`)
- README invariants reworded to match what the code enforces (approval-queue
  isolation flagged as roadmap, not yet implemented).

### Planned

- `gads` directive (Google Ads agentic analysis: lens-based decomposition,
  read-only MCP integration, evidence-bound recommendation verification).
- `orc eval consistency|perturb|retrieval|regression` reliability commands.
- Voyage-AI or local-`sentence-transformers` embeddings + hybrid retrieval (RRF over BM25 + vector).
- PDF ingestion.
- Hosted runtime (scheduled triggers, web dashboard, team workspaces).
- Decomposition + arithmetic combined for DROP-shaped multi-step claims.

## [0.1.4] — 2026-05-19

### Added

- **`mode="arithmetic"` on `verify_claim`** — multi-turn LLM loop with a
  safe AST-walking calculator (`src/orc/llm/tools/calculate.py`). The model
  can invoke the calculator mid-verification for numeric claims; every
  expression and its result is recorded in the trace. Targets
  FinanceBench-style claims where the answer is a derived value.
  **FinanceBench F1 climbed 0.736 → 0.916; aggregate F1 0.832 → 0.864**,
  above Patronus AI's Lynx-70B home-court F1 of 0.85 on the same
  HaluBench 504-item subsample.
- **`src/orc/llm/agentic.py`** — tool-agnostic multi-turn loop primitive
  with `on_tool_call` and `on_llm_call` callbacks for trace streaming.
- **`domain=` parameter on `verify_claim`** — promotes source-aware routing
  from benchmark-only into the runtime. `DOMAIN_TO_MODE` lives at
  `src/orc/directives/research/routing.py`. CLI: `orc verify --domain pubmedQA`.
  MCP: `orc_verify_claim(claim, domain="DROP")`. Unknown domains raise
  `UnknownDomainError` rather than falling through silently.
- **`--include-evidence` flag on `orc audit export`** — optional opt-in for
  self-contained bundles. Includes the workspace SQLite DB + every ingested
  evidence file under `workspace/`, so an external auditor can extract the
  subtree and run `orc replay` against it with no access to the operator's
  infra. Manifest gains a `self_contained: bool` field.
- **`mode="decomposed"`** — Haiku decomposes a claim into 1–4 atoms, each
  verified in binary mode against the same staged passage, then aggregated
  by confidence-weighted majority vote. Available as an opt-in mode; not
  default-routed (binary outperformed it on HaluBench DROP at full N).

### Changed

- **Citation guard for evidence mode** — a `supported` verdict can no
  longer ship with zero valid citations after hallucinated chunk IDs are
  filtered. The label downgrades to `not_found` and the dropped IDs land
  in the trace as `dropped_chunk_ids` + `label_downgraded: true`.
- **Binary mode `faithful=false` mapping** — now maps to `not_found`
  rather than `contradicted`. The binary prompt defines unfaithful as
  *either* contradiction or corpus silence; conflating both into
  `contradicted` was wrong for audit consumers. Use `evidence` mode if
  the two must be distinguished.
- **Audit-export README in the bundle** is conditional: strong
  "self-contained" framing when `--include-evidence` was used, softened
  "inspectable handoff" framing otherwise.
- **Manifest hashing covers `README.md`** in the bundle — the integrity
  claim ("every file is hashed") was previously false because the
  bundle's README was added after the hashing loop ran.

## [0.1.2] — 2026-05-18

### Added

- **`orc audit export`** — bundle a workspace's full audit trail into a
  single tar.gz for regulator, auditor, or customer handoff. Includes the
  run table, every trace JSON (schema-validated on the way out), evidence
  manifest with sha256, approval queue with per-approver decisions,
  workspace metadata, and runtime version info. `manifest.json` carries
  sha256 for every file in the bundle so integrity can be verified
  independently. Range filters via `--from` / `--to` on `started_at`.
  Satisfies EU AI Act Article 12 (record-keeping) and Article 26(6) (log
  retention) end-to-end.
- **Trace JSON schema versioning** (`orc.runs.trace_schema`). Explicit
  `LATEST_TRACE_SCHEMA_VERSION`, `SUPPORTED_TRACE_SCHEMA_VERSIONS`, and
  `assert_supported()` check. Trace schema bumped to **v2** to mark the
  addition of `effective_kwargs`. Replay and audit export refuse traces
  with unsupported versions, returning an actionable error that names the
  offending version and the supported range.
- **`Run.record_effective_kwargs`** + `effective_kwargs` field in trace
  JSON, capturing the kwargs the skill was actually invoked with (manifest
  defaults merged with caller overrides). Pinned at write time so a
  manifest change between original and replay can no longer silently shift
  model / max_tokens / k.
- **In-process MCP wire-protocol tests** via
  `mcp.shared.memory.create_connected_server_and_client_session`. Exercise
  `list_tools` / `call_tool` over real JSON-RPC against the FastMCP server,
  catching breaks in tool-schema generation and result encoding that the
  function-level tests miss.

### Changed

- `orc replay` output surfaces `kwargs_source` (green
  `effective_kwargs` for pinned snapshot vs yellow `legacy_inputs` for
  best-effort reconstruction) and `original_schema_version` so an auditor
  can see which path ran without inspecting the trace JSON.
- All seven skill-invocation sites (CLI verify ×3, research, search,
  MCP ×3, Workflow) now call `record_effective_kwargs` before executing.

### Fixed

- **Workflow child-trace linkage on failure.** A failing step inside
  `orc.orchestrate.Workflow` now records the child's `run_id` on the
  `StepResult` so the parent trace still points at the error trace
  `open_run()` wrote on the way out. Previously the linkage was dropped
  when the skill raised and audit-chain reconstruction broke for failed
  steps.
- **Fanout `_step_index` uniqueness.** `Workflow.fanout` pre-allocates
  indices before scheduling, so parallel children no longer all read the
  same `len(self.results)` and write identical indexes into their traces.
- **Replay pins effective kwargs over current manifest defaults.** Legacy
  v1 traces still replay via best-effort reconstruction with `_*`
  metadata keys stripped from the kwargs passed to the skill.
- **Landing page credibility.** Replaced `v0.4.2` reference with the real
  version, dropped the fabricated `arxiv 2402.xxxxx` citation (now marked
  as an illustrative composite), and rewrote the topbar replay button so
  it copies the real `orc replay <run_id>` command instead of faking a
  "✓ identical" verification result.

### Compliance

- EU AI Act Articles 12 (record-keeping) and 26(6) (log retention ≥ 6
  months): satisfied end-to-end via `orc audit export` producing a hashed,
  schema-validated bundle that a third party can read without access to
  the live system. `docs/compliance/eu-ai-act.md` updated; compliance
  trace verdict counters on the live site read **6 satisfied · 0 partial ·
  0 open**.

## [0.1.1] — 2026-05-18

### Added

- **Multi-approver workflow** in the approval queue, addressing EU AI Act
  Article 14 §5 (some Annex III systems require verification by two natural
  persons). Approvals can be enqueued with `approvers_required=N`; each
  decision is recorded with the natural person's name in
  `approval_decision`, with a `UNIQUE (approval_id, decided_by)` constraint
  preventing double-voting. Status flips to *approved* only when N distinct
  acceptances are recorded; any single rejection blocks immediately. Full
  per-decision audit trail preserved.
- `DuplicateApproverError` raised when the same person tries to record a
  second decision on the same approval.
- CLI: `orc approve list` now shows an `approvers` column (e.g. `1/2`); `orc
  approve show` includes the full decisions array; `orc approve accept/reject`
  surfaces the progress (`accepted by alice · progress 1/2 · still pending:
  1 more approver(s) required`).

### Changed

- `decided_by` is now required on `accept()` and `reject()` (raises
  `ValueError` if omitted/empty). The regulation requires named natural
  persons; the module enforces it.
- `Approval` dataclass gains `approvers_required: int` and
  `decisions: list[Decision]`. Backward-compatible: existing single-approver
  flows default to `approvers_required=1`.

### Compliance

- EU AI Act Article 14 §5 (two-natural-persons verification): now satisfied.
  Updated `docs/compliance/eu-ai-act.md` and `/compliance` on the live site.
  Compliance trace verdict counters now read **5 satisfied · 0 partial · 0 open**.

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
