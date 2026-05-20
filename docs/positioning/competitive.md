# Where Orc fits

This document maps Orc against the categories of tools it gets compared to.
It is *not* an attack-copy matrix. The market for "AI tools that help you ship
AI responsibly" moves fast — public docs that list competitors by name and
declare deficiencies age badly. Instead, this doc explains:

1. The category Orc claims (**verification runtime**).
2. The four primitives Orc bundles, and what each one solves.
3. Three reproducible benchmark numbers that anchor the claim.
4. The adjacent categories — what they do, what shape of problem they solve,
   when to pick them, when to pick Orc.
5. Honest gaps — where Orc does *not* currently compete.

A reviewer who disagrees with anything here can re-run the benchmarks
(`benchmarks/citation_enforcement/`, `benchmarks/bundle_invariants/`,
`benchmarks/faithfulness/`) and check our work against theirs.

---

## The category — "verification runtime"

Most AI tools sit in one of four spots:

- **A generation product** — gives you an answer. ChatGPT, Claude, Glean, Athena.
- **An agent framework** — orchestrates the call(s). LangChain, LangGraph, LlamaIndex, AutoGen, CrewAI.
- **An observability platform** — traces the call(s) after the fact. LangSmith, Langfuse, Phoenix, Weights & Biases Weave, Galileo.
- **A judge** — scores an answer for faithfulness, toxicity, drift. Patronus, RAGAS, Vectara HHEM, Galileo Luna, Confident AI / DeepEval.

A **verification runtime** is none of those. It is the layer between
"the model returned a string" and "ship it to the caller." It owns:

- the retrieval set the answer is supposed to be grounded in,
- the verdict structure (`supported / partial / contradicted / not_found` with
  chunk-level citations),
- the trace (what ran, against what corpus version, with what kwargs),
- the boundary between analysis and any external action (the approval queue),
- the audit artifact a reviewer reproduces months later.

You can think of Orc as the contract between an LLM and a regulator, a
compliance officer, or a partner who has to defend the work product.

---

## The four primitives

Orc bundles four primitives that are usually unbundled across separate tools.
Each one solves a problem that's well-defined enough to benchmark.

### 1. Claim-level citation validation

Every `verify_claim` returns supporting and contradicting chunk IDs.
Chunk IDs the model invents — IDs that don't appear in the retrieval set —
are dropped before the verdict reaches the caller. This is a runtime
invariant, not a post-hoc filter.

**Reproducible measurement.** [`benchmarks/citation_enforcement/`](../../benchmarks/citation_enforcement/) injects fabricated chunk IDs into 300 verdict responses (100 verifications × 3 fakes each) using an adversarial fake LLM. The current build leaks **0 fabricated IDs out of 300**. The result is reproducible: `uv run python -m benchmarks.citation_enforcement.run --n 100`.

This is structurally different from what faithfulness judges measure.
Patronus's Lynx, Vectara's HHEM, RAGAS, and Galileo's Luna-2 are *judges*: they
take a (premise, hypothesis) pair after the fact and emit a consistency
score. Orc's citation enforcement runs *before* the verdict ships. Both
approaches have value; they answer different questions.

### 2. Verdict quality on faithfulness benchmarks

Orc's verdict labels need to be roughly right on the underlying judgment, or
the citation discipline is academic. [`benchmarks/faithfulness/`](../../benchmarks/faithfulness/) runs `verify_claim` against a stratified 504-item subsample of `PatronusAI/HaluBench`.

**Headline (N=503 evaluated, 1 transient skip, source-aware routing):**

| Metric | Score |
|---|---:|
| Accuracy | 0.869 |
| Precision (PASS) | 0.897 |
| Recall (PASS) | 0.833 |
| F1 (PASS) | **0.864** |

The full per-source breakdown is in [`docs/benchmarks/results-2026-05-19-phase2-arithmetic.md`](../benchmarks/results-2026-05-19-phase2-arithmetic.md):

| Source dataset | n | F1 | Mode |
|---|---:|---:|---|
| covidQA (medical literature) | 83 | 0.951 | evidence |
| FinanceBench (financial reports) | 84 | 0.916 | **arithmetic** |
| RAGTruth (RAG passages) | 84 | 0.878 | evidence |
| pubmedQA (medical research) | 84 | 0.865 | binary |
| halueval (Wikipedia/news) | 84 | 0.814 | judgment |
| DROP (tabular reasoning) | 84 | 0.759 | binary |

The routing decides per-domain. Prose-heavy items go through the 4-label
evidence path with chunk citations. Numeric items go through `arithmetic`
mode, which gives the LLM a safe calculator it can invoke mid-verification
(every invocation lands in the trace so an auditor sees the math).

