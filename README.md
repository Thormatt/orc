# orc.

**The verification runtime for AI that has to be defensible.**

Bind every claim to evidence you own. Cite real sources only. Replay every decision. Built for workflows where *"the model said so"* isn't good enough.

`orc` is short for **orchestration** — the runtime spawns bounded sub-skills, never free-form agents.

---

## What is this

`orc` is a CLI + MCP server that runs LLM verification against a corpus you control. The architecture is built around four invariants:

| | Invariant |
|---|---|
| **Citations** | A verdict can only cite chunks that exist in the retrieval set. Hallucinated chunk IDs are filtered from both the structured citations *and* the free-text reasoning before the verdict reaches you, and a verdict left with no valid grounding is downgraded to `not_found`. |
| **Architecture** | Skills are stateless callables with explicit I/O contracts — no agent identities, no personas, no emergent coordination. Side effects are funneled through an injected run/workspace; persistence lives in the workspace, not the agents. |
| **Replay** | Every call writes a trace: retrieval set, every LLM call's tokens and cache hits, the structured output. LLM sampling is pinned to `temperature=0` and the corpus is pinned by version, so `orc replay <run_id>` re-issues the original decision against the same snapshot rather than a fresh sample (best-effort against residual model nondeterminism). |
| **Approval** | Anything that would mutate the outside world is routed to an approval queue first. Skills can only *propose* a typed, schema-validated, allow-listed action; a **separate process** holding the write credentials — which the analysis plane never sees — carries out human-approved actions and records the result, either one-shot (`orc execute`) or via the auto-drain daemon (`orc worker`, with leasing + idempotency + retry/backoff). *(Hosted row-level authz per plane is Phase 3; see [docs/design/0001-isolated-write-paths.md](docs/design/0001-isolated-write-paths.md).)* |

Built for **research analysts, editorial teams, legal & compliance, agentic-workflow engineers** — anyone whose AI work product has to survive a second reviewer six months later.

## Quickstart

```bash
# Install
uv pip install git+https://github.com/Thormatt/orc

# Or, once published to PyPI:
# uv pip install orc

# Set up credentials (either of these works; OpenRouter takes priority if both set)
export ANTHROPIC_API_KEY=sk-ant-...
export OPENROUTER_API_KEY=sk-or-...

# Create a workspace, ingest evidence, verify a claim
orc workspace create research
orc ingest ./notes -w research
orc ingest https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents -w research

orc verify "Anthropic's prompt caching has a 5-minute ephemeral TTL by default" -w research
# → SUPPORTED  confidence=0.88
#   chunk_id=01KR1MQ...  source: prompt-caching docs · §2 · p.3

# Or pipe a whole draft through
orc verify --file draft.md -w research
# → 7 claims extracted. supported=4 partial=1 contradicted=1 not_found=1

# Inspect or replay any decision
orc trace show <run_id>
orc replay <run_id>           # frozen replay against the original corpus snapshot
orc replay <run_id> --live    # against the current corpus

# Expose as MCP for Claude Code / Codex
orc mcp serve
# Then in another shell:
claude mcp add orc -- uv run --directory $(pwd) orc mcp serve
```

## Commands

```
orc workspace create <name>            create a new workspace
orc workspace list                     list workspaces
orc ingest <path-or-url> [-w <name>]   add evidence (md, txt, urls)
orc search "<query>" [-w <name>]       BM25 retrieval, no LLM
orc verify "<claim>" [-w <name>]       verify a single claim
orc verify --file <path>               extract + verify every claim in a draft
orc verify --url <url>                 same, from a URL
orc research "<topic>" [-w <name>]     corpus-grounded synthesis with citations
orc trace show <run_id>                full trace JSON
orc trace list [-w <name>]             recent runs
orc replay <run_id> [--live]           re-execute a recorded run
orc approve list [-w <name>]           list pending approval items
orc approve accept <id> [--note]       accept a pending recommendation
orc approve reject <id> [--note]       reject one
orc mcp serve                          start the MCP stdio server
```

## Architecture

```
~/.orc/
└── workspaces/<name>/
    ├── orc.db                              workspace, evidence, chunks (FTS5), runs, run_evidence, approval
    ├── evidence/<evidence_id>.<ext>        original ingested files, copied
    └── traces/<YYYY>/<MM>/<run_id>.json    full per-run trace payloads
```

- **Stateless skills + durable context.** Skills are stateless callables — their side effects flow through an injected run/workspace, never module-level state. Workspaces, evidence, runs, and traces persist; agents do not.
- **Verification bound to owned evidence.** `verify_claim` retrieves K=10 chunks via BM25 (SQLite FTS5), sends them in one LLM call with `cache_control: ephemeral` on the corpus block, and parses a structured verdict via tool use.
- **Trace-and-replay from day one.** Every CLI/MCP call writes a `run` row + a JSON file containing the full retrieval, every LLM call's usage (including `cache_read_input_tokens`), and the structured output. `orc replay` re-executes against the corpus snapshot referenced by `corpus_version`.
- **Directive registry.** `directives.get(name).skills[skill_name]` is the only dispatch path. Adding a new directive (e.g. `marketing`, `legal`, `db-doctor`) is a `register(DirectiveSpec(...))` call + a manifest — no surface code changes.
- **Bounded orchestration.** `orc.orchestrate.Workflow` spawns sub-skills with explicit context budgets, in either sequential or bounded-parallel mode. No free-form inter-agent chat. Each step opens its own Run with its own trace.

