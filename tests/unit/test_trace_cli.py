"""trace + replay CLI smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from orc.cli import main
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.runs import open_run
from orc.storage import workspace as ws_module
from tests._fake_llm import FakeAnthropic, make_verdict_response


def _seeded(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc A\n\nSkills API October 2025.\n")
    do_ingest(ws, str(corpus))
    return ws.name


def _verify_run(name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    from orc import directives

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": "x"}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output=result)
    return run.run_id


def test_trace_show_cli(orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _seeded(orc_home, tmp_path)
    run_id = _verify_run(name, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "show", run_id])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["run_id"] == run_id
    assert payload["skill"] == "verify_claim"


def test_trace_list_cli(orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _seeded(orc_home, tmp_path)
    run_id = _verify_run(name, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "list", "-w", name])
    assert result.exit_code == 0, result.output
    # Rich may truncate columns at narrow widths; use the run_id which is hard to
    # mistake for anything else and will be present whether truncated or not.
    assert run_id[:6] in result.output


def test_replay_cli_smoke(orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = _seeded(orc_home, tmp_path)
    original = _verify_run(name, monkeypatch)

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", original])
    assert result.exit_code == 0, result.output
    assert "frozen" in result.output
    assert "new_run_id" in result.output


def test_replay_cli_live_flag(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seeded(orc_home, tmp_path)
    original = _verify_run(name, monkeypatch)

    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", original, "--live"])
    assert result.exit_code == 0, result.output
    assert "live" in result.output
