"""Bootstrap the HaluBench stratified subsample used by the faithfulness benchmark.

Produces `halubench-stratified-504.jsonl`: 504 items, balanced 252 PASS / 252 FAIL,
84 items from each of the 6 source datasets (DROP, FinanceBench, RAGTruth, covidQA,
halueval, pubmedQA). Deterministic under `random.seed(42)`.

Run once, then the benchmark reads the local JSONL — no HuggingFace dependency at
benchmark time.

Usage:
    uv run python -m benchmarks.faithfulness.bootstrap
    # Requires: `uv pip install datasets` (one-time, outside the project's deps).
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

OUT_PATH = Path(__file__).parent / "halubench-stratified-504.jsonl"
SEED = 42
PER_CELL = 42  # 12 (source × label) cells × 42 = 504 items, balanced.


def main() -> None:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit(
            "bootstrap needs the `datasets` library. "
            "Install with `uv pip install datasets` (one-time)."
        ) from exc

    print("loading PatronusAI/HaluBench (test split)…")
    ds = load_dataset("PatronusAI/HaluBench", split="test")
    print(f"  loaded {len(ds)} rows")

    random.seed(SEED)
    buckets = defaultdict(list)
    for r in ds:
        buckets[(r["source_ds"], r["label"])].append(r)

    out = []
    for (src, lbl), rows in sorted(buckets.items()):
        chosen = random.sample(rows, min(PER_CELL, len(rows)))
        out.extend(chosen)
    random.shuffle(out)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w") as f:
        for r in out:
            f.write(
                json.dumps(
                    {k: r[k] for k in ("id", "passage", "question", "answer", "label", "source_ds")}
                )
                + "\n"
            )

    print(f"\nwrote {len(out)} items → {OUT_PATH}")
    print(f"  size: {OUT_PATH.stat().st_size // 1024} KB")
    print(f"  label balance: {dict(Counter(r['label'] for r in out))}")
    print(f"  source mix:    {dict(Counter(r['source_ds'] for r in out))}")


if __name__ == "__main__":
    main()