## Configuration

| Variable | Default | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | LLM auth (direct path) |
| `OPENROUTER_API_KEY` | — | LLM auth (OpenRouter; auto-pinned to Anthropic upstream for cache fidelity) |
| `ORC_PROVIDER` | auto | `anthropic` \| `openrouter` (explicit override; auto picks whichever key is set) |
| `ORC_HOME` | `~/.orc` | Where workspaces live |
| `ORC_DEFAULT_WORKSPACE` | `default` | Workspace used when `-w` is omitted |
| `ORC_VERIFY_MODEL` | `claude-sonnet-4-6` | Override the verify model |
| `ORC_RESEARCH_MODEL` | `claude-sonnet-4-6` | Override the research-topic model |
| `ORC_EXTRACT_MODEL` | `claude-haiku-4-5` | Override the claim-extraction model |

A `.env` file in the repo root or at `$ORC_HOME/.env` is auto-loaded. Shell-exported vars take precedence over `.env`.

## Project status

`v0.1.4` — current. Faithfulness benchmark headline (HaluBench, stratified 504-item subsample, source-aware routing):

| Metric | Score |
|---|---:|
| **F1 (PASS)** | **0.864** |
| Precision | 0.897 |
| Recall | 0.833 |
| Accuracy | 0.869 |

> **0.864 sits above Patronus AI's Lynx-70B home-court F1 of 0.85** on the same benchmark — achieved with a general-purpose Claude Sonnet 4.6 call (no fine-tuning) plus a safe arithmetic evaluator the model can invoke for numeric claims. Orc additionally produces chunk-level citations, deterministic replay against a frozen corpus snapshot, audit-export bundles that can be self-contained (`--include-evidence`), and a multi-approver gate for high-risk verdicts. The competitive set of post-hoc faithfulness judges does not produce these artifacts.

What shipped in this version:

- `domain=` parameter on `verify_claim` + `--domain` CLI flag → source-aware routing is a real product feature, not a benchmark variant.
- `--include-evidence` flag on `orc audit export` → optional self-contained bundles (workspace DB + evidence files included) for offline regulator handoff.
- `mode="arithmetic"` for numeric claims — multi-turn LLM loop with a safe AST-walking calculator. FinanceBench F1 climbed 0.736 → 0.916.
- Citation guard: an evidence-mode verdict can no longer ship as `supported` with zero valid citations (downgraded to `not_found` and the dropped IDs land in the trace).
- Self-hosting any open-weight 70B judge: the runtime is model-agnostic — pass `model="llama-3.3-70b-instruct"` (or even Lynx itself) at any compatible endpoint and every artifact above is unchanged.

Live walkthrough: **[pagenta.app/p/thorm/orc-how-it-works](https://pagenta.app/p/thorm/orc-how-it-works)** — six-scene visual explainer. Full pitch: **[pagenta.app/p/thorm/orc-pitch](https://pagenta.app/p/thorm/orc-pitch)**.

Full per-source breakdown + reproducing instructions: [`docs/benchmarks/results-2026-05-19-phase2-arithmetic.md`](./docs/benchmarks/results-2026-05-19-phase2-arithmetic.md). Multi-model portability (Sonnet, Haiku, GPT-4o, Gemini Flash, Llama 3.3 70B): [`docs/benchmarks/results-2026-05-19-multi-model.md`](./docs/benchmarks/results-2026-05-19-multi-model.md). Competitive positioning: [`docs/positioning/competitive.md`](./docs/positioning/competitive.md). EU AI Act mapping: [`docs/compliance/eu-ai-act.md`](./docs/compliance/eu-ai-act.md). Business model + stage-by-stage roadmap: [`docs/business/roadmap.md`](./docs/business/roadmap.md). Cost economics across all tested models (Sonnet, Haiku, GPT-4o, Gemini Flash, Llama, Qwen, Gemma): [`docs/business/cost-economics.md`](./docs/business/cost-economics.md).

See [CHANGELOG.md](./CHANGELOG.md) for the full version history.

## Development

```bash
git clone https://github.com/Thormatt/orc.git
cd orc
uv sync --extra dev

uv run pytest                           # 115+ tests, 1s
uv run ruff check src tests
uv run orc --version
```

Live LLM tests are gated behind `ORC_TEST_ALLOW_LIVE_LLM=1` and require a real Anthropic or OpenRouter key. The default suite runs against a fake Anthropic client and costs nothing.

## Roadmap

- Embedding-based retrieval (hybrid BM25 + vector via `sqlite-vec`)
- PDF ingestion
- Long-running directives (scheduled triggers, cloud execution)
- `marketing` directive (assisted-only at first, autonomous behind approval gates later)
- `legal` / `gads` / `code-review` directives — same runtime, new skill packages
- `orc eval consistency` / `perturb` / `regression` — R(k,ε,λ) reliability instrumentation

## License

MIT. See [LICENSE](./LICENSE).

The CLI, runtime, and verification skills are open source and free forever. Hosted and Enterprise tiers (managed workspaces, scheduled runs, SSO, audit export, on-prem) are commercial — see [orc.run](https://orc.run) when it goes live.
