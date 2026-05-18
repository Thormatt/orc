# Faithfulness benchmark — judgment-mode through the production runtime

**Run date:** 2026-05-18 · **Orc version:** 0.1.2 + dual-mode patch · **Dataset:** same stratified 504-item HaluBench subsample.

## What was measured

`mode="judgment"` is a new parameter on the production `verify_claim` skill. It skips BM25 retrieval (uses all workspace chunks or a specified `evidence_id`), loads a lighter system prompt (`verify_claim_judgment.md`), and otherwise keeps every architectural primitive: structured 4-label output via tool use, chunk-id validation, trace + replay + audit export.

This run validates whether the F1 lift the `lynx_style` variant showed (+0.04 over default) is recoverable inside the production runtime when callers opt into judgment mode for single-passage verification tasks.

## Headline (N=503 evaluated, 1 skipped)

| Metric | Default (evidence mode) | **Judgment mode** | Lynx-style (bare LLM) |
|---|---:|---:|---:|
| Accuracy | 0.7913 | **0.8032** (+0.012) | 0.8076 |
| F1 | 0.7879 | **0.8085 (+0.021)** | 0.8273 |
| Precision (PASS) | 0.8025 | 0.7887 (-0.014) | 0.7517 |
| Recall (PASS) | 0.7738 | **0.8294 (+0.056)** | 0.9198 |

**Judgment mode captures roughly half the F1 lift that bare lynx-style gets, while preserving the full architectural moat.** The other half of the gap (judgment 0.81 → lynx_style 0.83) is the cost of producing a structured verdict with chunk citations — and that's a cost we want to pay, because it's literally what makes the output auditable.

## Per-source comparison

| Source | Default F1 | Judgment F1 | Δ |
|---|---:|---:|---:|
| `pubmedQA` (medical reasoning) | 0.765 | **0.849** | **+0.084** |
| `DROP` (tabular) | 0.592 | **0.659** | **+0.067** |
| `FinanceBench` | 0.722 | 0.729 | +0.007 |
| `halueval` | 0.800 | 0.814 | +0.014 |
| `covidQA` | 0.951 | 0.940 | -0.011 |
| `RAGTruth` | 0.889 | 0.884 | -0.005 |

The shape matches the hypothesis: judgment mode is the *right* default for tasks where the runtime can stage a specific passage and the question is "does this answer follow from this passage." It's the wrong default for tasks where the question is "find evidence in a curated corpus."

The aggregate gains on Orc's two weakest source datasets (pubmedQA +0.08, DROP +0.07) come at a marginal cost on the strongest (covidQA -0.01, RAGTruth -0.01). This is a much better trade than swapping the production prompt entirely.

## What this preserves

Every architectural primitive Orc's market position depends on. Judgment mode:

- **Validates chunk IDs.** The model still has to cite chunks from the retrieval set; hallucinated IDs are dropped.
- **Writes a structured verdict.** `supported / partial / contradicted / not_found` with confidence and chunk citations. Direct comparison to the default-mode trace.
- **Records `effective_kwargs={"mode": "judgment"}`** so a replay re-runs against the same mode.
- **Flows through `Run.record_retrieval`** with `method="judgment_all"` so the audit bundle can distinguish the two retrieval strategies.
- **Is replay-safe.** `corpus_version` is honored in judgment mode the same way it is in evidence mode.

What changes between evidence and judgment mode is *which* chunks the LLM sees (BM25-filtered top-K vs. all corpus chunks) and *what prompt structure* drives the verdict (verbose 4-label adjudication vs. lighter binary-leaning framing). Nothing about the audit story changes.

## Public-claim framing this earns

After this run, the defensible public statement evolves to:

> Orc's verification runtime offers two modes for faithfulness claims, both fully audit-exportable:
>
> - **Evidence mode** (default) — BM25 retrieval over a curated workspace, structured 4-label verdict with chunk-level citations. F1=0.79 on a stratified HaluBench subsample. This is the right call when the runtime owns the corpus and citation discipline matters.
>
> - **Judgment mode** — pre-staged passage, lighter prompt, same structured output and citation enforcement. F1=0.81 on the same subsample, with the biggest gains exactly where evidence mode is weakest (medical reasoning, tabular extraction). The right call for verifying a specific (passage, claim) pair.
>
> The runtime never trades the auditability of the result for headline accuracy — what changes between modes is which question is being asked, not whether the answer is defensible.

## Cost

- ~$5 of Claude Sonnet 4.6 via OpenRouter for the full N=504 judgment run.
- Wall-clock ~40 min (judgment mode is the same LLM-call shape as default, just smaller corpus blocks per item — BM25 query is the cheap part anyway).
- 1 item skipped due to a parsing edge case (no `label` key in response payload). Should be fixed with a stricter tool-use enforcement.

## Reproducing

```sh
ORC_BENCHMARK_ALLOW_LIVE_LLM=1 \
  uv run python -m benchmarks.faithfulness.run --n 504 --variant judgment
```

The raw per-item verdicts live in `benchmarks/faithfulness/results/<ts>/results.json`. The dataset is the same `halubench-stratified-504.jsonl` produced by `bootstrap.py`.
