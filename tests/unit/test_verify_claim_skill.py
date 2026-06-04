"""verify_claim skill tests with a fake Anthropic client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from orc import directives
from orc.cli import main
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.paths import workspace_db_path, workspace_traces_dir
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection
from tests._fake_llm import FakeAnthropic, FakeContentBlock, FakeResponse, make_verdict_response


def _make_binary_response(*, faithful: bool, confidence: float, reasoning: str = "ok") -> FakeResponse:
    return FakeResponse(
        content=[
            FakeContentBlock(
                type="tool_use",
                name="record_binary_verdict",
                input={
                    "faithful": faithful,
                    "confidence": confidence,
                    "reasoning": reasoning,
                },
            )
        ],
    )


def _make_decomposition_response(atoms: list[str]) -> FakeResponse:
    return FakeResponse(
        content=[
            FakeContentBlock(
                type="tool_use",
                name="record_decomposition",
                input={"atoms": atoms},
            )
        ],
    )


def _setup_corpus(orc_home: Path, tmp_path: Path) -> str:
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "skills.md").write_text(
        "# Skills API\n\n"
        "Anthropic released the Skills API in October 2025. Skills are versioned, auditable\n"
        "capabilities Claude composes at runtime.\n"
    )
    (corpus / "context.md").write_text(
        "# Context engineering\n\nContext engineering is iterative.\n"
    )
    do_ingest(ws, str(corpus))
    return ws.name


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, fake: FakeAnthropic) -> None:
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)


def _supporting_chunk_id(workspace_name: str, evidence_title: str) -> str:
    with open_connection(workspace_db_path(workspace_name)) as conn:
        row = conn.execute(
            "SELECT chunk.chunk_id FROM chunk "
            "JOIN evidence ON evidence.evidence_id = chunk.evidence_id "
            "WHERE evidence.title = ? ORDER BY chunk.seq LIMIT 1",
            (evidence_title,),
        ).fetchone()
    assert row is not None, f"no chunk for {evidence_title!r}"
    return row["chunk_id"]


def test_verify_supported_returns_label_and_records_trace(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    chunk_id = _supporting_chunk_id(name, "Skills API")

    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="supported",
                confidence=0.92,
                reasoning="Chunk affirms the claim.",
                supporting_chunk_ids=[chunk_id],
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": "x"}) as run:
        result = skill.run(
            workspace=ws, run=run, claim="Anthropic released the Skills API in October 2025"
        )
        run.close(output=result)

    assert result["label"] == "supported"
    assert result["confidence"] == pytest.approx(0.92)
    assert any(c["chunk_id"] == chunk_id for c in result["supporting_chunks"])
    assert result["model"] == "claude-sonnet-4-6"

    # Trace JSON exists with retrieval + llm_calls + output
    traces = list(workspace_traces_dir(name).rglob(f"{run.run_id}.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text())
    assert trace["retrieval"]["method"] == "bm25"
    assert len(trace["llm_calls"]) == 1
    assert trace["output"]["label"] == "supported"

    # run_evidence has retrieved + supporting roles
    with open_connection(workspace_db_path(name)) as conn:
        roles = {
            r["role"]
            for r in conn.execute(
                "SELECT role FROM run_evidence WHERE run_id = ?", (run.run_id,)
            ).fetchall()
        }
    assert "retrieved" in roles
    assert "supporting" in roles


def test_verify_not_found_when_corpus_empty(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty corpus -> no LLM call, automatic not_found verdict."""
    ws_module.create("demo")
    fake = FakeAnthropic()  # exhausted on call -> proves no LLM call happened
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve("demo")
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="anything")
        run.close(output=result)

    assert result["label"] == "not_found"
    assert result["supporting_chunks"] == []
    assert fake.calls == []  # never called the LLM


