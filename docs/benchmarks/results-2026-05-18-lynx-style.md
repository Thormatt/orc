# Faithfulness benchmark — Lynx-style variant comparison

**Run date:** 2026-05-18 · **Orc version:** 0.1.2 · **Dataset:** same stratified 504-item HaluBench subsample as the default run ([results-2026-05-18-faithfulness.md](results-2026-05-18-faithfulness.md)).

## What was measured

The `lynx_style` benchmark variant calls Claude Sonnet 4.6 directly with Lynx's literal evaluation prompt — no retrieval, no chunk-id validation, no structured 4-label output. The point: isolate the underlying model's judgment capability from Orc's full pipeline, so we know which part of the F1 gap to Lynx is addressable.

Lynx's prompt template ([arXiv:2407.08488](https://arxiv.org/abs/2407.08488) §4.2):
```
Question: {q}
Context: {c}
Answer: {a}
Is the answer FAITHFUL to the context? Respond with YES or NO.
```

YES → PASS, NO → FAIL. Same default mapping as Orc's verdict labels in the original run.

## Headline (N=473 evaluable / 504 total, 31 skipped)

| Metric | Default (full Orc pipeline) | Lynx-style (direct judge) | Δ |
|---|---:|---:|---:|
| Accuracy | 0.7913 | **0.8076** | +0.016 |
| Precision (PASS) | 0.8025 | **0.7517** | -0.051 |
| Recall (PASS) | 0.7738 | **0.9198** | +0.146 |
| **F1 (PASS)** | **0.7879** | **0.8273** | **+0.039** |

The F1 lift is real (~4 points) and puts Orc inside Lynx's true OOD range (0.78–0.82, per the third-party reproductions the research surfaced). But the precision/recall tradeoff is what makes this interesting: Lynx-style is much more permissive (recall +14.6 points), at the cost of precision (-5.1 points). This is **a different shape of model**, not just a better one.

## The unexpected finding — Orc's structure helps where you'd expect it to

| Source | Default F1 | Lynx-style F1 | Δ | What this means |
|---|---:|---:|---:|---|
| `pubmedQA` (medical research) | 0.765 | **0.966** | **+0.201** | Direct reasoning over a single passage benefits hugely from a stripped prompt |
| `DROP` (tabular reasoning) | 0.592 | **0.719** | **+0.127** | Orc's structured prompt was *hurting* on numeric items |
| `FinanceBench` (financial tables) | 0.722 | 0.756 | +0.034 | Marginal gain on numeric tables |
| `halueval` (Wikipedia/news) | 0.800 | 0.818 | +0.018 | Roughly equivalent |
| `covidQA` (medical literature) | 0.951 | 0.905 | **-0.046** | Citation-aware prompt *helps* here |
| `RAGTruth` (RAG passages) | 0.889 | 0.804 | **-0.085** | Citation-aware prompt *helps* here |

The pattern is striking and product-relevant. Stripping Orc's pipeline:
- **Helps** on tasks where the verification is "does this answer follow from the math/logic in the passage" (pubmedQA, DROP, FinanceBench)
- **Hurts** on tasks where the verification is "does the corpus support the claim, with evidence" (covidQA, RAGTruth)

That isn't a bug in either approach — it's a real distinction between *judgment tasks* (one passage, derive a yes/no) and *verification tasks* (a corpus, find supporting evidence). Orc was built for the second; Lynx is optimized for the first.

## What the F1 gain costs

| Cost | Lynx-style |
|---|---|
| Chunk-level citations | **None** — no chunk IDs in the response |
| Retrieval against a corpus | **None** — passage is given |
| Confidence score | **None** — binary only |
| Multi-document support | **None** — single-passage assumption baked in |
| Replay against corpus snapshot | **N/A** — no corpus |
| Audit-bundle compatibility | The call still writes a Run row + trace, but there's no `effective_kwargs` retrieval set to pin |
| Items the model refused to parse | **31 / 504 (6%)** — Sonnet started multi-step CoT and got truncated, mostly on FinanceBench/DROP numeric items |

So a strict "swap the prompt" move trades 4 F1 points for the loss of every primitive that defines Orc as a verification runtime instead of a faithfulness judge. The architectural moat dies for a marketing number.

## The product implication

This benchmark doesn't tell us to swap the prompt. It tells us **the runtime should know which mode to use**.

Two next-step options follow naturally:

### Option A — Dual-mode `verify_claim`

Add a `mode` parameter to the production skill:
- `mode="evidence"` (default): current behavior — BM25 retrieval, 4-label verdict, chunk-id validation. The right call when the input is a claim that must be *verified against a curated corpus*.
- `mode="judgment"`: lightweight binary judgment when the caller already has the relevant passage staged. The right call when the input is a (passage, claim) pair and the answer is "is this internally consistent" rather than "is this supported."

Both modes write traces and effective_kwargs; both are reproducible. The audit bundle covers either.

This preserves the moat AND captures the per-source F1 gains. Estimated implementation: 1 day.

### Option B — Confidence-gated routing in production

The runtime decides which mode to use per call based on heuristics:
- Corpus size = 1 single document → `judgment` mode
- Corpus size > 1, or claim mentions specific numbers → `evidence` mode
- Numeric/calculation claims → always `judgment` (the lynx_style numbers say this beats the structured prompt by 13 points on DROP)

Lower-effort interpretation: ship Option A first; layer Option B routing on top later.

## What I'm explicitly NOT recommending

- **Swap the production prompt to Lynx-style.** Loses citations and retrieval (the moat). Bad trade.
- **Treat the F1 = 0.83 as our headline.** It's the underlying-judge-only number on a benchmark where the architectural primitives don't get to contribute. Publishing it as "Orc's number" would be misleading.
- **Use the per-source per-mode numbers as a competitive claim against Lynx.** They're useful internal product signals, not external attacks.

## What is publishable from this run

Two true statements with their evidence:

> **The underlying judge capability of Claude Sonnet 4.6 with Lynx's prompt on the HaluBench subsample is F1 = 0.83**, which is at the high end of Lynx's own published out-of-distribution range. Orc's verification runtime, which adds retrieval + chunk-level citations + structured 4-label adjudication on top of that judge, scores F1 = 0.79 — the 4-point gap is the cost of producing an auditable artifact rather than a bare score.

> **The per-source decomposition is informative.** Orc's architecture *helps* (relative to a bare judge) on tasks where the buyer's question is "does the corpus support this claim with citable evidence" (covidQA, RAGTruth — Orc beats lynx_style by 5–9 points). It *hurts* on tasks that are essentially single-passage math problems (pubmedQA, DROP — lynx_style beats Orc by 13–20 points). The right product move is dual-mode, not single-mode.

## Skipped items

31 of 504 items returned unparseable responses, all of them numeric/tabular (DROP and FinanceBench). The pattern: Sonnet started multi-step calculation reasoning and ran into the 64-token output budget before emitting YES/NO. A larger output budget (256 tokens) would close most of these; a "answer first, then explain" instruction would close more.

This is a benchmarking artifact, not a production concern — in production, `verify_claim` doesn't have a tight token budget.

## Reproducing

```sh
ORC_BENCHMARK_ALLOW_LIVE_LLM=1 \
  uv run python -m benchmarks.faithfulness.run --n 504 --variant lynx_style
```

Cost: ~$2.50 of Claude Sonnet 4.6 via OpenRouter. Wall-clock: ~17 minutes.

The full per-item verdicts live in `benchmarks/faithfulness/results/<ts>/results.json` (gitignored). This file is the canonical summary.
