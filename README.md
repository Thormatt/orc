# orc.

**The verification runtime for AI that has to be defensible.**

Bind every claim to evidence you own. Cite real sources only. Replay every decision. Built for workflows where *"the model said so"* isn't good enough.

`orc` is short for **orchestration** — the runtime spawns bounded sub-skills, never free-form agents.

---

## What is this

`orc` is a CLI + MCP server that runs LLM verification against a corpus you control. The architecture enforces four invariants by construction:

| | Invariant |
|---|---|
| **Citations** | The runtime *structurally cannot* return a citation that doesn't exist in retrieval. Hallucinated chunk IDs are dropped before the verdict reaches you. |
| **Architecture** | Skills are pure functions with explicit I/O contracts. No agent identities, no personas, no emergent coordination. Persistence lives in the workspace, not the agents. |
| **Replay** | Every call writes a trace: retrieval set, every LLM call's tokens and cache hits, the structured output. `orc replay <run_id>` re-executes the exact decision against the same corpus snapshot. |
| **Approval** | Anything that would mutate the outside world lands in the approval queue first. Write paths run as separate processes with separate tokens. Blast radius from a compromised agent is zero by design. |

Built for **research analysts, editorial teams, legal & compliance, agentic-workflow engineers** — anyone whose AI work product has to survive a second reviewer six months later.

## Quickstart

```bash
# Install
uv pip install git+https://github.com/thormatthiasson/orc

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

- **Stateless skills + durable context.** Skills are pure functions. Workspaces, evidence, runs, and traces persist; agents do not.
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

`v0.1.0` — first public release. The four-command loop (workspace / ingest / verify / replay) is stable and tested. MCP server stable. Approval queue and bounded-orchestration primitives shipped but the gads / marketing / legal directives that consume them are not yet released — those land in subsequent versions.

See [CHANGELOG.md](./CHANGELOG.md) for details.

## Development

```bash
git clone https://github.com/thormatthiasson/orc.git
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
