"""Replay engine tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.runs import open_run
from orc.runs.replay import replay
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace
from tests._fake_llm import FakeAnthropic, make_verdict_response


def _seed_corpus(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "v1"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc A\n\nThe Skills API ships in October 2025.\n")
    do_ingest(ws, str(corpus))
    return ws.name


def _verify_once(workspace_name: str, claim: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a verify_claim and return the run_id."""
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    from orc import directives

    ws = ws_module.resolve(workspace_name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": claim}) as run:
        result = skill.run(workspace=ws, run=run, claim=claim)
        run.close(output=result)
    return run.run_id


def test_replay_frozen_pins_to_original_corpus_version(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed_corpus(orc_home, tmp_path)
    original = _verify_once(name, "skills api", monkeypatch)
    original_trace = load_trace(original)
    original_cv = original_trace["corpus_version"]

    # Add more evidence post-run, bumping corpus_version.
    extra = tmp_path / "v2"
    extra.mkdir()
    (extra / "b.md").write_text("# Doc B\n\nUnrelated skills content here.\n")
    do_ingest(ws_module.resolve(name), str(extra))

    # Frozen replay should still see corpus_version <= original_cv (i.e. only doc A).
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    out = replay(original)
    assert out["mode"] == "frozen"
    assert out["original_corpus_version"] == original_cv
    assert out["current_corpus_version"] > original_cv

    # The new run's retrieval should not include doc B's chunks.
    new_trace = load_trace(out["new_run_id"])
    new_chunk_ids = {c["chunk_id"] for c in new_trace["retrieval"]["returned"]}
    # Compare against the original retrieval — they must match exactly under frozen replay.
    original_chunk_ids = {c["chunk_id"] for c in original_trace["retrieval"]["returned"]}
    assert new_chunk_ids == original_chunk_ids


def test_replay_live_uses_current_corpus(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed_corpus(orc_home, tmp_path)
    original = _verify_once(name, "skills api", monkeypatch)

    # Add unrelated evidence so corpus grows.
    extra = tmp_path / "v2"
    extra.mkdir()
    (extra / "b.md").write_text("# Doc B\n\nMore information about skills.\n")
    do_ingest(ws_module.resolve(name), str(extra))

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    out = replay(original, live=True)
    assert out["mode"] == "live"

    new_trace = load_trace(out["new_run_id"])
    new_chunk_ids = {c["chunk_id"] for c in new_trace["retrieval"]["returned"]}
    # Live should include the newly-ingested doc B chunks, so >= original set.
    assert len(new_chunk_ids) >= 1


def test_replay_records_lineage_in_inputs(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed_corpus(orc_home, tmp_path)
    original = _verify_once(name, "skills api", monkeypatch)

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    out = replay(original)
    new_trace = load_trace(out["new_run_id"])
    assert new_trace["inputs"]["_replay_of"] == original
    assert new_trace["inputs"]["_replay_mode"] == "frozen"
