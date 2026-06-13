"""Mode-specific verify_claim regression tests.

Covers three guards that the per-mode code paths must share with evidence mode:
decomposed aggregation must be able to vote *against* a claim, judgment mode
must enforce the citation guard, and every free-text field that can carry a
chunk ID must pass through ULID redaction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orc import directives
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.paths import workspace_db_path, workspace_traces_dir
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection
from tests._fake_llm import FakeAnthropic, FakeContentBlock, FakeResponse, make_verdict_response

# ULID-shaped, deliberately absent from any retrieval set.
FABRICATED_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


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
    do_ingest(ws, str(corpus))
    return ws.name


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, fake: FakeAnthropic) -> None:
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)


def _real_chunk_id(workspace_name: str) -> str:
    with open_connection(workspace_db_path(workspace_name)) as conn:
        row = conn.execute("SELECT chunk_id FROM chunk ORDER BY seq LIMIT 1").fetchone()
    assert row is not None
    return row["chunk_id"]


def _run_skill(workspace_name: str, **kwargs: object) -> dict:
    ws = ws_module.resolve(workspace_name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, **kwargs)
        run.close(output=result)
    result["_run_id"] = run.run_id
    return result


def test_verify_decomposed_all_unfaithful_atoms_yields_not_found(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every atom judged unfaithful at high confidence must produce a negative
    net vote → not_found. The bug: binary atoms only ever return supported /
    not_found, and not_found contributed 0, so the aggregate could never go
    negative and an entirely-unfaithful claim shipped as `partial` at 0.95."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["A", "B"]),
            _make_binary_response(faithful=False, confidence=0.95),
            _make_binary_response(faithful=False, confidence=0.95),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="x", mode="decomposed")

    assert result["label"] == "not_found"


def test_verify_decomposed_trace_records_retrieval_across_all_atoms(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each atom's binary sub-run overwrote run.retrieval, so the trace audited
    only the last atom's retrieval. The aggregate must account for every atom:
    candidates_considered sums across atoms, returned is the deduped union."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["A", "B"]),
            _make_binary_response(faithful=True, confidence=0.9),
            _make_binary_response(faithful=True, confidence=0.9),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="x", mode="decomposed")

    traces = list(workspace_traces_dir(name).rglob(f"{result['_run_id']}.json"))
    assert len(traces) == 1
    retrieval = json.loads(traces[0].read_text())["retrieval"]
    # 2 atoms x 1 candidate each: both retrievals must be accounted for.
    assert retrieval["candidates_considered"] == 2
    assert {r["chunk_id"] for r in retrieval["returned"]} == {_real_chunk_id(name)}
    assert retrieval["method"] == "binary_all"


def test_verify_decomposed_negative_majority_yields_not_found_not_contradicted(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A low-confidence supported atom outweighed by a high-confidence
    unfaithful atom → net negative → not_found. Never `contradicted`: binary
    atoms cannot distinguish contradiction from corpus silence, so the
    aggregate must stay as conservative as the binary mapping itself."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["A", "B"]),
            _make_binary_response(faithful=True, confidence=0.3),   # +0.3
            _make_binary_response(faithful=False, confidence=0.9),  # -0.9
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="x", mode="decomposed")

    assert result["label"] == "not_found"


def test_verify_judgment_mode_downgrades_hallucinated_citations_to_not_found(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Judgment mode produces the same 4-label verdict with chunk citations as
    evidence mode (reachable publicly via domain='halueval'), so it needs the
    same citation guard: a `supported` verdict whose every cited chunk was
    hallucinated has no grounding and must downgrade to not_found — and the
    trace must record the downgrade, same as evidence mode."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="supported",
                confidence=0.8,
                supporting_chunk_ids=[FABRICATED_ULID],
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="skills api", mode="judgment")

    assert result["supporting_chunks"] == []
    assert result["label"] == "not_found"

    traces = list(workspace_traces_dir(name).rglob(f"{result['_run_id']}.json"))
    trace = json.loads(traces[0].read_text())
    response_meta = trace["llm_calls"][0]["response"]
    assert response_meta["dropped_chunk_ids"] == [FABRICATED_ULID]
    assert response_meta["label_downgraded"] is True


def test_verify_redacts_hallucinated_ids_in_missing_information(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`missing_information` is free text just like `reasoning`, so a chunk ID
    the model fabricates there must not reach the caller verbatim — only IDs
    present in the retrieval set survive redaction."""
    name = _setup_corpus(orc_home, tmp_path)
    real_id = _real_chunk_id(name)
    fake = FakeAnthropic(
        responses=[
            make_verdict_response(
                label="supported",
                confidence=0.9,
                reasoning="ok",
                supporting_chunk_ids=[real_id],
                missing_information=f"Compare {real_id} with {FABRICATED_ULID}.",
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="skills api")

    assert real_id in result["missing_information"]  # genuine citation survives
    assert FABRICATED_ULID not in result["missing_information"]


def test_verify_arithmetic_redacts_hallucinated_ids_in_reasoning(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arithmetic mode's verdict comes from record_binary_verdict, bypassing
    the evidence-path redaction — a fabricated chunk ID in its reasoning must
    still be redacted against the retrieval candidate set."""
    name = _setup_corpus(orc_home, tmp_path)
    real_id = _real_chunk_id(name)

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
                        {
                            "faithful": True,
                            "confidence": 0.9,
                            "reasoning": f"Chunk {real_id} checks out; see also {FABRICATED_ULID}.",
                        },
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="skills api", mode="arithmetic")

    assert result["label"] == "supported"
    assert real_id in result["reasoning"]  # genuine citation survives
    assert FABRICATED_ULID not in result["reasoning"]


def test_verify_decomposed_redacts_hallucinated_ids_in_atoms_and_reasoning(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decomposer's atom strings are LLM free text and they flow verbatim
    into the caller-facing `reasoning` and `atoms` fields — a fabricated chunk
    ID there must be redacted just like every other free-text field. IDs that
    exist in the atoms' retrieval set survive."""
    name = _setup_corpus(orc_home, tmp_path)
    real_id = _real_chunk_id(name)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(
                [f"The doc cites {real_id}", f"See {FABRICATED_ULID} for details"]
            ),
            _make_binary_response(faithful=True, confidence=0.9),
            _make_binary_response(faithful=True, confidence=0.9),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="x", mode="decomposed")

    assert real_id in result["reasoning"]  # genuine citation survives
    assert FABRICATED_ULID not in result["reasoning"]
    assert all(FABRICATED_ULID not in a["atom"] for a in result["atoms"])


def test_verify_decomposed_tied_vote_yields_partial(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equal-confidence supported and unfaithful atoms cancel to a net of
    exactly zero → partial (genuinely mixed evidence, no majority)."""
    name = _setup_corpus(orc_home, tmp_path)
    fake = FakeAnthropic(
        responses=[
            _make_decomposition_response(["A", "B"]),
            _make_binary_response(faithful=True, confidence=0.5),
            _make_binary_response(faithful=False, confidence=0.5),
        ]
    )
    _install_fake_client(monkeypatch, fake)

    result = _run_skill(name, claim="x", mode="decomposed")

    assert result["label"] == "partial"