def test_verify_drops_hallucinated_chunk_ids(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every cited chunk is hallucinated, the label must downgrade to
    not_found — shipping `supported` with empty citations is the bug this
    test guards against."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="supported",
                confidence=0.7,
                supporting_chunk_ids=["FAKEID12345", "ANOTHERFAKE"],
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output=result)

    assert result["supporting_chunks"] == []
    assert result["label"] == "not_found"  # downgraded from supported

    # Trace should record both the dropped IDs and the downgrade flag.
    traces = list(workspace_traces_dir(name).rglob(f"{run.run_id}.json"))
    trace = json.loads(traces[0].read_text())
    response_meta = trace["llm_calls"][0]["response"]
    assert set(response_meta["dropped_chunk_ids"]) == {"FAKEID12345", "ANOTHERFAKE"}
    assert response_meta["label_downgraded"] is True


def test_verify_partial_with_no_valid_citations_downgrades_to_not_found(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `partial` verdict whose every cited chunk was hallucinated has no
    grounding either — it must downgrade to not_found, same as supported /
    contradicted. Otherwise `partial` is a citation-guard bypass."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="partial",
                confidence=0.5,
                supporting_chunk_ids=["01ARZ3NDEKTSV4RRFFQ69G5FAV"],
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output=result)

    assert result["supporting_chunks"] == []
    assert result["label"] == "not_found"


def test_verify_redacts_hallucinated_ids_in_reasoning_prose(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structured citation arrays are filtered, but the free-text `reasoning`
    can also smuggle chunk IDs (the tool schema asks the model to cite them in
    prose). A chunk ID in reasoning that is not in the retrieval set must not
    reach the caller verbatim."""
    name = _setup_corpus(orc_home, tmp_path)
    real_id = _supporting_chunk_id(name, "Skills API")
    fake_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # ULID-shaped, not in retrieval
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="supported",
                confidence=0.9,
                reasoning=f"Chunk {real_id} affirms it; see also {fake_id}.",
                supporting_chunk_ids=[real_id],
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output=result)

    assert result["label"] == "supported"
    assert real_id in result["reasoning"]  # the genuine citation survives
    assert fake_id not in result["reasoning"]  # the hallucinated one is gone


def test_verify_records_token_usage_and_cache_metrics(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="not_found",
                confidence=0.6,
                input_tokens=120,
                output_tokens=40,
                cache_read_input_tokens=900,
                cache_creation_input_tokens=0,
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output={})
    with open_connection(workspace_db_path(name)) as conn:
        row = conn.execute(
            "SELECT total_input_tokens, total_output_tokens, total_cache_read, "
            "total_cache_creation, model FROM run WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    assert row["total_input_tokens"] == 120
    assert row["total_output_tokens"] == 40
    assert row["total_cache_read"] == 900
    assert row["total_cache_creation"] == 0
    assert row["model"] == "claude-sonnet-4-6"


def test_verify_model_override(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api", model="claude-opus-4-7")
        run.close(output=result)

    assert result["model"] == "claude-opus-4-7"
    assert fake.calls[0]["model"] == "claude-opus-4-7"


def test_cli_verify_supported_smoke(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    chunk_id = _supporting_chunk_id(name, "Skills API")
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="supported",
                confidence=0.9,
                supporting_chunk_ids=[chunk_id],
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "Anthropic shipped the Skills API in October 2025", "--workspace", name],
    )
    assert result.exit_code == 0, result.output
    assert "SUPPORTED" in result.output
    assert "0.90" in result.output


def test_cli_verify_json_output(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.6)])
    _install_fake_client(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "An unrelated claim", "--workspace", name, "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["label"] == "not_found"
    assert payload["model"] == "claude-sonnet-4-6"


def test_verify_domain_routes_to_binary_mode(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """domain='pubmedQA' must route to binary mode — same tool schema the
    benchmark uses for source-aware routing."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        name="record_binary_verdict",
                        input={"faithful": True, "confidence": 0.9, "reasoning": "ok"},
                    )
                ]
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api", domain="pubmedQA")
        run.close(output=result)

    assert result["label"] == "supported"
    assert fake.calls[0]["tool_choice"] == {"type": "tool", "name": "record_binary_verdict"}


