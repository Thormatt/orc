"""orc vs. a raw LLM call over a *large, private* corpus.

The single-passage demo (`orc_vs_raw.py`) is the case where a raw call is
strongest: the whole source fits in the prompt. This demo is the opposite — a
knowledge base of many documents the model has never seen — which is where a
verification runtime earns its keep:

  * You cannot paste the whole corpus into every prompt (cost, context limits,
    and on a real corpus it simply does not fit). You must *retrieve*.
  * The answer to a claim lives in one specific document among many. A reviewer
    needs to know *which* one — a citation, not a vibe.
  * A raw call asked to "cite the source" will invent a plausible-sounding one.

The corpus under `demos/corpus/` is a small, deliberately fictional internal
knowledge base for a made-up company ("Helix Freight Systems"). Fictional on
purpose: the model cannot answer from memory, so the demo isolates the value of
grounding + citation rather than the model's world knowledge.

    uv run python -m demos.orc_large_corpus --live

Live LLM spend is gated behind --live (or ORC_DEMO_ALLOW_LIVE_LLM=1).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = Path(__file__).parent / "corpus"

# (claim, what we expect, why it's a good probe)
CLAIMS = [
    (
        "The 2026-03-14 Helix Freight checkout outage was caused by an expired TLS certificate.",
        "false — the postmortem explicitly rules out TLS and names pool exhaustion",
    ),
    (
        "The 2026-03-14 Helix Freight checkout outage lasted 73 minutes.",
        "true — stated in the postmortem; unknowable without the corpus",
    ),
]


def _response_text(response: Any) -> str:
    return "".join(
        getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
    ).strip()


def run_raw(claim: str) -> str:
    """Ask the model, with no corpus access, to judge the claim and cite a source.

    This is the 'ask the chatbot' baseline. The corpus is fictional, so any
    citation it produces is fabricated by construction.
    """
    from orc.llm.client import get_client, messages_create, resolve_model_for_provider
    from orc.llm.models import resolve_verify_model

    model = resolve_verify_model(None)
    prompt = (
        "You are an analyst verifying a claim about Helix Freight Systems' internal "
        "operations. State whether the claim is true or false and cite the specific "
        "source document you relied on.\n\n"
        f"CLAIM: {claim}"
    )
    response = messages_create(
        get_client(),
        model=resolve_model_for_provider(model),
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return _response_text(response)


def ingest_corpus(orc_home: Path) -> tuple[Any, int]:
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.storage import workspace as ws_module

    ws = ws_module.create("helix")
    do_ingest(ws, str(CORPUS_DIR))
    from orc.paths import workspace_db_path
    from orc.storage.db import open_connection

    with open_connection(workspace_db_path("helix")) as conn:
        n_chunks = conn.execute("SELECT COUNT(*) AS c FROM chunk").fetchone()["c"]
    return ws, n_chunks


def run_orc(ws: Any, claim: str) -> dict[str, Any]:
    from orc import directives
    from orc.runs import open_run

    skill = directives.get("research").skills["verify_claim"]
    kwargs = {"claim": claim, "mode": "evidence"}
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": claim}) as run:
        run.record_effective_kwargs(kwargs)
        out = skill.run(workspace=ws, run=run, **kwargs)
        run.close(output=out)
    out["_run_id"] = run.run_id
    return out


def _cite_line(out: dict[str, Any]) -> str:
    chunks = (out.get("supporting_chunks") or []) + (out.get("contradicting_chunks") or [])
    if not chunks:
        return "citation    : none survived the retrieval-set guard"
    c = chunks[0]
    src = c.get("evidence_source_path") or c.get("evidence_title") or "?"
    src_name = Path(src).name if src != "?" else "?"
    return f"citation    : {src_name}  ·  chunk [{(c.get('chunk_id') or '?')[:12]}…]"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Acknowledge live LLM spend")
    args = parser.parse_args(argv)

    if not (args.live or os.environ.get("ORC_DEMO_ALLOW_LIVE_LLM") == "1"):
        print("Refusing to run: this makes live LLM calls. Pass --live to acknowledge.")
        return 2

    sys.path.insert(0, str(REPO_ROOT / "src"))
    n_docs = len(list(CORPUS_DIR.glob("*.md")))

    tmp_home = Path(tempfile.mkdtemp(prefix="orc-demo-corpus-"))
    os.environ["ORC_HOME"] = str(tmp_home)
    bar = "─" * 74
    try:
        ws, n_chunks = ingest_corpus(tmp_home)
        print(bar)
        print(f"CORPUS: {n_docs} private documents → {n_chunks} retrievable chunks")
        print("(a fictional internal knowledge base the model has never seen)")
        print(bar)
        for claim, expectation in CLAIMS:
            raw = run_raw(claim)
            orc = run_orc(ws, claim)
            print()
            print(f"CLAIM: {claim}")
            print(f"truth: {expectation}")
            print(bar)
            print("① RAW LLM (no corpus access — the 'ask the chatbot' baseline)")
            print(f"   {raw}")
            print()
            print("② ORC (retrieves across the corpus, cites the exact source)")
            print(f"   verdict     : {orc['label'].upper()}   confidence: {orc['confidence']:.2f}")
            print(f"   {_cite_line(orc)}")
            print(f"   reasoning   : {orc['reasoning'][:240]}")
            print(bar)
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
