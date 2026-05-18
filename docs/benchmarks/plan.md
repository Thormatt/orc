# Orc verification benchmark — plan

**Status:** draft, internal. The public-facing competitive doc waits on this
suite producing numbers.

## Why this exists

The competitive landscape research (May 2026) confirmed Orc's strongest
positioning is **verification runtime** — claim-level citation enforcement +
deterministic replay + portable audit bundle, packaged as a local-first
runtime. The directional claim defends well in conversation; it does not
defend in writing without measurements.

This benchmark gives the positioning teeth. It exists to convert four
invisible runtime properties into reproducible numbers a reviewer can re-run
on their own machine.

Cardinal rule: **the benchmark itself runs through Orc** (workspace, run,
trace, replay). Every benchmark invocation produces an `orc audit export`
bundle. The benchmark is its own self-demonstration.

## What it does *not* try to do

- It does not declare a winner against named competitors in marketing copy.
- It does not claim "no competitor does X." Public claims will use the
  hedged framing: *"Most tools focus on RAG faithfulness, observability,
  governance workflows, or agent orchestration. Orc's wedge is bundling
  claim-level citation validation, replayable traces, approval gates, and
  portable audit export into a local-first runtime."*
- It does not benchmark generation quality. Orc does not generate; it
  verifies. The relevant axis is "what reaches the caller?", not "is the
  answer good?".

## The four claims under test

| # | Claim | Type | Comparable to |
|---|---|---|---|
| 1 | Hallucinated citation IDs are dropped *before* the verdict reaches the caller. | Property assertion (runtime invariant) | None directly — Lynx/HHEM/RAGAS run *after* generation. Demonstrate the architectural difference. |
| 2 | Unsupported claims are not marked `supported`. | Quantitative benchmark | Patronus Lynx, Vectara HHEM, RAGAS faithfulness — public faithfulness datasets (HaluBench, RAGTruth). |
| 3 | The audit bundle is structurally complete: every run row has a trace, every trace has provenance to a sha256-pinned evidence item. | Property assertion + bundle invariant test | Capabilities matrix vs. LangSmith, Langfuse, Phoenix, Galileo. |
| 4 | A trace is replayable on a clean machine without access to the original infrastructure. | Reproducibility test | Capabilities matrix; only Orc and (partially) Langfuse offer offline replay. |

## Methodology

### Test 1 — Citation enforcement (runtime invariant)

**Goal:** prove the runtime drops chunk_ids that did not appear in the
retrieval set, regardless of what the LLM returns.

**Method.** Construct an injection corpus and a fake LLM client that returns
`supporting_chunk_ids` containing (a) real chunk_ids from the retrieval set
and (b) fabricated chunk_ids that look plausible but do not exist. Run
`verify_claim` N=100 times across varied claims. Measure: fraction of
fabricated IDs that reach the caller's `supporting_chunks` array.

**Expected result.** 0 fabricated IDs reach the caller. The drop is enforced
by `verify_claim.py`'s chunk_id-validation step against the retrieval set.

**Honest framing.** This is a *property assertion*, not a head-to-head
benchmark. Faithfulness judges (Lynx, HHEM, RAGAS) score the answer-vs-context
relation *after* the answer is produced. They do not intercept the citation
graph. The comparable claim is architectural: *"in Orc, a hallucinated
citation cannot reach the caller. In a faithfulness-judge pipeline, it can
reach the caller and be flagged downstream — which is a different system
property."*

### Test 2 — Unsupported claims marked supported

**Goal:** measure Orc's false-positive rate (`supported` label on a claim
the corpus does not support) on a public faithfulness dataset.

