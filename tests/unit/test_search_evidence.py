"""End-to-end test of the `search_evidence` skill via the directive registry + Run."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from orc import directives
from orc.cli import main
from orc.ingest.pipeline import ingest as do_ingest
from orc.runs import open_run
from orc.storage import workspace as ws_module


def _make_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "skills.md").write_text(
        "# Skills API\n\nAnthropic released the Skills API in October 2025.\n"
    )
    (corpus / "context.md").write_text(
        "# Context engineering\n\nContext engineering is iterative and curated per call.\n"
    )
    return corpus


def test_search_evidence_skill_returns_top_chunk(orc_home: Path, tmp_path: Path) -> None:
    ws = ws_module.create("demo")
    do_ingest(ws, str(_make_corpus(tmp_path)))

    skill = directives.get("research").skills["search_evidence"]
    with open_run(
        ws, directive="research", skill="search_evidence", inputs={"query": "skills"}
    ) as run:
        result = skill.run(workspace=ws, run=run, query="skills api", k=5)
        run.close(output=result)

    chunks = result["chunks"]
    assert chunks
    assert chunks[0]["evidence_title"] == "Skills API"
    assert chunks[0]["rank"] == 0
    # Run.retrieval was populated and run_evidence rows exist
    assert run.retrieval is not None
    assert run.retrieval["method"] == "bm25"


def test_cli_search_smoke(orc_home: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["workspace", "create", "demo"])
    runner.invoke(main, ["ingest", str(_make_corpus(tmp_path)), "--workspace", "demo"])
    result = runner.invoke(main, ["search", "skills api", "--workspace", "demo"])
    assert result.exit_code == 0, result.output
    assert "Skills API" in result.output


def test_cli_search_json_output(orc_home: Path, tmp_path: Path) -> None:
    import json

    runner = CliRunner()
    runner.invoke(main, ["workspace", "create", "demo"])
    runner.invoke(main, ["ingest", str(_make_corpus(tmp_path)), "--workspace", "demo"])
    result = runner.invoke(main, ["search", "skills", "--workspace", "demo", "--json", "--k", "3"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["k"] == 3
    assert "chunks" in payload
