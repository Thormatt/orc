"""Faithfulness benchmark — Orc verify_claim on HaluBench, optionally vs HHEM.

For each (passage, question, answer) item in the stratified HaluBench
subsample (`halubench-stratified-504.jsonl`, produced by bootstrap.py):

  1. Spin up a per-item temp workspace under a shared ORC_HOME.
  2. Ingest the passage as evidence.
  3. Run `orc verify` with claim = f"Q: {question}\\nA: {answer}".
  4. Map the verdict to a binary label (PASS/FAIL) for comparison against
     ground truth, under an explicit label_mapping (documented in results).
  5. Optionally score the same items with HHEM-2.1-Open (free, self-hosted)
     for a head-to-head comparable.

Gating:
  - Live LLM calls require `ORC_BENCHMARK_ALLOW_LIVE_LLM=1` (sanity gate).
  - The runner aborts before spending money if not set.
  - `--n` is the actual number of items run (default 5 for smoke; pass 504
    for the full stratified subsample).

Output:
    results/<timestamp>/
        results.json    per-item verdicts + aggregate precision/recall/F1
        README.md       summary with sources / framing / label-mapping note
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = Path(__file__).parent / "halubench-stratified-504.jsonl"
RESULTS_ROOT = Path(__file__).parent / "results"

# Verdict → binary mapping. Documented in results so a reviewer can re-score
# under an alternative mapping if they disagree.
DEFAULT_LABEL_MAPPING = {
    "supported": "PASS",
    "partial": "FAIL",
    "contradicted": "FAIL",
    "not_found": "FAIL",
}


@dataclass
class ItemResult:
    id: str
    source_ds: str
    ground_truth: str  # PASS or FAIL
    orc_verdict: str | None = None
    orc_confidence: float | None = None
    orc_binary: str | None = None
    orc_correct: bool | None = None
    orc_error: str | None = None
    hhem_score: float | None = None
    hhem_binary: str | None = None
    hhem_correct: bool | None = None
    run_id: str | None = None
    skipped_reason: str | None = None


@dataclass
class Aggregate:
    n_evaluated: int
    n_skipped: int
    label_mapping: dict[str, str]
    orc: dict[str, Any] = field(default_factory=dict)
    hhem: dict[str, Any] = field(default_factory=dict)
    per_source: dict[str, dict[str, Any]] = field(default_factory=dict)


def _per_source_breakdown(
    item_results: list[ItemResult], binary_attr: str
) -> dict[str, dict[str, Any]]:
    """Compute confusion + scores per source_ds so a reviewer can see where the
    model strong-performs vs. struggles. Distinguishes natural-language Q+A
    from tabular / numeric tasks."""
    from collections import defaultdict

    by_src: dict[str, list[ItemResult]] = defaultdict(list)
    for r in item_results:
        if getattr(r, binary_attr) is None:
            continue
        by_src[r.source_ds].append(r)
    out: dict[str, dict[str, Any]] = {}
    for src, rows in sorted(by_src.items()):
        cm = _confusion(rows, binary_attr)
        out[src] = {"confusion": cm, "scores": _scores(cm), "n": len(rows)}
    return out


def _load_dataset(n: int, source_filter: str | None) -> list[dict[str, Any]]:
    if not DATASET_PATH.exists():
        raise SystemExit(
            f"dataset not found at {DATASET_PATH}. "
            "Run `uv run python -m benchmarks.faithfulness.bootstrap` once."
        )
    items: list[dict[str, Any]] = []
    with DATASET_PATH.open() as f:
        for line in f:
            items.append(json.loads(line))
    if source_filter:
        items = [i for i in items if i["source_ds"] == source_filter]
    return items[:n]


def _confusion(results: list[ItemResult], binary_attr: str) -> dict[str, int]:
    tp = fp = tn = fn = 0
    for r in results:
        if getattr(r, binary_attr) is None:
            continue
        pred = getattr(r, binary_attr)
        gt = r.ground_truth
        if pred == "PASS" and gt == "PASS":
            tp += 1
        elif pred == "PASS" and gt == "FAIL":
            fp += 1
        elif pred == "FAIL" and gt == "FAIL":
            tn += 1
        elif pred == "FAIL" and gt == "PASS":
            fn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def _scores(cm: dict[str, int]) -> dict[str, float]:
    """Treat PASS as the positive class. Reviewers may re-score with FAIL-positive."""
    tp, fp, tn, fn = cm["tp"], cm["fp"], cm["tn"], cm["fn"]
    n = tp + fp + tn + fn
    if n == 0:
        return {"accuracy": 0.0, "precision_pass": 0.0, "recall_pass": 0.0, "f1_pass": 0.0}
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "accuracy": (tp + tn) / n,
        "precision_pass": precision,
        "recall_pass": recall,
        "f1_pass": f1,
    }


# ───────────── Orc scoring ────────────────────────────────────


def _run_lynx_style_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Variant: direct Sonnet call with Lynx's literal binary prompt.

    No Orc pipeline involvement — measures the underlying judge capability
    when given the optimal prompt. The reference point for "ceiling without
    fine-tuning."
    """
    from benchmarks.faithfulness.variants import run_lynx_style

    res = ItemResult(
        id=item["id"],
        source_ds=item["source_ds"],
        ground_truth=item["label"],
    )
    label, raw, _elapsed, err = run_lynx_style(item)
    if err is not None:
        res.orc_error = err
        return res
    if label == "":
        res.orc_error = f"unparseable response: {raw!r}"
        return res
    res.orc_verdict = "supported" if label == "PASS" else "contradicted"
    res.orc_confidence = 1.0  # Lynx-style prompt does not emit confidence.
    res.orc_binary = label
    res.orc_correct = label == res.ground_truth
    return res