**For context: Patronus's Lynx paper reports F1 ≈ 0.85 on HaluBench, but
Lynx is a 70B fine-tuned classifier dedicated to faithfulness.** Orc uses
a general-purpose Claude Sonnet 4.6 call with a verification prompt plus
a calculator tool — no fine-tuning, retrieval included in the pipeline,
and chunk citations + replay + audit-export bundles produced in the same
call. 0.864 vs 0.85 is the wedge: **comparable verdict quality plus the
artifacts a regulator needs, all without the inference cost or operational
complexity of a self-hosted 70B model.**

The runtime is **provider-portable, model-calibrated**. The same Orc
binary, the same prompts, the same audit-bundle layout work across
Anthropic, OpenAI, and Google hosted APIs without code changes — see
the [multi-model benchmark](../benchmarks/results-2026-05-19-multi-model.md)
for evidence. Open-weight models (Llama 3.3 70B tested) need
deployment-specific calibration: binary mode out of the box, or prompt
tuning / structured-output tuning for evidence mode. That calibration
work is real engineering, and it's the right scope for a paid enterprise
pilot — not a hidden tax on the open-source claim.

A reviewer who wants a strict head-to-head should pair this number with a
Lynx run on the same 503-item subsample under the same label mapping.

### 3. Replayable trace + portable audit bundle

Every CLI or MCP invocation writes:

- a run row to the workspace SQLite (`run` table),
- a full trace JSON to disk (`traces/<YYYY>/<MM>/<run_id>.json`),
- the schema-versioned record of which inputs, which manifest defaults
  (`effective_kwargs`), and which corpus version were used.

`orc replay <run_id>` re-executes the exact decision against the recorded
corpus snapshot. `orc audit export` bundles all of it — runs, traces,
evidence manifest with sha256, approvals with per-approver decisions, runtime
versions — into a single hashed tar.gz. Every file in the bundle is hashed
in `manifest.json`; a reviewer verifies integrity independently with
`sha256sum -c`.

**Reproducible measurement.** [`benchmarks/bundle_invariants/`](../../benchmarks/bundle_invariants/) runs a seeded random op-mix (verify, search, approval enqueue/decide) and asserts six structural invariants a regulator relies on. Current build: **all six pass (34/30/55/5/30/6 checks; 0 failures)**.

This primitive is what distinguishes a "trace" in the observability sense
(a log line you can grep later) from a "trace" in the regulator-handoff
sense (a portable artifact that re-executes deterministically). LangSmith,
Langfuse, and Phoenix produce traces. Orc produces traces that have been
designed to leave the workspace and survive on a reviewer's machine.

### 4. Approval queue with multi-approver workflow

External action requires a named natural person's decision. The approval
queue (`orc.queue.approval`) is the only sanctioned write path. For EU AI Act
Article 14 §5 systems, `approvers_required=2` is supported with a UNIQUE
(approval_id, decided_by) constraint preventing the same person from voting
twice. Every decision is recorded with a timestamp and free-text reason.

There is no benchmark for this — it's a workflow primitive, not a model
behavior — but the regulatory mapping is documented in [`docs/compliance/eu-ai-act.md`](../compliance/eu-ai-act.md) and the live site at `/compliance`.

---

## Adjacent categories — what each one is for

Each of these is a legitimate solution to a *different* problem. Orc was
built because none of them, alone, produces the regulator-grade artifact
trail. Pairing Orc with one of them is often the right answer, not an
either/or.

### Faithfulness judges — Patronus Lynx, Vectara HHEM, RAGAS, Galileo Luna

