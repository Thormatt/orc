# Orc verification benchmarks

Reproducible measurements of Orc's runtime invariants and verification
quality. The full design lives in [`docs/benchmarks/plan.md`](../docs/benchmarks/plan.md).

## What's here

| Path | Status | What it measures |
|---|---|---|
| `citation_enforcement/` | shipped | Fraction of fabricated chunk_ids that reach the caller across N=100 `verify_claim` runs driven by an adversarial fake LLM. Headline metric: **leak rate**. |
| `bundle_invariants/`    | planned | Audit-bundle structural invariants (manifest-hash integrity, run↔trace coverage, chunk_id→evidence_id closure). |
| `faithfulness/`         | planned | Orc verdict labels vs HaluBench/RAGTruth ground truth. Cost-bounded; gated behind `ORC_BENCHMARK_ALLOW_LIVE_LLM`. |
| `replay_portability/`   | deferred | Replay an exported bundle on a clean machine; depends on the future `orc audit import` command. |

## How a benchmark is structured

Each benchmark directory contains:

- `run.py` — entry point: `uv run python -m benchmarks.<name>.run`
- `dataset.yaml` — the inputs the benchmark runs against
- `results/<timestamp>/` — per-run output. **Not** checked in.
  - `results.json` — per-case detail + aggregate
  - `audit.tar.gz` — Orc's audit export of the benchmark's own runs
  - `manifest.json` — extracted from the bundle for quick inspection
  - `README.md` — human-readable summary

The benchmark *itself* runs through Orc: each case opens a Run, writes a
trace, and is included in the audit bundle. The bundle is the artifact a
reviewer re-runs to validate any published number.

## Reproducing a result

```
git clone https://github.com/Thormatt/orc
cd orc
uv sync
uv run python -m benchmarks.citation_enforcement.run --n 100
```

Then `cat benchmarks/citation_enforcement/results/<timestamp>/README.md`.

## Why not just unit tests?

The runtime invariants tested here *are* covered by unit tests too. The
difference is intent: a benchmark produces a citeable number with a real
audit bundle behind it. A reviewer can verify the bundle hashes, replay any
of its constituent runs, and reproduce the aggregate independently.