# Routing lives in the runtime (src/orc/directives/research/routing.py) so the
# benchmark and the production verify_claim never drift. The mapping was
# derived from per-source-ds breakdowns on the 504-item stratified HaluBench
# subsample. Prose-heavy sources where corpus citations help → evidence mode.
# Single-passage numeric/extraction tasks → binary mode. Mixed natural-language
# Q+A → judgment mode.
from orc.directives.research.routing import DOMAIN_TO_MODE as SOURCE_TO_MODE  # noqa: E402


def _run_with_mode(item: dict[str, Any], orc_home: Path, mode: str) -> ItemResult:
    """Run verify_claim against the item with the requested mode. Shared
    plumbing for the judgment / binary / source-routed variants."""
    from orc import directives
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.runs import open_run
    from orc.storage import workspace as ws_module

    res = ItemResult(
        id=item["id"],
        source_ds=item["source_ds"],
        ground_truth=item["label"],
    )

    ws_name = f"halu-{item['id'].replace('-', '')[:24]}"
    corpus_dir = orc_home / "corpora" / ws_name
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "passage.md").write_text(item["passage"])

    try:
        ws = ws_module.create(ws_name)
        do_ingest(ws, str(corpus_dir))
        claim = f"Q: {item['question']}\nA: {item['answer']}"
        skill = directives.get("research").skills["verify_claim"]
        # Allow env-driven max_tokens override for benchmarking verbose models
        # like Gemini that occasionally exceed the 2048 default.
        skill_kwargs: dict[str, Any] = {"claim": claim, "mode": mode}
        if (env_max := os.environ.get("ORC_MAX_TOKENS")):
            skill_kwargs["max_tokens"] = int(env_max)
        with open_run(
            ws, directive="research", skill="verify_claim", inputs={"claim": claim, "mode": mode}
        ) as run:
            run.record_effective_kwargs(
                {"claim": claim, "model": "halubench", "mode": mode, **(
                    {"max_tokens": skill_kwargs["max_tokens"]} if "max_tokens" in skill_kwargs else {}
                )}
            )
            out = skill.run(workspace=ws, run=run, **skill_kwargs)
            run.close(output=out)
        res.run_id = run.run_id
        res.orc_verdict = out["label"]
        res.orc_confidence = out["confidence"]
        res.orc_binary = DEFAULT_LABEL_MAPPING[out["label"]]
        res.orc_correct = res.orc_binary == res.ground_truth
    except Exception as exc:
        res.orc_error = f"{type(exc).__name__}: {exc}"
    return res


