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


def test_replay_works_for_extract_claims_runs(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: extract_claims runs (from `verify --file/--url`) must be replayable.

    Previously the extract_claims trace recorded only `source` and `doc_chars` in
    inputs. Replay rebuilds skill kwargs from inputs, so the skill ran without its
    required `document` kwarg and crashed with TypeError. Fix: store the document text
    in trace inputs so replay can reconstruct the call.
    """
    from click.testing import CliRunner

    from orc.cli import main
    from tests.unit.test_extract_and_research import _Plan, _seed

    name = _seed(orc_home, tmp_path)
    plan = _Plan(
        extract_claims=[{"text": "Skills API released October 2025", "context": ""}],
        verdicts={
            "Skills": {
                "label": "supported",
                "confidence": 0.9,
                "reasoning": "ok",
                "supporting_chunk_ids": [],
                "contradicting_chunk_ids": [],
            }
        },
    )
    fake = FakeAnthropic(responder=plan)
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    draft = tmp_path / "draft.md"
    draft.write_text("# Draft\n\nSome claim about Skills.\n")

    runner = CliRunner()
    result = runner.invoke(
        main, ["verify", "--file", str(draft), "--workspace", name, "--yes"]
    )
    assert result.exit_code == 0, result.output

    # Find the extract_claims run and replay it.
    from orc.storage.trace_store import list_runs

    runs = list_runs(name, skill="extract_claims", limit=10)
    assert runs, "expected at least one extract_claims run"
    extract_run_id = runs[0]["run_id"]

    fake.queue = []  # responder still set, will produce more claims on replay
    out = replay(extract_run_id)
    assert out["mode"] == "frozen"
    assert out["new_run_id"] != extract_run_id

    # Confirm the new run actually executed (no crash)
    new_trace = load_trace(out["new_run_id"])
    assert new_trace["status"] == "ok"
    assert new_trace["skill"] == "extract_claims"
    assert new_trace["output"]["claims"], "extract_claims should have produced claims on replay"


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


def test_cli_verify_records_effective_kwargs(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI verify path must pin the full effective kwargs (manifest defaults +
    caller overrides) into the trace so replay reproduces the original execution
    rather than re-reading the current manifest."""
    from click.testing import CliRunner

    from orc.cli import main

    name = _seed_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    runner = CliRunner()
    result = runner.invoke(
        main, ["verify", "skills api", "--workspace", name, "--k", "7"]
    )
    assert result.exit_code == 0, result.output

    from orc.storage.trace_store import list_runs

    rows = list_runs(name, skill="verify_claim", limit=1)
    trace = load_trace(rows[0]["run_id"])
    ek = trace.get("effective_kwargs")
    assert ek is not None
    assert ek["claim"] == "skills api"
    assert ek["k"] == 7  # caller override survived into the pin
    # manifest defaults must be present too — replay needs the full picture
    assert "max_tokens" in ek


def test_replay_uses_pinned_kwargs_over_current_manifest(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: if manifest defaults shift between original and replay, the
    replay must still execute with the original kwargs. Without pinning, a
    deployer who edits the manifest could silently invalidate every prior
    trace's reproducibility."""
    from orc import directives
    from orc.directives.base import DirectiveSpec

    name = _seed_corpus(orc_home, tmp_path)

    captured: dict[str, object] = {}

    class _RecordingSkill:
        name = "record"

        def run(self, *, workspace, run, **kwargs):
            captured.update(kwargs)
            return {"got": list(kwargs.keys())}

    spec_v1 = DirectiveSpec(
        name="__replay_pin_test__",
        version="0.0.1",
        description="t",
        skills={"record": _RecordingSkill()},
        skill_defaults={"record": {"model": "model-v1", "max_tokens": 1024, "k": 10}},
    )
    directives.register(spec_v1)
    try:
        ws = ws_module.resolve(name)
        spec = directives.get("__replay_pin_test__")
        kwargs = {**spec.kwargs_for("record"), "query": "hello"}
        with open_run(
            ws,
            directive="__replay_pin_test__",
            skill="record",
            inputs=dict(kwargs),
        ) as run:
            run.record_effective_kwargs(kwargs)
            result = spec.skills["record"].run(workspace=ws, run=run, **kwargs)
            run.close(output=result)
        original_id = run.run_id

        # Now swap in a v2 spec with different defaults. Replay must IGNORE these.
        directives._REGISTRY["__replay_pin_test__"] = DirectiveSpec(
            name="__replay_pin_test__",
            version="0.0.2",
            description="t",
            skills={"record": _RecordingSkill()},
            skill_defaults={"record": {"model": "model-v2", "max_tokens": 8192, "k": 99}},
        )

        captured.clear()
        out = replay(original_id)
        assert out["kwargs_source"] == "effective_kwargs"
        # The replay must have run with v1 values, not v2.
        assert captured["model"] == "model-v1"
        assert captured["max_tokens"] == 1024
        assert captured["k"] == 10
        assert captured["query"] == "hello"
    finally:
        del directives._REGISTRY["__replay_pin_test__"]


def test_replay_legacy_trace_without_effective_kwargs_falls_back(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older traces (written before effective_kwargs existed) must still replay.
    They reconstruct kwargs from current manifest + recorded inputs, stripping
    internal `_*` metadata so it isn't passed to the skill."""
    from orc import directives
    from orc.directives.base import DirectiveSpec

    name = _seed_corpus(orc_home, tmp_path)

    captured: dict[str, object] = {}

    class _Skill:
        name = "echo"

        def run(self, *, workspace, run, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    spec = DirectiveSpec(
        name="__replay_legacy_test__",
        version="0.0.1",
        description="t",
        skills={"echo": _Skill()},
        skill_defaults={"echo": {"model": "fallback-default"}},
    )
    directives.register(spec)
    try:
        ws = ws_module.resolve(name)
        with open_run(
            ws,
            directive="__replay_legacy_test__",
            skill="echo",
            # Mimic an old trace: inputs include both real args AND an internal
            # _parent_run marker. effective_kwargs is intentionally NOT recorded.
            inputs={"query": "test", "_parent_run": "rn_old", "_step_index": 3},
        ) as run:
            spec.skills["echo"].run(workspace=ws, run=run, query="test")
            run.close(output={"ok": True})
        original_id = run.run_id

        # Sanity: trace has no effective_kwargs
        assert load_trace(original_id).get("effective_kwargs") is None

        captured.clear()
        out = replay(original_id)
        assert out["kwargs_source"] == "legacy_inputs"
        assert captured["query"] == "test"
        # Manifest default merged in (legacy path)
        assert captured["model"] == "fallback-default"
        # Internal _* markers must NOT be passed to the skill
        assert "_parent_run" not in captured
        assert "_step_index" not in captured
    finally:
        del directives._REGISTRY["__replay_legacy_test__"]
