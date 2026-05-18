"""Trace JSON schema versioning + replay-safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.runs import open_run
from orc.runs.replay import replay
from orc.runs.trace_schema import (
    LATEST_TRACE_SCHEMA_VERSION,
    SUPPORTED_TRACE_SCHEMA_VERSIONS,
    TraceSchemaError,
    assert_supported,
)
from orc.storage import workspace as ws_module
from orc.storage.trace_store import find_trace_path, load_trace
from tests._fake_llm import FakeAnthropic, make_verdict_response


def test_latest_is_in_supported_set() -> None:
    assert LATEST_TRACE_SCHEMA_VERSION in SUPPORTED_TRACE_SCHEMA_VERSIONS


def test_assert_supported_accepts_current() -> None:
    assert assert_supported({"schema_version": LATEST_TRACE_SCHEMA_VERSION}) == LATEST_TRACE_SCHEMA_VERSION


def test_assert_supported_defaults_missing_to_v1() -> None:
    """Pre-v1.x traces written before the field existed are still loadable."""
    assert assert_supported({}) == 1


def test_assert_supported_accepts_v1() -> None:
    assert assert_supported({"schema_version": 1}) == 1


def test_assert_supported_refuses_unknown_future_version() -> None:
    """The audit story requires that an unknown trace version fail loudly
    rather than silently being treated as 'close enough'."""
    with pytest.raises(TraceSchemaError) as excinfo:
        assert_supported({"schema_version": 99}, context="orc audit export")
    assert excinfo.value.found == 99
    assert "orc audit export" in str(excinfo.value)
    assert "99" in str(excinfo.value)


def test_assert_supported_refuses_negative_or_garbage() -> None:
    with pytest.raises(TraceSchemaError):
        assert_supported({"schema_version": 0})
    with pytest.raises(TraceSchemaError):
        assert_supported({"schema_version": "v1"})


def _seed(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc\n\nSkills API ships in October 2025.\n")
    do_ingest(ws, str(corpus))
    return ws.name


def _make_run(name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    from orc import directives

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": "x"}) as run:
        run.record_effective_kwargs({"claim": "x", "model": "claude-sonnet-4-6", "k": 10})
        result = skill.run(workspace=ws, run=run, claim="x")
        run.close(output=result)
    return run.run_id


def test_new_traces_are_written_at_latest_version(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: writes that don't go through trace_schema would silently
    diverge. This pins the wire-format version to the constant."""
    name = _seed(orc_home, tmp_path)
    run_id = _make_run(name, monkeypatch)
    trace = load_trace(run_id)
    assert trace["schema_version"] == LATEST_TRACE_SCHEMA_VERSION


def test_replay_refuses_unsupported_schema_version(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a trace claims a version this build doesn't understand, replay must
    refuse rather than execute with potentially-wrong semantics."""
    import json

    name = _seed(orc_home, tmp_path)
    run_id = _make_run(name, monkeypatch)

    # Tamper with the on-disk trace JSON to simulate a future-version trace.
    trace_path = find_trace_path(name, run_id)
    payload = json.loads(trace_path.read_text())
    payload["schema_version"] = 99
    trace_path.write_text(json.dumps(payload))

    with pytest.raises(TraceSchemaError) as excinfo:
        replay(run_id)
    assert excinfo.value.found == 99
    # Error message must name the offending version + the supported range so
    # operators have actionable information.
    msg = str(excinfo.value)
    assert "99" in msg
    assert str(SUPPORTED_TRACE_SCHEMA_VERSIONS) in msg


def test_replay_accepts_legacy_v1_traces(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trace written under v1 (no effective_kwargs) must still replay,
    routed through the legacy reconstruction path."""
    import json

    name = _seed(orc_home, tmp_path)
    run_id = _make_run(name, monkeypatch)

    # Downgrade the on-disk trace to look like a v1 trace.
    trace_path = find_trace_path(name, run_id)
    payload = json.loads(trace_path.read_text())
    payload["schema_version"] = 1
    payload.pop("effective_kwargs", None)
    trace_path.write_text(json.dumps(payload))

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)

    out = replay(run_id)
    assert out["original_schema_version"] == 1
    assert out["kwargs_source"] == "legacy_inputs"


def test_replay_reports_original_schema_version_in_result(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replay payload returned to the CLI/MCP must surface the schema
    version of the original trace so an auditor can confirm what they're
    consuming."""
    name = _seed(orc_home, tmp_path)
    run_id = _make_run(name, monkeypatch)

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)

    out = replay(run_id)
    assert out["original_schema_version"] == LATEST_TRACE_SCHEMA_VERSION
