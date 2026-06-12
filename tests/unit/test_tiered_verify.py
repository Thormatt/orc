"""tiered_verify: cheap Tier-1 pass, escalate to Tier-2 below a threshold."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc import directives
from orc.eval.policy import save_policy
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.paths import workspace_db_path
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection
from tests._fake_llm import FakeAnthropic, FakeContentBlock, FakeResponse, make_verdict_response


def _binary(*, faithful: bool, confidence: float) -> FakeResponse:
    return FakeResponse(
        content=[
            FakeContentBlock(
                type="tool_use",
                name="record_binary_verdict",
                input={"faithful": faithful, "confidence": confidence, "reasoning": "r"},
            )
        ]
    )


def _setup(orc_home: Path, tmp_path: Path) -> tuple[str, str, int]:
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.md").write_text("# Doc\n\nThe sky is blue on a clear day.\n")
    do_ingest(ws, str(corpus))
    with open_connection(workspace_db_path("demo")) as conn:
        cid = conn.execute("SELECT chunk_id FROM chunk ORDER BY seq LIMIT 1").fetchone()["chunk_id"]
    return ws.name, cid, ws_module.resolve("demo").corpus_version


def _run(name: str, **kwargs) -> dict:
    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, **kwargs)
        run.close(output=result)
    return result


def test_tier1_accepts_above_threshold_without_escalating(orc_home, tmp_path, monkeypatch) -> None:
    name, _cid, cv = _setup(orc_home, tmp_path)
    save_policy(name, tier1_model="claude-haiku-4-5", tier2_model="claude-sonnet-4-6",
                top_judge_model=None, escalation_threshold=0.9, target=0.95,
                calibrated_against_eval_id=None, n_gold=10)
    # Tier-1 binary returns high confidence -> accept, no Tier-2 call.
    fake = FakeAnthropic(responses=[_binary(faithful=True, confidence=0.99)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    result = _run(name, claim="The sky is blue", mode="tiered", corpus_version=cv)
    assert result["label"] == "supported"
    assert result["tier"] == 1
    assert result["escalated"] is False
    assert len(fake.calls) == 1  # only Tier-1 ran


def test_low_confidence_escalates_to_tier2(orc_home, tmp_path, monkeypatch) -> None:
    name, cid, cv = _setup(orc_home, tmp_path)
    save_policy(name, tier1_model="claude-haiku-4-5", tier2_model="claude-sonnet-4-6",
                top_judge_model=None, escalation_threshold=0.9, target=0.95,
                calibrated_against_eval_id=None, n_gold=10)
    # Tier-1 low confidence -> escalate; Tier-2 evidence verdict decides.
    fake = FakeAnthropic(responses=[
        _binary(faithful=True, confidence=0.5),
        make_verdict_response(label="contradicted", confidence=0.95, contradicting_chunk_ids=[cid]),
    ])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    result = _run(name, claim="The sky is blue", mode="tiered", corpus_version=cv)
    assert result["tier"] == 2
    assert result["escalated"] is True
    assert result["label"] == "contradicted"
    assert len(fake.calls) == 2


def test_uncalibrated_workspace_warns_and_uses_default(orc_home, tmp_path, monkeypatch) -> None:
    name, _cid, cv = _setup(orc_home, tmp_path)  # no save_policy
    fake = FakeAnthropic(responses=[_binary(faithful=True, confidence=0.99)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    with pytest.warns(UserWarning, match="not calibrated"):
        result = _run(name, claim="The sky is blue", mode="tiered", corpus_version=cv)
    assert result["tier"] == 1  # default threshold still routes
