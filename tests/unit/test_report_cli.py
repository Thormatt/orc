"""`orc report` CLI tests.

Most tests write trace JSON straight into the workspace traces dir (the
cheapest fixture that load_trace can find); one end-to-end test drives the
real verify pipeline the same way test_trace_cli.py does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from orc.cli import main
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.trace_store import write_trace_json
from tests._fake_llm import FakeAnthropic, make_verdict_response


def _trace_dict(run_id: str, *, claim: str = "Skills API shipped in 2025.") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "run_id": run_id,
        "directive": "research",
        "skill": "verify_claim",
        "workspace": "demo",
        "corpus_version": 1,
        "started_at": "2026-06-01T08:00:00Z",
        "ended_at": "2026-06-01T08:00:31Z",
        "status": "ok",
        "model": "claude-sonnet-4-6",
        "inputs": {"claim": claim},
        "effective_kwargs": {"k": 6},
        "events": [],
        "retrieval": {"method": "bm25", "candidates_considered": 4, "returned": []},
        "llm_calls": [
            {
                "model": "claude-sonnet-4-6",
                "input_tokens": 500,
                "output_tokens": 80,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "elapsed_ms": 700,
            }
        ],
        "output": {
            "claim": claim,
            "label": "supported",
            "confidence": 0.9,
            "reasoning": "Stated verbatim in the corpus.",
            "supporting_chunks": [],
            "contradicting_chunks": [],
            "missing_information": None,
        },
        "error_message": None,
    }


def _seed_trace(run_id: str) -> None:
    payload = _trace_dict(run_id)
    write_trace_json("demo", run_id, payload["started_at"], payload)


def test_report_writes_html_to_stdout_by_default(orc_home: Path) -> None:
    _seed_trace("01STDOUTRUN")

    result = CliRunner().invoke(main, ["report", "01STDOUTRUN"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("<!doctype html>")
    assert "01STDOUTRUN" in result.output


def test_report_o_writes_file_and_echoes_path(orc_home: Path, tmp_path: Path) -> None:
    _seed_trace("01FILEDRUN")
    out = tmp_path / "report.html"

    result = CliRunner().invoke(main, ["report", "01FILEDRUN", "-o", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "01FILEDRUN" in out.read_text()
    assert str(out) in result.output
    assert "<!doctype html>" not in result.output


def test_report_multiple_run_ids_renders_multi_claim_report(orc_home: Path) -> None:
    _seed_trace("01MULTIAAA")
    _seed_trace("01MULTIBBB")

    result = CliRunner().invoke(main, ["report", "01MULTIAAA", "01MULTIBBB"])

    assert result.exit_code == 0, result.output
    assert "01MULTIAAA" in result.output
    assert "01MULTIBBB" in result.output
    assert result.output.count('<article class="claim"') == 2


def test_report_unknown_run_id_is_a_clean_error(orc_home: Path) -> None:
    _seed_trace("01KNOWNRUN")

    result = CliRunner().invoke(main, ["report", "01NOSUCHRUN"])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Error" in result.output
    assert "01NOSUCHRUN" in result.output


def test_report_open_without_output_errors(orc_home: Path) -> None:
    _seed_trace("01OPENRUN")

    result = CliRunner().invoke(main, ["report", "01OPENRUN", "--open"])

    assert result.exit_code != 0
    assert "--open requires -o" in result.output


def test_report_cli_end_to_end(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same seeding approach as test_trace_cli.py: a real workspace, a real
    # ingest, and a verify run against a fake LLM — the report renders a
    # trace produced by the actual pipeline, not a hand-built dict.
    from orc import directives

    ws = ws_module.create("demo")
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc A\n\nSkills API October 2025.\n")
    do_ingest(ws, str(corpus))

    # "supported" with no cited chunk ids would be structurally downgraded by
    # verify_claim; "not_found" is the honest verdict a chunkless fake can return.
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": "x"}) as run:
        result_out = skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output=result_out)

    out = tmp_path / "e2e.html"
    result = CliRunner().invoke(main, ["report", run.run_id, "-o", str(out)])

    assert result.exit_code == 0, result.output
    html_doc = out.read_text()
    assert run.run_id in html_doc
    assert 'data-verdict="nf"' in html_doc
    assert "<style>" in html_doc