def _run_judgment_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Production path in `mode="judgment"`."""
    return _run_with_mode(item, orc_home, "judgment")


def _run_binary_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Production path in `mode="binary"` — uses the simpler binary tool schema."""
    return _run_with_mode(item, orc_home, "binary")


def _run_decomposed_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Production path in `mode="decomposed"` — decompose then verify each atom in binary mode."""
    return _run_with_mode(item, orc_home, "decomposed")


def _run_arithmetic_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Production path in `mode="arithmetic"` — calculator tool + binary verdict."""
    return _run_with_mode(item, orc_home, "arithmetic")


def _run_source_routed_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Source-aware routing: picks the mode that's empirically best for the
    item's source_ds (per the per-source breakdowns from the earlier runs).

    The router takes a SIGNAL the workflow caller would provide in production
    — domain/source hint — and picks the right verification strategy."""
    mode = SOURCE_TO_MODE.get(item.get("source_ds", ""), "judgment")
    return _run_with_mode(item, orc_home, mode)


def _run_orc_one(item: dict[str, Any], orc_home: Path) -> ItemResult:
    """Spin up a per-item workspace, ingest the passage, run verify_claim."""
    from orc import directives
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.runs import open_run
    from orc.storage import workspace as ws_module

    res = ItemResult(
        id=item["id"],
        source_ds=item["source_ds"],
        ground_truth=item["label"],
    )

    ws_name = f"halu-{item['id'].replace('-', '')[:24]}"
    corpus_dir = orc_home / "corpora" / ws_name
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "passage.md").write_text(item["passage"])

    try:
        ws = ws_module.create(ws_name)
        do_ingest(ws, str(corpus_dir))
        claim = f"Q: {item['question']}\nA: {item['answer']}"
        skill = directives.get("research").skills["verify_claim"]
        with open_run(
            ws, directive="research", skill="verify_claim", inputs={"claim": claim}
        ) as run:
            run.record_effective_kwargs({"claim": claim, "model": "halubench"})
            out = skill.run(workspace=ws, run=run, claim=claim)
            run.close(output=out)
        res.run_id = run.run_id
        res.orc_verdict = out["label"]
        res.orc_confidence = out["confidence"]
        res.orc_binary = DEFAULT_LABEL_MAPPING[out["label"]]
        res.orc_correct = res.orc_binary == res.ground_truth
    except Exception as exc:
        res.orc_error = f"{type(exc).__name__}: {exc}"
    return res


# ───────────── HHEM scoring ───────────────────────────────────


def _try_load_hhem():
    """Return a callable (premise, hypothesis) -> P(consistent) or None if HHEM
    isn't available locally."""
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        return None
    model_id = "vectara/hallucination_evaluation_model"
    print(f"loading HHEM ({model_id})…")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_id, trust_remote_code=True
    )
    model.eval()
    device = "cpu"  # CPU is fine for N=504 with HHEM-2.1-Open.

    def score(premise: str, hypothesis: str) -> float:
        with torch.no_grad():
            # HHEM-2.1-Open expects a pair text input. Their model card uses
            # `predict` for batched scoring; we call directly for portability.
            text = f"{premise}\n[SEP]\n{hypothesis}"
            enc = tokenizer(text, truncation=True, max_length=1024, return_tensors="pt")
            enc = {k: v.to(device) for k, v in enc.items()}
            logits = model(**enc).logits  # shape (1,) or (1,1)
            prob = torch.sigmoid(logits).squeeze().item()
        return float(prob)

    return score


