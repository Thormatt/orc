# orc eval + tiered verification — design

**Status:** approved design, pre-implementation
**Date:** 2026-06-12

## Context

Two independent validation studies of orc's architecture (a 106-agent adversarial
web-research run and a 4-model Delphi panel) converged on the same top gap: the
verification gate is **unmeasured on the user's own corpus**, and any tiered
cost-saving routing built on top of it would be **tuned blind**. The Delphi panel
was explicit — "an unmeasured verification gate is theater regardless of retrieval
quality," and a labeled gold set is the *shared* prerequisite for both judge
calibration and retrieval-recall evaluation. Both studies also recommended tiered
verification (cheap pass for all claims, expensive escalation only when needed) and
warned it cannot be tuned without that gold set.

This feature makes the gate measurable on a user-owned, labeled gold set, then uses
that measurement to calibrate a cheap→expensive tiered router. Eval and tiering
share one dependency — the gold set — by design.

orc already provides most of the machinery: the benchmark harness has the scoring
math (`_confusion`/`_scores`/`_per_source_breakdown` in `benchmarks/faithfulness/run.py`),
`verify_claim.run()` already accepts a per-call `model` and `mode` (so cross-family
judging and tiered escalation need no core surgery), there is a precedent labeled
format (`tests/fixtures/claims.yaml`), and the schema is cleanly versioned for an
additive bump.

## Goals

1. A per-workspace **gold set** of human-confirmed (claim → verdict) labels, seeded
   by import and grown by promoting real verdicts.
2. `orc eval` that measures the gate on that gold set: judge accuracy
   (precision/recall/F1 per mode and domain), confidence **calibration**, and
   **retrieval recall** where chunk-level labels exist.
3. A **tiered_verify** strategy: cheap Haiku pass → escalate to an expensive
   (optionally cross-family) judge when confidence is below a *calibrated*
   threshold, with the deciding tier recorded in the trace.
4. `orc eval calibrate` that closes the loop: derive the escalation threshold from
   the gold set so tiering is never tuned blind.

## Non-goals (this iteration)

- Corpus provenance/freshness controls (the "faithful-but-wrong corpus content"
  failure mode no gold set can catch — documented, not built).
- A hosted/web calibration dashboard.
- Automatic gold-set generation (gold entries are always human-confirmed).

## Data model

New per-workspace table, additive **schema v2** (current is v1; `db.py` stamps
`SCHEMA_VERSION` in `schema_meta`):

```sql
CREATE TABLE gold_claim (
    gold_id            TEXT PRIMARY KEY,          -- ULID
    workspace          TEXT NOT NULL,
    claim              TEXT NOT NULL,
    expected_label     TEXT NOT NULL,             -- supported|contradicted|not_found|partial
    corpus_version     INTEGER NOT NULL,          -- snapshot the label is valid against
    relevant_chunk_ids TEXT,                      -- JSON list, nullable (retrieval-recall gold)
    source             TEXT NOT NULL,             -- import|promoted
    source_run_id      TEXT,                      -- the run a promoted label came from
    note               TEXT,
    added_at           TEXT NOT NULL,
    added_by           TEXT
);
CREATE INDEX idx_gold_claim_workspace ON gold_claim(workspace);
```

**Corpus-version pinning is load-bearing.** Chunk IDs change on re-ingest, so
`relevant_chunk_ids` are valid only for the `corpus_version` they were labeled
against. Retrieval-recall eval therefore runs **frozen** against each entry's
`corpus_version` (reusing the replay machinery — `bm25_search`/`retrieve()` already
accept `corpus_version`). Judge-accuracy labels (the verdict) survive re-ingest;
only chunk-relevance is version-bound. Eval flags entries whose `corpus_version`
lags the workspace's current version so stale chunk labels are visible, not silent.

The schema bump needs a real migration step (the current `db.py` has only a broad
`suppress(Exception)` ALTER): add a minimal forward migration that creates
`gold_claim` on open when `schema_version < 2`, then re-stamps. (This also retires
an existing low-severity finding about unchecked schema version.)

## Metrics library (extraction)

Move the benchmark-private scoring into an importable `src/orc/metrics/` package so
both `benchmarks/` and `orc eval` share one implementation:

