# Cost economics — what does running Orc actually cost?

This is the most common buyer question after "does the runtime work."
The honest answer: **cost varies by 30–100× depending on which model you
point Orc at, and for most regulated-AI deployments the API cost is
small compared to the human review time it replaces.**

This doc lays out the math.

---

## Per-verification cost across the tested models

All numbers are approximate, taken from actual OpenRouter spend during
the multi-model benchmark on the 504-item HaluBench subsample. Token
counts assume the production workspace shape (10 retrieved chunks,
~3K-token corpus block, structured tool-call response).

| Model | Per-claim cost | Note |
|---|---:|---|
| Sonnet 4.6 (no cache) | $0.010 – 0.014 | Production headline F1 0.864 |
| Sonnet 4.6 (with prompt cache, steady state) | **$0.002 – 0.005** | Same F1; caching cuts repeated corpus reads 10× |
| GPT-4o | $0.008 – 0.012 | F1 0.761 |
| Haiku 4.5 | **$0.001 – 0.003** | F1 0.764 (only ~0.025 below Sonnet) |
| Gemini 3.5 Flash | **$0.0005 – 0.001** | **F1 0.876 on N=449** (max_tokens=4096) — the cost/quality winner of the field; 55 long-FB/DROP items still skipped ([details](../benchmarks/results-2026-05-19-multi-model.md)) |
| Llama 3.3 70B / Qwen 2.5 72B (open-weight via OpenRouter) | $0.001 – 0.003 | Binary mode only; F1 ~0.58–0.67 |
| Llama 3.3 70B / Qwen 2.5 72B (self-hosted) | $0 API + GPU rental | See below |

Three honest caveats:

1. **Prompt-cache discipline matters.** Orc's corpus block is cached
   when (a) running through direct Anthropic API or Anthropic-pinned
   OpenRouter and (b) the same chunks are retrieved across calls. In
   benchmark mode each item is a fresh workspace, so the benchmark
   under-counts the steady-state cost saving by ~5×.
2. **Non-Anthropic upstreams don't cache.** OpenAI / Google / open-
   weight all bill full input tokens every call. The cache benefit
   only applies on the Anthropic path.
3. **Open-weight via OpenRouter has API cost too.** "Free" only
   applies if you self-host (next section).

---

## Annual cost by volume

A regulated organization typically generates **somewhere between 1K and
100K verifications per day**, depending on the workflow.

| Daily volume | Sonnet (no cache) | Sonnet (cached) | Haiku | Gemini Flash | Self-hosted Llama/Qwen |
|---:|---:|---:|---:|---:|---:|
| 1,000/day | $3.7K–5.1K/yr | $0.7K–1.8K/yr | $0.4K–1.1K/yr | $0.2K–0.4K/yr | $6K–24K/yr flat |
| 10,000/day | $36K–51K/yr | $7K–18K/yr | $4K–11K/yr | $2K–4K/yr | $6K–24K/yr flat |
| 100,000/day | $365K–510K/yr | $73K–180K/yr | $36K–110K/yr | $18K–37K/yr | $24K–60K/yr flat |
| 1,000,000/day | $3.6M–5M/yr | $0.7M–1.8M/yr | $0.4M–1.1M/yr | $0.2M–0.4M/yr | $60K–240K/yr flat |

Self-hosted-flat assumes a single H100 or 2×A100 GPU ($500–2K/mo) at
the lower end, and a small dedicated cluster ($2–5K/mo) at the upper
end. Above ~100K/day API spend, self-hosted starts winning on pure
cost — but you pay in operational complexity.

---

## What the buyer should hear

The pitch isn't "Orc is cheap" or "Orc is expensive." It's:

> Pick the model tier that matches your volume and risk posture. Orc
> runs the same audit-bundle, the same replay, the same multi-approver
> workflow on all of them. The cost knob is yours to turn.

Three concrete deployment shapes:

1. **High-stakes, low-volume (a few hundred to a few thousand
   verifications per day).** Credit memos. Regulatory submissions.
   Legal opinions. Use Sonnet — the F1 ceiling matters more than the
   API spend, and the absolute cost is small ($4–20K/yr).

2. **Steady-state, moderate-to-high volume (10K–100K/day).** Compliance
   monitoring. Transaction-narrative review. Editorial fact-checks.
   Use Haiku or Gemini Flash with prompt caching enabled. Cost lands
   in the $5–40K/yr range — comfortably inside a compliance team's
   tooling budget.