def test_verify_explicit_mode_wins_over_domain(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If both mode and domain are passed, explicit mode wins. Otherwise
    upgrade-path tooling can't override the router in edge cases."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        skill.run(
            workspace=ws, run=run, claim="skills api", mode="evidence", domain="DROP"
        )
        run.close(output={})

    # DROP routes to binary in DOMAIN_TO_MODE; explicit mode="evidence"
    # must override that, so the record_verdict (evidence) tool was used.
    assert fake.calls[0]["tool_choice"] == {"type": "tool", "name": "record_verdict"}


def test_verify_unknown_domain_raises(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orc.directives.research.routing import UnknownDomainError

    name = _setup_corpus(orc_home, tmp_path)
    _install_fake_client(monkeypatch, FakeAnthropic())
    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with (
        open_run(ws, directive="research", skill="verify_claim", inputs={}) as run,
        pytest.raises(UnknownDomainError),
    ):
        skill.run(workspace=ws, run=run, claim="x", domain="NotARealDomain")


def test_cli_verify_unknown_domain_is_clean_click_error(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User typo on --domain shouldn't blow up as an unhandled exception —
    it should land as a Click error with non-zero exit and the unknown
    value mentioned in the output."""
    name = _setup_corpus(orc_home, tmp_path)
    _install_fake_client(monkeypatch, FakeAnthropic())  # never called

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "x", "--workspace", name, "--domain", "NotARealDomain"],
    )
    assert result.exit_code != 0
    # The error should be Click-formatted (no raw traceback).
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "NotARealDomain" in combined


def test_mcp_verify_unknown_domain_returns_error_payload(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP surface returns a clean error dict for unknown domain, doesn't
    let the exception bubble up as a protocol-level fault."""
    name = _setup_corpus(orc_home, tmp_path)
    _install_fake_client(monkeypatch, FakeAnthropic())

    from orc.mcp.server import _verify_claim_core

    result = _verify_claim_core("x", workspace=name, domain="NotARealDomain")
    assert "error" in result
    assert "NotARealDomain" in result["error"]


def test_cli_verify_domain_flag_routes_to_binary(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--domain pubmedQA on the CLI must invoke the binary tool schema."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        name="record_binary_verdict",
                        input={"faithful": True, "confidence": 0.95, "reasoning": "ok"},
                    )
                ]
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify", "skills api claim", "--workspace", name, "--domain", "pubmedQA"],
    )
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["tool_choice"] == {"type": "tool", "name": "record_binary_verdict"}