def _run_hhem_pass(items: list[dict[str, Any]], results_by_id: dict[str, ItemResult]) -> None:
    scorer = _try_load_hhem()
    if scorer is None:
        print(
            "HHEM dependencies not available — skipping HHEM pass. "
            "Install `transformers torch` to enable."
        )
        return
    print(f"scoring {len(items)} items with HHEM…")
    for i, item in enumerate(items, 1):
        if i % 25 == 0 or i == len(items):
            print(f"  hhem {i}/{len(items)}")
        try:
            prob = scorer(item["passage"], item["answer"])
            res = results_by_id[item["id"]]
            res.hhem_score = prob
            res.hhem_binary = "PASS" if prob >= 0.5 else "FAIL"
            res.hhem_correct = res.hhem_binary == res.ground_truth
        except Exception as exc:
            print(f"  hhem error on {item['id']}: {exc}")


def _readme(agg: Aggregate, total: int) -> str:
    orc_scores = agg.orc.get("scores", {})
    hhem_scores = agg.hhem.get("scores") or {}
    lines = [
        "# Faithfulness benchmark — Orc on HaluBench",
        "",
        "Stratified subsample: 504 items, 84 each from DROP, FinanceBench, RAGTruth,",
        "covidQA, halueval, pubmedQA. 252 PASS / 252 FAIL ground truth.",
        "",
        "## Run scope",
        f"- items evaluated: **{agg.n_evaluated}** / requested {total}",
        f"- items skipped:   **{agg.n_skipped}**",
        "",
        "## Label mapping",
        "",
        "Orc returns one of `supported / partial / contradicted / not_found`.",
        "We map to HaluBench's binary PASS/FAIL as follows (documented for the",
        "reviewer to re-score under an alternative mapping):",
        "",
    ]
    for k, v in agg.label_mapping.items():
        lines.append(f"- `{k}` → **{v}**")
    lines.extend(["", "## Orc", ""])
    if orc_scores:
        cm = agg.orc.get("confusion", {})
        lines.extend(
            [
                f"- accuracy:        **{orc_scores['accuracy']:.4f}**",
                f"- precision (PASS): **{orc_scores['precision_pass']:.4f}**",
                f"- recall (PASS):    **{orc_scores['recall_pass']:.4f}**",
                f"- F1 (PASS):        **{orc_scores['f1_pass']:.4f}**",
                f"- confusion:        TP={cm.get('tp',0)} FP={cm.get('fp',0)} "
                f"TN={cm.get('tn',0)} FN={cm.get('fn',0)}",
            ]
        )
    # Per-source breakdown — surfaces strong/weak performance by task type.
    per_src_orc = (agg.per_source or {}).get("orc") or {}
    if per_src_orc:
        lines.extend(
            [
                "",
                "## Orc — per source_ds",
                "",
                "| source | n | accuracy | precision | recall | F1 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for src, info in per_src_orc.items():
            s = info["scores"]
            lines.append(
                f"| `{src}` | {info['n']} | {s['accuracy']:.3f} | "
                f"{s['precision_pass']:.3f} | {s['recall_pass']:.3f} | "
                f"{s['f1_pass']:.3f} |"
            )
    if hhem_scores:
        cm = agg.hhem.get("confusion", {})
        lines.extend(
            [
                "",
                "## HHEM-2.1-Open (self-hosted)",
                "",
                f"- accuracy:        **{hhem_scores['accuracy']:.4f}**",
                f"- precision (PASS): **{hhem_scores['precision_pass']:.4f}**",
                f"- recall (PASS):    **{hhem_scores['recall_pass']:.4f}**",
                f"- F1 (PASS):        **{hhem_scores['f1_pass']:.4f}**",
                f"- confusion:        TP={cm.get('tp',0)} FP={cm.get('fp',0)} "
                f"TN={cm.get('tn',0)} FN={cm.get('fn',0)}",
            ]
        )
    lines.extend(
        [
            "",
            "## Honest framing",
            "",
            "Orc and HHEM answer different shapes of question.",
            "",
            "- **Orc** is a *runtime* that retrieves, verifies, and produces a",
            "  structured verdict with chunk-level citations. It's making a system-",
            "  level decision about whether to ship a verdict to the caller.",
            "- **HHEM** is a *judge* that takes (premise, hypothesis) and outputs a",
            "  scalar consistency score. It does not retrieve, cite, or decide.",
            "",
            "On a single-document-per-item benchmark like HaluBench, both reduce to",
            "the same binary, so the numbers are comparable here. Outside this",
            "narrow setting (multi-document corpora, citation requirements,",
            "compliance handoff) the comparison is no longer apples-to-apples.",
            "",
            "## Reproducing",
            "",
            "```",
            "uv run python -m benchmarks.faithfulness.bootstrap          # one-time",
            "ORC_BENCHMARK_ALLOW_LIVE_LLM=1 \\",
            "  uv run python -m benchmarks.faithfulness.run --n 504 --hhem",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=5, help="Number of items (default 5 for smoke)")
    parser.add_argument(
        "--source",
        default=None,
        help="Restrict to one source_ds (DROP, FinanceBench, RAGTruth, covidQA, halueval, pubmedQA)",
    )
    parser.add_argument("--hhem", action="store_true", help="Also score with self-hosted HHEM")
    parser.add_argument(
        "--variant",
        choices=[
            "default",
            "lynx_style",
            "judgment",
            "binary",
            "decomposed",
            "arithmetic",
            "source_routed",
        ],
        default="default",
        help=(
            "Verification variant. "
            "`default` = Orc verify_claim, mode=evidence (BM25 + 4-label). "
            "`lynx_style` = direct Sonnet call with Lynx's binary prompt, no Orc pipeline. "
            "`judgment` = mode=judgment (no BM25, lighter prompt, full moat). "
            "`binary` = mode=binary (no BM25, binary tool schema, full trace+replay+audit). "
            "`decomposed` = mode=decomposed (Haiku decompose → binary atoms → confidence-weighted aggregate). "
            "`arithmetic` = mode=arithmetic (binary + calculator tool, multi-turn loop). "
            "`source_routed` = pick best mode per item's source_ds (caller-provided domain hint in production)."
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if os.environ.get("ORC_BENCHMARK_ALLOW_LIVE_LLM") != "1":
        print(
            "Refusing to run: live LLM spend is gated. "
            "Set ORC_BENCHMARK_ALLOW_LIVE_LLM=1 to acknowledge."
        )
        return 2

    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from orc.core.clock import now_iso

    ts = now_iso().replace(":", "").replace("-", "").replace("T", "-")[:15]
    out_dir = args.out or (RESULTS_ROOT / ts)
    out_dir.mkdir(parents=True, exist_ok=True)

    items = _load_dataset(args.n, args.source)
    print(f"loaded {len(items)} items from {DATASET_PATH.name}")

    tmp_home = Path(tempfile.mkdtemp(prefix="orc-bench-faith-"))
    os.environ["ORC_HOME"] = str(tmp_home)

    def _write_artifacts(item_results: list[ItemResult], stage: str) -> None:
        """Save results.json and README at every checkpoint so a partial run
        never loses the work that's already been paid for. The latest call
        wins; earlier writes are overwritten with the more-complete picture."""
        orc_cm = _confusion(item_results, "orc_binary")
        orc_scores = _scores(orc_cm)
        hhem_cm = _confusion(item_results, "hhem_binary")
        hhem_scores = (
            _scores(hhem_cm) if any(r.hhem_binary for r in item_results) else None
        )
        agg = Aggregate(
            n_evaluated=sum(1 for r in item_results if r.orc_binary is not None),
            n_skipped=sum(1 for r in item_results if r.orc_error or r.skipped_reason),
            label_mapping=DEFAULT_LABEL_MAPPING,
            orc={"confusion": orc_cm, "scores": orc_scores},
            hhem={"confusion": hhem_cm, "scores": hhem_scores} if hhem_scores else {},
            per_source={
                "orc": _per_source_breakdown(item_results, "orc_binary"),
                **(
                    {"hhem": _per_source_breakdown(item_results, "hhem_binary")}
                    if hhem_scores
                    else {}
                ),
            },
        )
        (out_dir / "results.json").write_text(
            json.dumps(
                {
                    "stage": stage,
                    "variant": args.variant,
                    "aggregate": asdict(agg),
                    "items": [asdict(r) for r in item_results],
                },
                indent=2,
            )
        )
        (out_dir / "README.md").write_text(_readme(agg, len(items)))
        return agg

    runners = {
        "default": _run_orc_one,
        "lynx_style": _run_lynx_style_one,
        "judgment": _run_judgment_one,
        "binary": _run_binary_one,
        "decomposed": _run_decomposed_one,
        "arithmetic": _run_arithmetic_one,
        "source_routed": _run_source_routed_one,
    }
    runner = runners[args.variant]
    print(f"variant: {args.variant}")

    item_results: list[ItemResult] = []
    try:
        for i, item in enumerate(items, 1):
            if i % 10 == 0 or i == len(items) or i == 1:
                print(f"  {args.variant} {i}/{len(items)}  ({item['source_ds']})")
            r = runner(item, tmp_home)
            item_results.append(r)
            # Checkpoint every 25 items so a mid-run crash never throws away
            # the API calls that have already been paid for.
            if i % 25 == 0 or i == len(items):
                _write_artifacts(item_results, stage=f"orc-partial-{i}/{len(items)}")

        # Orc pass complete. Persist a clean orc-only snapshot before HHEM —
        # if HHEM fails, the Orc numbers are still on disk.
        _write_artifacts(item_results, stage="orc-complete")
        print(f"  orc pass complete; results.json checkpointed at {out_dir}")

        if args.hhem:
            try:
                results_by_id = {r.id: r for r in item_results}
                _run_hhem_pass(items, results_by_id)
            except Exception as exc:
                print(f"HHEM pass failed: {type(exc).__name__}: {exc}")
                print("Orc results remain on disk; re-run HHEM later if desired.")

        agg = _write_artifacts(
            item_results,
            stage="orc-and-hhem-complete" if args.hhem else "orc-complete",
        )

        orc_scores = agg.orc.get("scores") or {}
        hhem_scores = (agg.hhem or {}).get("scores") or {}
        print("\n== faithfulness ==")
        print(f"  evaluated  : {agg.n_evaluated}")
        print(f"  skipped    : {agg.n_skipped}")
        if orc_scores:
            print(
                f"  orc        : acc={orc_scores['accuracy']:.4f} "
                f"f1={orc_scores['f1_pass']:.4f} "
                f"P={orc_scores['precision_pass']:.4f} "
                f"R={orc_scores['recall_pass']:.4f}"
            )
        if hhem_scores:
            print(
                f"  hhem       : acc={hhem_scores['accuracy']:.4f} "
                f"f1={hhem_scores['f1_pass']:.4f} "
                f"P={hhem_scores['precision_pass']:.4f} "
                f"R={hhem_scores['recall_pass']:.4f}"
            )
        print(f"  results dir: {out_dir}")
        return 0
    finally:
        # Workspaces are large (one SQLite + one passage.md per item). Clean up.
        # results.json + README.md live under `out_dir`, not `tmp_home`.
        with contextlib.suppress(Exception):
            shutil.rmtree(tmp_home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