3. **VPC / air-gapped / sovereign data residency.** Banks running their
   own infrastructure. EU enterprises with data-residency constraints.
   Self-host Llama 3.3 70B or Qwen 2.5 72B with binary mode +
   deployment-specific calibration. Quality lands at F1 ~0.6 out of
   the box; calibration work brings it higher and is the right scope
   for an enterprise pilot.

---

## How does this compare to the alternative?

The comparison isn't "Orc vs a perfect human reviewer." Two corrections
worth being explicit about:

**1. Humans aren't 100% either.** Inter-rater agreement on compliance
judgments typically runs 80–90% — fatigue, attention drift, and
genuine ambiguity in the underlying claim. Comparing Orc-at-F1-0.86
to a hypothetical 100%-accurate human is the wrong baseline.

**2. Orc is review-triage, not review-replacement.** The honest model
is that Orc changes *what* humans review, not whether they review:

| | Without Orc | With Orc |
|---|---|---|
| Human reviews | Every output | The 10–20% Orc flags |
| Auto-pass | Nothing | High-confidence `supported` with valid citations |
| Auto-flag for human | Nothing | `contradicted`, `not_found`, low-confidence |
| Audit trail | "Analyst approved" (no record) | Full retrieval + verdict + citations + approval gate, replayable |

The human stays in the loop on every hard case (via the approval
queue) and always in the loop on external action. **Orc doesn't
replace the reviewer; it lets one reviewer cover 5–10× the volume.**

### The honest cost comparison

A compliance analyst at $200/hr fully-loaded, 5 minutes per review,
costs **~$17 per manual verification.**

Today, a team of 5 analysts can review ~5,000 outputs/year — and the
record of what they reviewed is a Slack message and an approval
ticket.

With Orc routing the 80–90% of clear-cut cases automatically:
- Same 5 analysts now meaningfully review **25,000–50,000 outputs/year**
  (the same volume of human attention, applied to the cases that need
  it).
- Every one of the 50,000 carries a full audit bundle, replayable.
- The bill for the Orc API spend at Haiku: $1.5–5K/year for 50K
  verifications.

Versus the human-only baseline of $850K/year for 5,000 reviews (and no
audit artifact), this is what the buyer is actually buying. **Not
"cheap AI"; better coverage per dollar of human attention, plus an
artifact that survives an audit.**

### Honest failure-mode framing

Two ways Orc can be wrong, both worth naming:

- **False positive (`supported` when the claim is actually wrong):**
  The citation guard reduces this — the cited chunks have to exist
  and match retrieval. But the model can still pick chunks that look
  supportive and aren't. Sonnet source-routed precision on PASS is
  0.90, meaning ~10% of `supported` verdicts could be wrong. The
  audit trail lets a downstream reviewer catch which ones.
- **False negative (Orc misses a real problem):** more dangerous in
  silent-fail mode. Mitigation: route low-confidence, `not_found`,
  and `contradicted` to mandatory human review rather than auto-
  passing them.

Neither failure mode invalidates the audit trail. Even an Orc verdict
that turns out to be wrong is a *traceable* wrong verdict — the
retrieval set, the LLM call, the citation, the approver all recorded.
That's what an audit needs: not "the AI was always right," but "here
is exactly what the AI did, with what evidence, and who signed off."

---

## Why the multi-model benchmark exists

The cost story above depends entirely on having a validated picture of
which models deliver acceptable verdict quality. That's what
[`results-2026-05-19-multi-model.md`](../benchmarks/results-2026-05-19-multi-model.md)
is for: it tells the buyer the cost-vs-quality curve isn't speculative.

When a compliance officer asks "can we use Haiku to keep the bill
under $20K?" — there's a real F1 number behind the answer. When they
ask "can we self-host Llama 3.3 inside our VPC?" — there's an honest
"yes, with binary mode and these calibration steps" answer.

---

## What Orc charges for, regardless of model

The model is the per-verification dial. The paid product (Team, VPC,
Enterprise) charges for the operations:

- Hosted MCP gateway so a 50-person team isn't installing the CLI
  individually.
- Compliance dashboard that maps outputs to EU AI Act articles, SR
  11-7 controls, etc.
- Retention guarantees and signed/timestamped audit handoff.
- Connectors to Google Drive / SharePoint / S3 / Slack / DMS.
- Custom directives + per-model calibration as billable pilot scope.

See [`roadmap.md`](./roadmap.md) for the full SKU shape.
