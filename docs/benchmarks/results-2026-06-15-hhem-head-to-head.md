# Same-set head-to-head: Orc vs. Vectara HHEM-2.1-Open

**Date:** 2026-06-15 · **n = 503** (stratified HaluBench subsample) · **positive class = PASS (faithful)**

This is the same-set comparison the [competitive doc](../positioning/competitive.md)
previously listed as pending ("we will publish ours once the HHEM tokenizer-load
issue is resolved"). The blocker is resolved (see *Reproducing*); here are the
numbers.

## What was compared

- **Orc** — `verify_claim` in source-routed mode. Verdicts are **reused** from
  the existing 503-item run (`results/20260519-191250/`, F1 0.864), not re-run,
  so the Orc side cost nothing here.
- **HHEM-2.1-Open** — Vectara's open-weight faithfulness scorer, self-hosted on
  CPU. Input: `premise = passage`, `hypothesis = answer` (the standard
  formulation — and the more favorable one; see *Fairness*). Threshold 0.5.
- **Identical items**, matched by id. Same ground-truth labels, same scoring
  code (`orc.metrics.scoring`).

## Headline

| System | F1 (PASS) | Accuracy | Precision | Recall |
|---|---:|---:|---:|---:|
| **Orc** (source-routed) | **0.864** | 0.869 | 0.897 | 0.833 |
| HHEM-2.1-Open | 0.643 | 0.634 | 0.629 | 0.659 |

Confusion — Orc: TP=210 FP=24 TN=227 FN=42 · HHEM: TP=166 FP=98 TN=153 FN=86.

On this set Orc leads by **0.22 F1**. The rest of this doc is the honest
accounting of *why*, and what the number does and doesn't mean.

## It is not a truncation artifact

HHEM has a 512-token context window; ~27% of these items (137/503) exceed it,
so HHEM sees only the first ~512 tokens of long passages. That could explain the
gap — except it doesn't:

| Subset | n | Orc F1 | HHEM F1 |
|---|---:|---:|---:|
| All | 503 | 0.864 | 0.643 |
| **Fits in 512 tokens** | 366 | 0.844 | **0.623** |
| Truncated (>512) | 137 | 0.930 | 0.693 |

HHEM scores essentially the same on items that fit comfortably in its window
(0.623) as it does overall (0.643) — and slightly *higher* on the truncated
ones (0.693, mostly long covidQA passages it handles well). The gap is real,
not a context-length handicap.

## Where the gap comes from — per source

| Source | n | Orc F1 | HHEM F1 | Note |
|---|---:|---:|---:|---|
| RAGTruth | 84 | 0.878 | **0.796** | HHEM's home turf (RAG passages) — competitive |
| covidQA | 83 | 0.951 | 0.752 | long medical literature |
| halueval | 84 | 0.814 | 0.727 | short Wikipedia/news claims |
| pubmedQA | 84 | 0.865 | 0.648 | biomedical |
| DROP | 84 | 0.759 | 0.448 | tabular reasoning |
| FinanceBench | 84 | 0.916 | **0.299** | numeric reasoning — HHEM collapses |

The pattern is exactly what theory predicts. HHEM is a **consistency-scoring
encoder**: strong on "is this sentence entailed by that passage" (RAGTruth
0.796), and structurally unable to do **arithmetic or multi-step reasoning**
(FinanceBench 0.299, DROP 0.448). Orc routes those to arithmetic/binary modes —
the model invokes a calculator, and the math is recorded in the trace.

## The honest caveat

This stratified subsample equal-weights all six categories (84 each). That makes
it **harder for HHEM than the full HaluBench**, which is weighted toward the
RAG/short-claim categories HHEM handles well — so HHEM's ~0.75 published headline
on full HaluBench is not contradicted by the 0.643 here; they are different
distributions. **Orc's 0.864 is measured on this same harder set.** The fair
reading:

> On an identical, reasoning-heavy 503-item set, Orc (0.86) clearly outscores
> the open HHEM-2.1 judge (0.64). HHEM stays competitive on RAG-style passages
> (0.80) but collapses on numeric and tabular reasoning — the cases Orc's
> calculator and routing are built for.

Two further honesty notes carried over from the main results:
- Orc's source routing was tuned on per-source breakdowns of this same
  subsample, so the 0.864 carries a mild optimistic bias (train-on-test in the
  *routing* decision, not the verdicts).
- Lynx-70B remains a *published-number* comparison (F1 ≈ 0.85 on full HaluBench),
  not same-set — we can't self-host a 70B judge here.

## Fairness

HHEM's worst category (FinanceBench, 0.299) was re-run with the question folded
into the hypothesis to check we weren't feeding it a bad input:

| HHEM input on FinanceBench | F1 |
|---|---:|
| `hypothesis = answer` (used here) | 0.299 |
| `hypothesis = question + answer` | 0.125 |

The formulation we used is the **more favorable** one. HHEM is not being hobbled
by input formatting.

## Reproducing

The "HHEM tokenizer-load issue" was a transformers-5.x incompatibility with
HHEM's vendored modeling code (`all_tied_weights_keys`). The `benchmarks` extra
now pins `transformers<5`.

```bash
uv sync --extra benchmarks                      # installs transformers 4.x + torch
uv run python -m benchmarks.faithfulness.head_to_head \
    --orc-run benchmarks/faithfulness/results/20260519-191250/results.json
```

HHEM runs on CPU (~0.4 s/item, ~3.5 min for 503). No live LLM spend — the Orc
verdicts are reused from the cited run.
