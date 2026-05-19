"""Tests for extract_claims, research_topic, and verify --file/--url."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from orc import directives
from orc.cli import main
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.runs import open_run
from orc.storage import workspace as ws_module
from tests._fake_llm import FakeAnthropic, FakeContentBlock, FakeResponse, FakeUsage


@dataclass
class _Plan:
    """Tiny scriptable responder for FakeAnthropic that switches on the tool requested."""

    extract_claims: list[dict[str, Any]] = field(default_factory=list)
    verdicts: dict[str, dict[str, Any]] = field(default_factory=dict)
    synthesis: dict[str, Any] | None = None

    def __call__(self, kwargs: dict[str, Any]) -> FakeResponse:
        tool = kwargs["tool_choice"]["name"]
        if tool == "record_claims":
            return FakeResponse(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        name="record_claims",
                        input={"claims": self.extract_claims},
                    )
                ],
                usage=FakeUsage(input_tokens=100, output_tokens=80),
            )
        if tool == "record_verdict":
            user_message = kwargs["messages"][0]["content"]
            for claim, verdict in self.verdicts.items():
                if claim in user_message:
                    return FakeResponse(
                        content=[
                            FakeContentBlock(type="tool_use", name="record_verdict", input=verdict)
                        ],
                        usage=FakeUsage(input_tokens=200, output_tokens=60),
                    )
            return FakeResponse(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        name="record_verdict",
                        input={
                            "label": "not_found",
                            "confidence": 0.5,
                            "reasoning": "default",
                            "supporting_chunk_ids": [],
                            "contradicting_chunk_ids": [],
                        },
                    )
                ],
                usage=FakeUsage(input_tokens=200, output_tokens=60),
            )
        if tool == "record_synthesis":
            assert self.synthesis is not None, "synthesis not configured"
            return FakeResponse(
                content=[
                    FakeContentBlock(type="tool_use", name="record_synthesis", input=self.synthesis)
                ],
                usage=FakeUsage(input_tokens=400, output_tokens=120),
            )
        raise RuntimeError(f"unexpected tool: {tool}")


def _seed(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "skills.md").write_text(
        "# Skills API\n\nAnthropic released the Skills API in October 2025.\n"
    )
    do_ingest(ws, str(corpus))
    return ws.name


def _install(monkeypatch: pytest.MonkeyPatch, plan: _Plan) -> FakeAnthropic:
    fake = FakeAnthropic(responder=plan)
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)
    return fake


def test_extract_claims_skill_returns_list(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed(orc_home, tmp_path)
    plan = _Plan(
        extract_claims=[
            {"text": "Skills API released October 2025", "context": "..."},
            {"text": "Skills are versioned", "context": "..."},
        ]
    )
    _install(monkeypatch, plan)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["extract_claims"]
    with open_run(ws, directive="research", skill="extract_claims", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, document="Skills API was released by Anthropic.")
        run.close(output=result)
    assert len(result["claims"]) == 2
    assert result["model"] == "claude-haiku-4-5"


def test_research_topic_skill_returns_synthesis(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed(orc_home, tmp_path)

    # Need to know a real chunk_id to put in supporting_chunk_ids
    from orc.paths import workspace_db_path
    from orc.storage.db import open_connection

    with open_connection(workspace_db_path(name)) as conn:
        chunk_id = conn.execute("SELECT chunk_id FROM chunk LIMIT 1").fetchone()["chunk_id"]

    plan = _Plan(
        synthesis={
            "summary": "The corpus describes the Skills API briefly.",
            "key_points": [
                {
                    "point": "Skills API was released in October 2025",
                    "supporting_chunk_ids": [chunk_id],
                },
                {"point": "fabricated_point", "supporting_chunk_ids": ["FAKE_ID_999"]},
            ],
            "gaps": "No deployment or pricing info.",
        }
    )
    _install(monkeypatch, plan)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["research_topic"]
    with open_run(ws, directive="research", skill="research_topic", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, topic="skills api")
        run.close(output=result)
    assert "Skills API" in result["summary"] or "skills" in result["summary"].lower()
    # Hallucinated chunk_id key_point dropped
    assert len(result["key_points"]) == 1
    assert result["key_points"][0]["supporting_chunk_ids"] == [chunk_id]


def test_research_topic_returns_silence_on_empty_corpus(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_module.create("demo")
    fake = FakeAnthropic()  # exhausted -> proves no LLM call made
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    ws = ws_module.resolve("demo")
    skill = directives.get("research").skills["research_topic"]
    with open_run(ws, directive="research", skill="research_topic", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, topic="anything")
        run.close(output=result)
    assert result["key_points"] == []
    assert "silent" in result["summary"].lower()


def test_cli_verify_from_file_extracts_and_verifies(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed(orc_home, tmp_path)
    # supported claims need a real chunk id, otherwise the citation guard
    # downgrades the label to not_found (which is exactly the bug we want
    # the guard to catch in real usage).
    from orc.paths import workspace_db_path
    from orc.storage.db import open_connection

    with open_connection(workspace_db_path(name)) as conn:
        chunk_id = conn.execute("SELECT chunk_id FROM chunk LIMIT 1").fetchone()["chunk_id"]

    plan = _Plan(
        extract_claims=[
            {"text": "Anthropic released the Skills API in October 2025", "context": ""},
            {"text": "Orc was acquired by Microsoft", "context": ""},
        ],
        verdicts={
            "Anthropic released": {
                "label": "supported",
                "confidence": 0.92,
                "reasoning": "Direct quote",
                "supporting_chunk_ids": [chunk_id],
                "contradicting_chunk_ids": [],
            },
            "Microsoft": {
                "label": "not_found",
                "confidence": 0.95,
                "reasoning": "Corpus silent on acquisition",
                "supporting_chunk_ids": [],
                "contradicting_chunk_ids": [],
            },
        },
    )
    _install(monkeypatch, plan)

    draft = tmp_path / "draft.md"
    draft.write_text(
        "# Notes\n\n"
        "Anthropic released the Skills API in October 2025. Orc was acquired by Microsoft.\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "--file", str(draft), "--workspace", name, "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "claim(s) extracted" in result.output
    assert "supported" in result.output.lower()
    assert "not_found" in result.output.lower()


def test_cli_verify_requires_input(orc_home: Path, tmp_path: Path) -> None:
    ws_module.create("demo")
    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--workspace", "demo"])
    assert result.exit_code != 0
    assert "Provide" in result.output or "Provide" in str(result.exception)