- `metrics/confusion.py` — `confusion(items, *, predicted, expected, positive_label) -> Confusion`
- `metrics/scores.py` — `scores(confusion) -> Scores` (accuracy, precision, recall, F1)
- `metrics/breakdown.py` — `per_group(items, key, ...) -> dict[str, GroupResult]`
- `metrics/calibration.py` — **new**: `reliability_bins(items, n_bins=10) -> list[Bin]`
  and `expected_calibration_error(bins) -> float`. A bin holds
  (confidence range, count, predicted_mean, actual_accuracy).

`benchmarks/faithfulness/run.py` is updated to import these instead of its private
copies (no behavioral change; the published numbers must stay identical — pinned by
the existing benchmark tests).

## Gold-set CLI

- `orc eval import <file.yaml> -w WS` — seed from the `claims.yaml` format
  (`id`, `text`, `expected`, optional `relevant_chunk_ids`, optional `note`).
  Stamps `source=import` and the workspace's current `corpus_version`.
- `orc eval label <run_id> --verdict <label> [--relevant <chunk_id>...] [--note ...]`
  — promote/correct a real verdict into gold. Pulls `claim` and `corpus_version`
  from the trace (`load_trace`), stamps `source=promoted`, `source_run_id`.
- `orc eval gold list -w WS [--json]` — list entries (with stale-version flag).

## `orc eval run`

For every gold claim in the workspace, verify against its pinned `corpus_version`
and compute:

