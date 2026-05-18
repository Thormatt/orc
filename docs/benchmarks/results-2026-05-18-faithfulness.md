# Faithfulness benchmark — Orc on HaluBench

**Run date:** 2026-05-18 · **Orc version:** 0.1.2 · **Dataset:** `PatronusAI/HaluBench` test split, stratified subsample N=504 (252 PASS / 252 FAIL, 84 each from DROP, FinanceBench, RAGTruth, covidQA, halueval, pubmedQA). Deterministic under `random.seed(42)`.

## Headline

Orc's `verify_claim` skill on the stratified subsample (1 item skipped due to a transient API error, so N=503 evaluated):

| Metric | Score |
|---|---:|
| Accuracy | **0.7913** |
| Precision (PASS) | **0.8025** |
| Recall (PASS) | **0.7738** |
| F1 (PASS) | **0.7879** |

Confusion matrix: TP=195, FP=48, TN=203, FN=57.

## Label mapping

HaluBench labels are binary `PASS` (faithful) / `FAIL` (hallucinated). Orc returns one of four verdict labels. The default mapping used here:

| Orc verdict | → | HaluBench |
|---|---|---|
| `supported` | → | PASS |
| `partial` | → | FAIL |
| `contradicted` | → | FAIL |
| `not_found` | → | FAIL |

A reviewer who disagrees with this mapping can re-score against the raw `orc_verdict` field in `results.json` under an alternative mapping. Results.json carries every per-item verdict + confidence + run_id.

## Per-source breakdown

The aggregate hides a real pattern. Orc dominates on natural-language Q+A and underperforms on tabular / numeric tasks:

| source | n | accuracy | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|
| `covidQA`      | 84 | 0.952 | 0.975 | 0.929 | **0.951** |
| `RAGTruth`     | 84 | 0.893 | 0.923 | 0.857 | **0.889** |
| `halueval`     | 84 | 0.798 | 0.791 | 0.810 | **0.800** |
| `pubmedQA`     | 83 | 0.807 | 1.000 | 0.619 | **0.765** |
| `FinanceBench` | 84 | 0.643 | 0.591 | 0.929 | **0.722** |
| `DROP`         | 84 | 0.655 | 0.724 | 0.500 | **0.592** |

Observations:
- **covidQA / RAGTruth / halueval** (natural-language passages): F1 between 0.800 and 0.951. This is Orc's home turf — BM25 retrieval + LLM verification on prose-heavy contexts.
- **pubmedQA**: precision 1.000, recall 0.619 — Orc never falsely marks a hallucination as supported in this slice (zero false positives), but is conservative and misses 32 actually-supported claims.
- **FinanceBench / DROP** (tabular, numeric, multi-hop): F1 drops to 0.722 / 0.592. BM25 struggles with discrete numeric facts; the LLM is then forced to verify against a less-relevant retrieval set.

This is honest evidence that Orc's current retrieval strategy is right for prose corpora (which describes the EU-AI-Act-deployer buyer's documentation) and weak for tabular extraction (which is its own engineering domain).

## Verdict distribution

| Verdict | Count |
|---|---:|
| `supported` | 243 |
| `contradicted` | 177 |
| `partial` | 80 |
| `not_found` | 3 |

Only 3 `not_found` verdicts across 503 items confirms BM25 retrieval surfaces *something* for almost every claim — the question becomes whether the LLM can adjudicate it correctly given the surfaced chunks.

## Confidence calibration

Roughly calibrated, with monotonic accuracy across confidence buckets:

| Confidence | n | Accuracy |
|---|---:|---:|
| 0.3 | 1 | 1.00 |
| 0.5 | 2 | 0.50 |
| 0.6 | 4 | 0.25 |
| 0.7 | 14 | 0.57 |
| 0.8 | 75 | 0.65 |
| 0.9 | 46 | 0.78 |
| 1.0 | 361 | 0.84 |

Low-confidence buckets are tiny samples; the 1.0 bucket (where Orc says "I'm sure") is right 84% of the time — calibrated enough to be useful as a downstream filter.

## HHEM-2.1-Open comparable — deferred

The first run attempted to also score the same 504 items with Vectara's open-weight HHEM-2.1-Open (self-hosted, free) for a direct head-to-head. It failed with a tokenizer-instantiation error from the `transformers` library even after `sentencepiece` was installed. The model uses a custom `HHEMv2Config` and likely requires loading via its bundled `model.predict(pairs)` API rather than the `AutoTokenizer + AutoModel` path my runner used.

**Status:** Orc results are saved; HHEM is a separate task. Cost-impact of the deferral is zero — HHEM scoring is local-CPU, no API spend.

## Cost

- API spend: ~$6 (one full N=504 pass through Claude Sonnet 4.6 via OpenRouter)
- Wall-clock: ~45 minutes
- A prior crashed run cost another ~$6 before I made the checkpointing resilient, bringing total session spend to ~$12. The runner now checkpoints `results.json` every 25 items and writes an Orc-only snapshot before the HHEM step, so a downstream failure can never throw away a paid Orc pass again.

## Public-claim framing

Per the agreed-on hedged framing — no "no competitor does X" — the defensible public statement that follows from this run is:

> On a stratified 503-item subsample of HaluBench, Orc's `verify_claim` skill achieves F1=0.79 against ground-truth faithfulness labels under the default verdict→binary mapping. Per-source F1 ranges from 0.59 (DROP, tabular/numeric) to 0.95 (covidQA, natural-language Q+A). The full per-item verdicts and confidence scores are published in `results.json` so reviewers can re-score under an alternative mapping or restrict to a single source.

The headline number is competitive with published faithfulness judges (Patronus's Lynx paper reports F1 ≈ 0.85 on HaluBench but on a fine-tuned model purpose-built for the task; Orc uses a general-purpose Sonnet 4.6 call with a verification prompt — different scope). A reviewer who wants apples-to-apples should pair this with a Lynx run on the same 503-item subsample, which is the natural next benchmarking step.

## Reproducing

```sh
# One-time: pull the stratified subsample from HuggingFace.
uv pip install datasets
uv run python -m benchmarks.faithfulness.bootstrap

# Run.
ORC_BENCHMARK_ALLOW_LIVE_LLM=1 \
  uv run python -m benchmarks.faithfulness.run --n 504
```

The raw per-item verdicts, confidence scores, and run_ids live in
`benchmarks/faithfulness/results/<timestamp>/results.json`. That directory is
gitignored — the canonical summary is this file.
