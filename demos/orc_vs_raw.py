"""orc vs. a raw LLM call on a single, human-legible claim.

Runs the *same model* two ways against one HaluBench item, then prints both
outputs next to the ground-truth label so the difference is concrete rather
than aggregate:

  1. RAW LLM   — one plain `messages.create` call with the passage inline and
                 a "is this faithful? explain" prompt. No retrieval, no
                 structured verdict, no citation, no trace. This is the
                 "the model said so" baseline.
  2. ORC       — `verify_claim` in evidence mode (what `orc verify` runs):
                 BM25 retrieval over the ingested passage, a 4-label verdict
                 with calibrated confidence, chunk-level citations validated
                 against the retrieval set, and a replayable trace on disk.

Live LLM spend is gated behind --live (or ORC_DEMO_ALLOW_LIVE_LLM=1) so an
accidental run costs nothing.

    uv run python -m demos.orc_vs_raw --live
    uv run python -m demos.orc_vs_raw --live --item halueval-803

The item defaults to a subtle, readable hallucination (a fabricated award
name) so a non-technical reader can see who is right at a glance.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "benchmarks" / "faithfulness" / "halubench-stratified-504.jsonl"

DEFAULT_ITEM = "halueval-803"

# Curated items and the verify mode a real caller would route them to. Numeric
# financial claims go through arithmetic mode (a calculator the model invokes
# mid-verification, every call recorded in the trace); prose goes through the
# default evidence mode. `orc verify --domain financial` selects this in
# production.
MODE_BY_ITEM = {
    "halueval-803": "evidence",
    "financebench_id_02747": "arithmetic",
}


def _load_item(item_id: str) -> dict[str, Any]:
    if not DATASET_PATH.exists():
        raise SystemExit(
            f"dataset not found at {DATASET_PATH}. "
            "Run `uv run python -m benchmarks.faithfulness.bootstrap` once."
        )
    with DATASET_PATH.open() as f:
        for line in f:
            item = json.loads(line)
            if item["id"] == item_id:
                return item
    raise SystemExit(f"item {item_id!r} not found in {DATASET_PATH.name}")


def _response_text(response: Any) -> str:
    """Concatenate the text blocks of an Anthropic-style messages response."""
    parts = [
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip()


def run_raw(item: dict[str, Any]) -> dict[str, Any]:
    """Baseline: one plain LLM call, same model orc uses, passage inline.

    No retrieval, no tool schema, no citation, no trace — the output is whatever
    prose the model returns. This is what trusting a bare model call gives you.
    """
    from orc.llm.client import get_client, messages_create, resolve_model_for_provider
    from orc.llm.models import resolve_verify_model

    model = resolve_verify_model(None)
    prompt = (
        "You are a fact-checker. Using ONLY the source passage below, decide "
        "whether the claimed answer is faithful to it.\n\n"
        f"SOURCE PASSAGE:\n{item['passage']}\n\n"
        f"QUESTION: {item['question']}\n"
        f"CLAIMED ANSWER: {item['answer']}\n\n"
        "Is the claimed answer faithful to the passage? Answer in a sentence or two."
    )
    client = get_client()
    response = messages_create(
        client,
        model=resolve_model_for_provider(model),
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return {"model": model, "text": _response_text(response)}


def run_orc(item: dict[str, Any], orc_home: Path, mode: str) -> dict[str, Any]:
    """orc verify_claim against the ingested passage in the given mode."""
    from orc import directives
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.runs import open_run
    from orc.storage import workspace as ws_module

    corpus_dir = orc_home / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "passage.md").write_text(item["passage"])

    ws = ws_module.create("demo")
    do_ingest(ws, str(corpus_dir))

    claim = f"Q: {item['question']}\nA: {item['answer']}"
    skill = directives.get("research").skills["verify_claim"]
    kwargs = {"claim": claim, "mode": mode}
    with open_run(
        ws, directive="research", skill="verify_claim", inputs={"claim": claim}
    ) as run:
        run.record_effective_kwargs(kwargs)
        out = skill.run(workspace=ws, run=run, **kwargs)
        run.close(output=out)
    out["_run_id"] = run.run_id
    out["_mode"] = mode
    return out


def _format_orc(out: dict[str, Any]) -> str:
    lines = [
        f"verdict     : {out['label'].upper()}",
        f"confidence  : {out['confidence']:.2f}",
        f"reasoning   : {out['reasoning']}",
    ]
    supporting = out.get("supporting_chunks") or []
    contradicting = out.get("contradicting_chunks") or []
    for label, chunks in (("supporting", supporting), ("contradicting", contradicting)):
        for c in chunks:
            snippet = (c.get("text") or "").replace("\n", " ")[:120]
            lines.append(f"{label} cite: [{c.get('chunk_id', '?')[:12]}…] {snippet!r}")
    if out.get("_mode") in {"binary", "arithmetic"} and not supporting and not contradicting:
        lines.append(
            f"citations   : n/a in {out['_mode']} mode (every input chunk still recorded in the trace)"
        )
    elif not supporting and not contradicting:
        lines.append("citations   : none survived the retrieval-set guard")
    lines.append(
        f"trace       : traces/.../{out['_run_id']}.json  "
        f"(replay with `orc replay {out['_run_id']}`)"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", default=DEFAULT_ITEM, help="HaluBench item id")
    parser.add_argument("--live", action="store_true", help="Acknowledge live LLM spend")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON instead of prose")
    args = parser.parse_args(argv)

    if not (args.live or os.environ.get("ORC_DEMO_ALLOW_LIVE_LLM") == "1"):
        print("Refusing to run: this makes live LLM calls. Pass --live to acknowledge.")
        return 2

    sys.path.insert(0, str(REPO_ROOT / "src"))
    item = _load_item(args.item)

    gt = "FAITHFUL (PASS)" if item["label"] == "PASS" else "HALLUCINATED (FAIL)"

    mode = MODE_BY_ITEM.get(item["id"], "evidence")
    tmp_home = Path(tempfile.mkdtemp(prefix="orc-demo-"))
    os.environ["ORC_HOME"] = str(tmp_home)
    try:
        raw = run_raw(item)
        orc = run_orc(item, tmp_home, mode)
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)

    if args.json:
        print(json.dumps({"item": item, "raw": raw, "orc": orc}, indent=2, default=str))
        return 0

    bar = "─" * 72
    print(bar)
    print(f"ITEM {item['id']}  ·  source: {item['source_ds']}")
    print(bar)
    print(f"Question      : {item['question']}")
    print(f"Claimed answer: {item['answer']}")
    print(f"Ground truth  : {gt}")
    print()
    print("① RAW LLM  (one plain call, same model, no pipeline)")
    print(bar)
    print(f"model       : {raw['model']}")
    print(f"answer      : {raw['text']}")
    print("structured? : no   citation? : no   confidence? : no   trace/replay? : no")
    print()
    print(f"② ORC  (verify_claim, {mode} mode — what `orc verify` runs)")
    print(bar)
    print(_format_orc(orc))
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
