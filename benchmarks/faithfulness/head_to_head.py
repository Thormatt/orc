"""Same-set head-to-head: Orc vs. Vectara HHEM-2.1-Open on identical items.

Unlike `run.py --hhem` (which re-runs Orc and HHEM together, spending live LLM
budget on the Orc pass), this script *reuses* an existing Orc results.json and
only runs the (free, self-hosted) HHEM pass on the exact same items, matched by
id. That makes the comparison genuinely same-set and costs nothing on the Orc
side.

HHEM-2.1-Open ships vendored modeling code written for transformers 4.x; it
breaks on transformers 5.x (`all_tied_weights_keys`). The `benchmarks` extra
pins `transformers<5` for this reason — that is the fix for the long-standing
"HHEM tokenizer-load issue."

    uv run python -m benchmarks.faithfulness.head_to_head \
        --orc-run benchmarks/faithfulness/results/20260519-191250/results.json

Output: a same-set table (Orc vs HHEM), a per-source breakdown, and a
truncation split (HHEM has a 512-token window) so a reviewer can see how much
of the gap — if any — is attributable to HHEM not seeing long passages.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = Path(__file__).parent / "halubench-stratified-504.jsonl"
THRESHOLD = 0.5  # HHEM P(consistent) >= THRESHOLD -> PASS (faithful)
TOKENS_PER_WORD = 1.3  # rough proxy for HHEM's 512-token window


def _load(orc_run: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    ds = {json.loads(line)["id"]: json.loads(line) for line in DATASET_PATH.open()}
    orc_items = [
        it
        for it in json.loads(orc_run.read_text())["items"]
        if it.get("orc_binary") and it.get("id") in ds
    ]
    return orc_items, ds


def _score(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    from orc.metrics.scoring import LabeledResult, confusion, scores

    cm = confusion(
        [LabeledResult(predicted=r[key], expected=r["gt"]) for r in rows], positive="PASS"
    )
    return {**scores(cm), **{f"cm_{k}": v for k, v in cm.items()}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--orc-run",
        type=Path,
        required=True,
        help="Path to an existing Orc faithfulness results.json to reuse verdicts from",
    )
    parser.add_argument("--threads", type=int, default=10, help="torch CPU threads")
    parser.add_argument("--out", type=Path, default=None, help="Write results JSON here")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT / "src"))
    import torch

    torch.set_num_threads(args.threads)
    from transformers import AutoModelForSequenceClassification

    orc_items, ds = _load(args.orc_run)
    print(f"reusing {len(orc_items)} Orc verdicts from {args.orc_run.name}")

    rows: list[dict[str, Any]] = []
    for it in orc_items:
        d = ds[it["id"]]
        words = len((d["passage"] + " " + d["answer"]).split())
        rows.append(
            {
                "id": it["id"],
                "source_ds": it["source_ds"],
                "gt": it["ground_truth"],
                "orc": it["orc_binary"],
                "passage": d["passage"],
                "answer": d["answer"],
                "est_tok": int(words * TOKENS_PER_WORD),
            }
        )

    print("loading HHEM-2.1-Open (self-hosted, free)…")
    model = AutoModelForSequenceClassification.from_pretrained(
        "vectara/hallucination_evaluation_model", trust_remote_code=True
    )
    model.eval()

    print(f"scoring {len(rows)} items (premise=passage, hypothesis=answer, threshold={THRESHOLD})…")
    t0 = time.monotonic()
    batch = 8
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        scores_out = model.predict([(r["passage"], r["answer"]) for r in chunk])
        for r, s in zip(chunk, scores_out, strict=True):
            r["hhem_score"] = float(s)
            r["hhem"] = "PASS" if float(s) >= THRESHOLD else "FAIL"
        if (i // batch) % 10 == 0:
            print(f"  {min(i + batch, len(rows))}/{len(rows)}")
    print(f"  done in {time.monotonic() - t0:.0f}s\n")

    orc_s, hhem_s = _score(rows, "orc"), _score(rows, "hhem")
    print("=" * 70)
    print(f"SAME-SET HEAD-TO-HEAD  ·  n={len(rows)}  ·  PASS = faithful")
    print("=" * 70)
    for name, s in (("orc", orc_s), ("hhem", hhem_s)):
        print(
            f"  {name:5} F1={s['f1']:.4f}  acc={s['accuracy']:.4f}  "
            f"P={s['precision']:.4f}  R={s['recall']:.4f}"
        )

    fits = [r for r in rows if r["est_tok"] <= 512]
    trunc = [r for r in rows if r["est_tok"] > 512]
    print(f"\ntruncation split (HHEM 512-token window; ~{len(fits)}/{len(rows)} fit):")
    for label, sub in (("fits<=512", fits), ("truncated", trunc)):
        if sub:
            print(
                f"  {label:10} n={len(sub):3}  orc F1={_score(sub,'orc')['f1']:.3f}  "
                f"hhem F1={_score(sub,'hhem')['f1']:.3f}"
            )

    print("\nper source_ds (F1):")
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[r["source_ds"]].append(r)
    for src in sorted(by):
        rs = by[src]
        print(
            f"  {src:13} n={len(rs):3}  orc={_score(rs,'orc')['f1']:.3f}  "
            f"hhem={_score(rs,'hhem')['f1']:.3f}"
        )

    if args.out:
        args.out.write_text(
            json.dumps({"orc": orc_s, "hhem": hhem_s, "n": len(rows), "rows": rows}, indent=2)
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
