"""Golden tests for verify_claim against a known fixture corpus.

These tests make REAL Anthropic API calls. They are skipped unless both:
- ORC_TEST_ALLOW_LIVE_LLM=1 is set, AND
- ANTHROPIC_API_KEY is set in the environment.

Run them with:
    ORC_TEST_ALLOW_LIVE_LLM=1 uv run pytest tests/golden/ -v

The pass threshold is >= 8/10. This is the load-bearing acceptance test for v1:
when this turns green, ship.

The same scaffolding is also exercised in `test_golden_framework_with_fakes` using
a fake client, so the test plumbing itself is exercised on every CI run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from orc import directives
from orc.ingest.pipeline import ingest as do_ingest
from orc.runs import open_run
from orc.storage import workspace as ws_module

LIVE_LLM_ENV = "ORC_TEST_ALLOW_LIVE_LLM"
PASS_THRESHOLD = 0.8


_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_CORPUS = _FIXTURES / "test_corpus"
_CLAIMS = _FIXTURES / "claims.yaml"


def _load_claims() -> list[dict]:
    return yaml.safe_load(_CLAIMS.read_text())["claims"]


def _setup_workspace(orc_home: Path) -> str:
    ws = ws_module.create("golden")
    do_ingest(ws, str(_CORPUS))
    return ws.name


@pytest.mark.skipif(
    not os.environ.get(LIVE_LLM_ENV) or not os.environ.get("ANTHROPIC_API_KEY"),
    reason=(
        f"Live LLM tests need {LIVE_LLM_ENV}=1 and ANTHROPIC_API_KEY. "
        "These tests cost money. See tests/golden/test_verify_known_claims.py."
    ),
)
def test_verify_golden_claims_meets_threshold(orc_home: Path) -> None:
    name = _setup_workspace(orc_home)
    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    claims = _load_claims()

    results: list[tuple[str, str, str, float]] = []
    for c in claims:
        with open_run(
            ws, directive="research", skill="verify_claim", inputs={"claim": c["text"]}
        ) as run:
            r = skill.run(workspace=ws, run=run, claim=c["text"])
            run.close(output=r)
        results.append((c["id"], c["expected"], r["label"], r["confidence"]))

    matched = sum(1 for _id, exp, got, _conf in results if got == exp)
    total = len(results)
    rate = matched / total

    detail = "\n".join(
        f"  [{'OK' if got == exp else 'MISS'}] {id_:35s} expected={exp:13s} got={got:13s} conf={conf:.2f}"
        for id_, exp, got, conf in results
    )
    assert rate >= PASS_THRESHOLD, (
        f"Only {matched}/{total} claims matched expected ({rate:.0%}, threshold {PASS_THRESHOLD:.0%}):\n"
        + detail
    )


@pytest.mark.skipif(
    not os.environ.get(LIVE_LLM_ENV) or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Same gating as the main golden test.",
)
def test_verify_golden_claims_use_prompt_cache(orc_home: Path) -> None:
    """Calling verify twice in succession should hit the prompt cache the second time."""
    name = _setup_workspace(orc_home)
    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]

    claim = "The Orc CLI uses click."
    cache_creation_first: int = 0
    cache_read_second: int = 0

    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        skill.run(workspace=ws, run=run, claim=claim)
        run.close(output={})
        cache_creation_first = sum(c.cache_creation_input_tokens for c in run.llm_calls)

    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        skill.run(workspace=ws, run=run, claim=claim)
        run.close(output={})
        cache_read_second = sum(c.cache_read_input_tokens for c in run.llm_calls)

    assert cache_creation_first > 0, "first call should have written to cache"
    assert cache_read_second > 0, "second call should have read from cache"


def test_golden_framework_with_fakes(orc_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same plumbing, fake client. Always runs — exercises the test framework itself."""
    from orc.llm import client as client_module
    from tests._fake_llm import FakeAnthropic, make_verdict_response

    name = _setup_workspace(orc_home)
    ws = ws_module.resolve(name)

    # Pre-build canned responses keyed to expected verdicts. Each call gets the next.
    claims = _load_claims()
    fake = FakeAnthropic(
        responses=[make_verdict_response(label=c["expected"], confidence=0.85) for c in claims]
    )
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    skill = directives.get("research").skills["verify_claim"]
    matched = 0
    for c in claims:
        with open_run(
            ws, directive="research", skill="verify_claim", inputs={"claim": c["text"]}
        ) as run:
            r = skill.run(workspace=ws, run=run, claim=c["text"])
            run.close(output=r)
        if r["label"] == c["expected"]:
            matched += 1

    assert matched / len(claims) >= PASS_THRESHOLD