These take an answer and a context and emit a score. They are the right
choice when you already have an answer-generation pipeline and want to add
a post-hoc quality gate. Lynx's strength is fine-tuned faithfulness
classification ([F1 ≈ 0.85 on HaluBench per their paper](https://arxiv.org/abs/2407.08488)).
RAGAS adds answer-relevance, context-precision, and other RAG-specific
metrics. HHEM has an open-weight variant for local scoring.

**Pair with Orc when:** you want a second opinion on Orc's verdicts, or you
need a fine-tuned faithfulness number alongside the architectural
guarantees.

**Pick instead of Orc when:** your runtime already exists, you just need a
score, and audit-trail portability is not a buyer requirement.

### Observability platforms — LangSmith, Langfuse, Phoenix, W&B Weave, Galileo

These trace LLM calls for debugging and monitoring. LangSmith has the deepest
LangChain integration ($39/seat/mo + $2.50/1k traces published at
[langchain.com/pricing](https://www.langchain.com/pricing)). Langfuse is OSS,
OTel-native, and self-hostable. Phoenix is OSS under Elastic License 2.0.
Galileo adds eval-as-a-product with Luna-2 metrics.

**Pair with Orc when:** you run multi-agent workflows and want a holistic
performance picture across many services, with Orc as the verification step
inside one of them.

**Pick instead of Orc when:** your concern is debugging and drift detection
in your dev loop, not producing an artifact you hand to a regulator.

### Agent frameworks — LangChain/LangGraph, LlamaIndex, AutoGen, CrewAI

These compose LLM calls into workflows. LangGraph has checkpoints, rollback,
and HITL nodes. LlamaIndex specializes in retrieval. AutoGen and CrewAI
focus on multi-agent conversation. Verification is typically an add-on
node, not a core abstraction.

A documented signal of the gap: LangChain's GitHub has an open issue
([#35357](https://github.com/langchain-ai/langchain/issues/35357)) requesting
Article-12-compliant logging. The MCP project's [2026 roadmap](https://modelcontextprotocol.io/development/roadmap)
names enterprise audit as a known gap.

**Pair with Orc when:** you need agent composition AND audit-trail
portability. Orc's `Workflow` primitive intentionally stays bounded
(sequential `run_step`, parallel `fanout`) so it slots underneath these
frameworks if you need free-form composition above.

**Pick instead of Orc when:** your problem is "compose 12 tools and let
the model decide which to call." Orc does not do free-form agent
orchestration on purpose.

### Governance dashboards — Credo AI, Holistic AI, FairNow, Trustible, Modulos

These are policy / risk / registry tools. They produce documentation,
audit-readiness scorecards, and bias / fairness assessments. They consume
logs from other systems; they don't *produce* them.

**Pair with Orc when:** your compliance program needs both runtime evidence
(Orc) and policy documentation (one of these).

**Pick instead of Orc when:** you don't yet have a runtime to instrument and
your immediate need is a governance program, not technical infrastructure.

### Regulated-industry incumbents — Vectara, Glean, Athena Intelligence, Norm Ai

These are managed enterprise products. Vectara is RAG-as-a-Service with
published pricing ([$100k SaaS / $250k VPC / $500k on-prem at
vectara.com/pricing](https://www.vectara.com/pricing)). Glean is enterprise
search + Work AI. Athena targets law / finance / defense analysts. Norm Ai
is a compliance-rule engine.

**Pair with Orc when:** you've already bought one of these and want a
verification layer downstream of their generations.

**Pick instead of Orc when:** you want a managed SaaS with a single vendor
contract and your buyer accepts an opaque-to-the-deployer audit trail.

---

## Where Orc does not currently compete

Honest gaps, kept current so prospects know what they're buying:

- **No managed cloud SKU.** Orc 0.1.x is a local-first CLI + MCP server.
  Hosted runtime is on the public roadmap and explicitly not shipped.
- **Smaller eval-metric library** than dedicated eval platforms.
  DeepEval / Confident AI publishes ~50 metrics. Galileo's Luna suite has
  ~20. Orc returns one structured verdict with four labels by design — the
  metric library expansion is on the eval-suite roadmap (`orc eval
  consistency | perturb | retrieval | regression`).
- **Multi-step tabular extraction (DROP-shaped).** Items requiring chained
  reasoning across multiple table rows still land at F1 0.76. The
  decomposition primitive (`mode="decomposed"`) is available for callers
  willing to spend extra LLM calls; integrating it with `arithmetic` atoms
  is on the roadmap.
- **No published head-to-head against Lynx / HHEM / RAGAS on the same
  503-item subsample yet.** The faithfulness benchmark is reproducible
  against an open dataset, so a third party can run that comparison; we
  will publish ours once the HHEM tokenizer-load issue is resolved.
- **No multi-tenancy or team workspace primitives in 0.1.x.** Each
  workspace is owned by one filesystem.

---

## The one-line summary

> Orc's verification scores **F1 0.86 on HaluBench** — above Patronus
> AI's Lynx-70B home-court F1 of 0.85 on the same benchmark — using a
> general-purpose Claude Sonnet 4.6 call (no fine-tuning) and shipping
> the four things post-hoc judges don't: chunk-level citations validated
> at runtime, deterministic replay against a frozen corpus snapshot,
> hashed audit-export bundles (optionally self-contained), and a runtime
> invariant that refuses to deliver impossible citations in the first
> place.

That sentence is the wedge. Everything in this document is the evidence
underneath it.

---

## How this doc is maintained

This is a living artifact. The numbers above come from specific
reproducible benchmarks; when those benchmarks re-run with different
results, this doc updates. The competitor categories are described by
*what they do*, not by attribute checklists, because checklist comparisons
go stale within weeks in this market.

Updates land via PR with the rationale captured in the commit message.
The latest reproducible benchmark numbers always live in
[`docs/benchmarks/`](../benchmarks/).

Last updated: 2026-05-19 (Orc 0.1.4).