**Datasets.**
- **HaluBench** (Patronus, [github.com/patronus-ai/Lynx-hallucination-detection](https://github.com/patronus-ai/Lynx-hallucination-detection)) — 15k items, labeled `PASS` (faithful) / `FAIL` (hallucinated). Public, license-permissive.
- **RAGTruth** ([github.com/ParticleMedia/RAGTruth](https://github.com/ParticleMedia/RAGTruth)) — 18k responses, spans labeled. More granular.

**Method.** For each labeled item: ingest the context, run
`orc verify "<claim>"`. Label-map: Orc `supported` → faithful; Orc
`contradicted`/`partial`/`not_found` → unfaithful. Compute precision,
recall, F1 against ground truth. Cost-bounded: run a stratified subsample
(N=500 from HaluBench, balanced PASS/FAIL) before scaling.

**Comparison.** Where API access is feasible and within budget, run the
same items through Lynx and RAGAS faithfulness. HHEM is the trickier one
(Vectara-hosted; check whether the open-weight version on HF
`vectara/hallucination_evaluation_model` is sufficient for self-hosted
scoring). Report precision/recall/F1 side by side.

**Honest framing.** The label maps are imperfect — Orc's `partial` and
`not_found` are not the same thing as "hallucinated." Document the mapping
choice explicitly. Report agreement with one mapping and disagreement
sensitivity with an alternative mapping.

### Test 3 — Audit bundle completeness

**Goal:** prove that an `orc audit export` bundle is internally consistent —
every run row has a trace JSON, every trace cites only evidence items present
in `evidence.csv`, every evidence sha256 verifies, every manifest sha256
verifies.

**Method.** A property-test harness that:
1. Runs a randomized sequence of `ingest`, `verify`, `search`, and
   `approve enqueue/accept` operations.
2. Executes `orc audit export`.
3. Asserts the bundle invariants (see below).

**Bundle invariants under test.**
- `manifest.json.files[*]` hashes match extracted file contents (already
  covered by `tests/unit/test_audit_export.py`; promote to benchmark).
- Every `runs.csv` row has a corresponding `traces/<YYYY>/<MM>/<run_id>.json`.
- Every `chunk_id` referenced in a trace's `retrieval.returned[]` and
  `output.supporting_chunks[]` belongs to an `evidence_id` listed in
  `evidence.csv`.
- Every `evidence.csv` row's sha256 matches the file content at the recorded
  `source_path` (where the source is still on disk).
- Every trace's `schema_version` is in `SUPPORTED_TRACE_SCHEMA_VERSIONS`.

**Comparison.** Capabilities matrix, not numeric. For each of LangSmith,
Langfuse, Phoenix, Galileo, document: does an equivalent single-artifact
export exist? does it carry per-file hashes? does it survive being moved
to a clean machine?

### Test 4 — Replay portability

**Goal:** prove a trace can be replayed on a clean machine.

**Method.**
1. On machine A: run a workspace through a known sequence; `orc audit export`.
2. Transfer the bundle to a clean filesystem (no `$ORC_HOME`, fresh venv).
3. Reconstruct the workspace from the bundle (script TBD — likely a new
   `orc audit import` command, slated for v0.1.3).
4. `orc replay <run_id>` for every run row.
5. Assert: same verdict, same confidence, same supporting_chunk_ids,
   bit-identical output for skills with deterministic LLM clients.

**Comparison.** Most competitors fail step 2 — their traces live in their
cloud. Langfuse OSS is the only plausible peer; document its actual
replay-vs-visualization behavior.

## Reproducibility

The benchmark suite lives at `benchmarks/` in the repo. Each test produces:
- A run script (`run.py`) callable as `python -m benchmarks.<name>`.
- A `dataset.yaml` (or pointer to an external dataset).
- An `orc audit export` of the benchmark run itself, written to
  `benchmarks/<name>/results/<timestamp>/`.

A reviewer reproduces by: clone repo → `uv sync` → `python -m benchmarks.<name>`.
The output bundle is the artifact; numeric results in `results.json` are
generated from it.

## Sequence of work

1. **Test 1** (citation enforcement) — fastest to ship; Orc's home turf;
   already partially covered by unit tests, needs to be promoted to a
   measurable harness with documented N and a published numeric result.
2. **Test 3** (bundle completeness) — second fastest; mostly invariant
   assertions; the property tests already exist as unit tests and can be
   re-targeted at randomized state.
3. **Test 2** (faithfulness vs HaluBench/RAGTruth) — biggest build; LLM cost
   bounded by stratified sampling.
4. **Test 4** (replay portability) — depends on a future `orc audit import`
   command. Defer until v0.1.3 or after gads. Document the capabilities
   matrix in the meantime.

After tests 1–3 produce numbers, write `docs/positioning/competitive.md` as
category framing with the benchmark numbers as evidence — not attack copy.

## What public claims will look like, post-benchmark

Anchored to numbers from this suite, the strongest defensible claims are:

> Orc's runtime enforces citation validity as an invariant: across N runs in
> the citation-enforcement benchmark, 0 fabricated chunk_ids reached the
> caller. Faithfulness judges (Lynx, HHEM, RAGAS) operate on the post-generation
> answer and do not intercept the citation graph; both approaches have value,
> but they answer different questions.

> On HaluBench (stratified N=500), Orc verify_claim achieves F1=X.X for
> identifying hallucinated claims, compared to Lynx=Y.Y and RAGAS=Z.Z under
> the documented label-mapping. Mapping sensitivity is reported.

> An `orc audit export` bundle of any workspace satisfies the bundle
> invariants in test 3 by construction. Equivalent single-artifact exports
> with per-file hashing and offline replay are uncommon in the tools surveyed;
> capabilities matrix in §3.

No "no competitor does X." No marketing-by-omission. Just the numbers and
the architectural framing.