- **Judge accuracy** — `confusion` → `scores`, broken down per mode and per domain.
  The 4-label verdict is mapped to correct/incorrect against `expected_label`
  (exact match; `partial` is its own class, not folded into FAIL as the benchmark
  does — eval is about the gate's own labels, not a binary PASS/FAIL task).
- **Calibration** — reliability bins + Expected Calibration Error over predicted
  confidence. This is the artifact that surfaces the escalation threshold.
- **Retrieval recall@k** — for entries with `relevant_chunk_ids`, the fraction of
  labeled-relevant chunks that frozen retrieval surfaced in the top k.

Each eval is itself auditable and replayable: an `eval_run` row
(`eval_id`, `workspace`, `created_at`, `config_json`, `metrics_json`) plus the
per-claim verify Runs it spawned — each a normal trace, tagged with `eval_id` in its
inputs. `orc eval show <eval_id> [--json]` prints the report (console table by
default). The per-claim runs mean an eval can be inspected claim-by-claim and
replayed like any other orc run.

## Tiered verification

A new `tiered_verify` meta-strategy, a sibling to the existing `decomposed` and
`arithmetic` meta-modes. **Refactor note:** `verify_claim.py` is ~800 lines and
already dispatches `decomposed`/`arithmetic` to internal `_run_*` helpers; extract
those plus the new tiered strategy into a `directives/research/skills/modes/`
submodule (`modes/decomposed.py`, `modes/arithmetic.py`, `modes/tiered.py`) so the
core `verify_claim.run()` stays a thin dispatcher. This is a targeted improvement of
code being touched, not an unrelated refactor.

Tier policy:

- **Tier 1** — Haiku, binary mode, on every claim (cheap).
- **Escalate to Tier 2** — Sonnet, evidence mode + decomposed — when Tier-1
  confidence `<` the calibrated threshold.
- **Top-tier judge model is configurable.** Default Sonnet (Anthropic). A user may
  set a true cross-family judge (e.g. a GPT/Gemini/Llama model via OpenRouter) to
  break the self-consistency bias both studies flagged. orc already routes any model
  string and handles OpenRouter, so this is configuration, not new plumbing.
- The trace records **which tier decided and why**: both verdicts, the Tier-1
  confidence, the threshold, and the escalation reason. Tiering is auditable.

`tiered_verify` is reachable via `mode="tiered"` and can be wired into
`route_to_mode` for a domain that should default to it.

### The calibration loop

`orc eval calibrate -w WS [--target 0.95] [--tier1-model ...] [--top-judge ...]`:

1. Run the gold set through **Tier 1 only** (Haiku binary), recording each verdict's
   confidence and correctness.
2. Sweep the confidence threshold; find the lowest cutoff at which Tier-1-accepted
   claims reach the `--target` accuracy (default **0.95**).
3. **Achievability guard:** if no cutoff reaches the target (Tier-1 accuracy caps
   below it at every confidence level), report that plainly — *"Tier 1 cannot reach
   0.95 at any cutoff on this gold set (max 0.91 at conf≥0.97); escalating all
   claims — lower --target or improve the gold set"* — rather than silently writing
   an always-escalate policy.
4. **Always report the resulting escalation rate** (fraction of gold claims that
   would escalate at the chosen threshold) so the cost implication is visible
   immediately.
5. Write a `tiered_policy` row into `orc.db` (one row per workspace, replacing any
   prior policy — keyed by workspace, same store as `gold_claim`/`eval_run` so it
   travels with the workspace backup and is queryable):
   `{workspace, tier1_model, tier2_model, top_judge_model?, escalation_threshold,
   target, calibrated_at, calibrated_against_eval_id, n_gold}`. (The effects
   allow-list stays in `config.toml`; calibration state is data, not policy a human
   hand-edits, so it lives in the DB.)

`tiered_verify` reads `tiered_policy`; if absent, it falls back to a documented
default threshold and warns once that tiering is uncalibrated. This is the loop the
studies demanded: tiering is tuned on the gold set, never blind, and the policy
records which eval calibrated it.

**Why 0.95:** orc is verification-first, so the default leans toward quality (only
auto-accept a cheap verdict when it is ~95% trustworthy). The achievability guard
plus escalation-rate reporting prevent the over-escalation failure mode. `--target`
lets cost-sensitive users dial it down.

## Honesty / coverage ceiling

The coverage-ceiling docs (README, `docs/compliance/eu-ai-act.md`,
`docs/positioning/competitive.md`) gain a sentence: `orc eval` measures judge
accuracy and retrieval recall **against the user's own labels** — it quantifies how
well the gate matches the gold set, and cannot detect faithful-but-wrong corpus
content (the third failure-mode class; no gold set can). A stale gold set produces
confident-but-miscalibrated gating, so eval flags stale-corpus-version entries and
`tiered_policy` records when it was last calibrated.

## Testing

All TDD, no network, against a deterministic fake LLM (the existing `tests/_fake_llm`
pattern) and `FakeEmbedder` where retrieval is involved:

- `metrics/` — hand-computed confusion matrices, scores, reliability bins, and a
  known-ECE fixture. Benchmark tests must stay green after the extraction (proves no
  behavioral drift).
- gold CLI — import round-trip, label/promote pulls claim+corpus_version from a real
  trace, stale-version flagging, `--json`.
- `orc eval run` — judge accuracy on a scripted gold set (fake verdicts), calibration
  bins, retrieval recall@k with `relevant_chunk_ids`, eval_run + per-claim run trace
  tagging, frozen corpus_version pinning.
- `tiered_verify` — Tier-1 accept above threshold, escalation below it, top-judge
  model override reaches the Tier-2 call (assert the model string), trace records
  both verdicts + escalation reason, uncalibrated fallback warns.
- `orc eval calibrate` — threshold sweep finds the right cutoff on a scripted
  reliability curve, the achievability guard fires when the target is unreachable,
  escalation rate reported, `tiered_policy` persisted and read back by `tiered_verify`.

## Build order (staged plan)

1. **Metrics extraction** — `src/orc/metrics/` (confusion/scores/breakdown +
   calibration); rewire `benchmarks/` to import it; benchmark tests stay green.
2. **Gold store** — `gold_claim` table + schema-v2 migration + gold CLI
   (import / label / list).
3. **`orc eval run` / `show`** — judge accuracy + calibration + retrieval recall;
   `eval_run` table + per-claim run tagging.
4. **`tiered_verify`** — `modes/` extraction + the tiered strategy + trace records.
5. **`orc eval calibrate`** — threshold sweep + achievability guard + `tiered_policy`
   + wire `tiered_verify` to read it.
6. **Docs** — coverage-ceiling sentence, CHANGELOG Unreleased entries, README
   commands.

Each stage is independently testable and shippable; stages 4–5 depend on 1–3.

## Open questions

None blocking. The `--target` default (0.95) is decided with the achievability guard
above; the cross-family top judge is opt-in configuration with an Anthropic default.