def test_verify_arithmetic_mode_chains_calculator_then_verdict(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arithmetic mode: model calls calculate, runtime executes safely, model
    sees the result, then commits a verdict. Each calculator call is in the
    trace so an auditor can spot-check the math."""
    name = _setup_corpus(orc_home, tmp_path)

    def _tu(name_: str, tid: str, input_data: dict[str, object]) -> FakeContentBlock:
        b = FakeContentBlock(type="tool_use", name=name_, input=input_data)
        b.id = tid  # type: ignore[attr-defined]
        return b

    fake = FakeAnthropic(
        responses=[
            # Turn 0: model decides to compute
            FakeResponse(
                content=[_tu("calculate", "c1", {"expression": "1234.5 / 8000 * 100"})],
                stop_reason="tool_use",
            ),
            # Turn 1: model emits verdict
            FakeResponse(
                content=[
                    _tu(
                        "record_binary_verdict",
                        "v1",
                        {
                            "faithful": True,
                            "confidence": 0.88,
                            "reasoning": "Computed 15.43, matches claim within tolerance.",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api", mode="arithmetic")
        run.close(output=result)

    assert result["label"] == "supported"
    assert result["confidence"] == pytest.approx(0.88)
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["tool"] == "calculate"
    assert tc["input"]["expression"] == "1234.5 / 8000 * 100"
    assert tc["result"].startswith("15.4")  # 1234.5 / 8000 * 100 ≈ 15.43125

    # Trace JSON records both LLM turns + the tool_call.
    traces = list(workspace_traces_dir(name).rglob(f"{run.run_id}.json"))
    trace = json.loads(traces[0].read_text())
    assert len(trace["llm_calls"]) == 2  # two turns in the loop


def test_verify_arithmetic_mode_unfaithful_maps_to_not_found(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same conservative mapping as binary mode: faithful=false → not_found,
    never 'contradicted' (we can't tell silent from contradiction in this mode)."""
    name = _setup_corpus(orc_home, tmp_path)

    def _tu(name_: str, tid: str, input_data: dict[str, object]) -> FakeContentBlock:
        b = FakeContentBlock(type="tool_use", name=name_, input=input_data)
        b.id = tid  # type: ignore[attr-defined]
        return b

    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    _tu(
                        "record_binary_verdict",
                        "v1",
                        {"faithful": False, "confidence": 0.7, "reasoning": "no support"},
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api", mode="arithmetic")
        run.close(output=result)

    assert result["label"] == "not_found"
    assert result["tool_calls"] == []


def test_verify_request_includes_cache_control(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2nd system block must carry cache_control: ephemeral so prompt caching works."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        skill.run(workspace=ws, run=run, claim="skills api")
        run.close(output={})

    sent = fake.calls[0]
    assert sent["tool_choice"] == {"type": "tool", "name": "record_verdict"}
    assert sent["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "<chunk id=" in sent["system"][1]["text"]


def test_verify_binary_mode_maps_unfaithful_to_not_found(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binary's `faithful=false` covers both contradiction *and* corpus silence.
    Mapping it to `contradicted` would be wrong (and dangerous for audit use)
    — we map to `not_found` instead. Use evidence mode if you need the two
    distinguished."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    FakeContentBlock(
                        type="tool_use",
                        name="record_binary_verdict",
                        input={"faithful": False, "confidence": 0.8, "reasoning": "no support"},
                    )
                ]
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="skills api", mode="binary")
        run.close(output=result)

    assert result["label"] == "not_found"


def test_verify_decomposed_mode_aggregates_atoms(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decomposed mode: Haiku decomposes → N binary verdicts → weighted majority."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["Skills shipped in October", "Skills are versioned"]),
            _make_binary_response(faithful=True, confidence=0.9),
            _make_binary_response(faithful=True, confidence=0.8),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(
            workspace=ws,
            run=run,
            claim="Anthropic shipped versioned Skills in October 2025",
            mode="decomposed",
        )
        run.close(output=result)

    assert result["label"] == "supported"
    assert result["confidence"] == pytest.approx(0.85, abs=0.01)  # mean(0.9, 0.8)
    assert len(result["atoms"]) == 2
    assert all(a["label"] == "supported" for a in result["atoms"])
    # 1 decomposition call + 2 binary atom calls
    assert len(fake.calls) == 3
    assert fake.calls[0]["tool_choice"] == {"type": "tool", "name": "record_decomposition"}
    assert fake.calls[1]["tool_choice"] == {"type": "tool", "name": "record_binary_verdict"}


def test_verify_decomposed_mode_confidence_weighted_majority(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One high-confidence supported + one low-confidence contradicted → supported."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["A", "B"]),
            _make_binary_response(faithful=True, confidence=0.9),   # +0.9
            _make_binary_response(faithful=False, confidence=0.3),  # -0.3
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="x", mode="decomposed")
        run.close(output=result)

    # Net score = +0.9 − 0.3 = +0.6 → supported
    assert result["label"] == "supported"


def test_verify_decomposed_zero_confidence_atom_does_not_count_as_half(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A supported atom with confidence 0.0 must contribute 0.0 to the vote, not
    be coerced to 0.5 by a falsy check. With a single such atom the net score is
    0 → `partial`; the bug would push it to `supported`."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["A"]),
            _make_binary_response(faithful=True, confidence=0.0),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="x", mode="decomposed")
        run.close(output=result)

    assert result["label"] == "partial"
