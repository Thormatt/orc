"""build_report_html unit tests — pure function over plain trace dicts.

Traces are built as plain dicts matching trace schema v2 (see
orc.runs.trace_schema) so the renderer is exercised without a workspace,
database, or LLM.
"""

from __future__ import annotations

from typing import Any

import pytest

from orc.rendering.trace_html import build_report_html


def make_trace(
    *,
    run_id: str = "01HXAMPLE01",
    claim: str = "Skills API shipped in October 2025.",
    label: str = "supported",
    confidence: float = 0.92,
    **overrides: Any,
) -> dict[str, Any]:
    """Plain dict matching the trace JSON contract written by Run.close()."""
    trace: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "directive": "research",
        "skill": "verify_claim",
        "workspace": "demo",
        "corpus_version": 3,
        "started_at": "2026-06-01T08:00:00Z",
        "ended_at": "2026-06-01T08:00:31Z",
        "status": "ok",
        "model": "claude-sonnet-4-6",
        "inputs": {"claim": claim},
        "effective_kwargs": {"k": 6},
        "events": [],
        "retrieval": {"method": "bm25", "candidates_considered": 12, "returned": []},
        "llm_calls": [
            {
                "model": "claude-sonnet-4-6",
                "input_tokens": 900,
                "output_tokens": 120,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "elapsed_ms": 800,
            }
        ],
        "output": {
            "claim": claim,
            "label": label,
            "confidence": confidence,
            "reasoning": "Matches the release-notes chunk verbatim.",
            "supporting_chunks": [
                {
                    "chunk_id": "chunk_AAA",
                    "evidence_id": "ev_1",
                    "evidence_title": "Release notes",
                    "evidence_source_path": "notes/release.md",
                    "headings_path": "Release notes",
                    "text": "The Skills API shipped in October 2025.",
                }
            ],
            "contradicting_chunks": [],
            "missing_information": None,
        },
        "error_message": None,
    }
    trace.update(overrides)
    return trace


@pytest.mark.parametrize(
    ("label", "verdict"),
    [
        ("supported", "ok"),
        ("partial", "warn"),
        ("contradicted", "bad"),
        ("not_found", "nf"),
    ],
)
def test_verdict_mapping_covers_all_labels(label: str, verdict: str) -> None:
    html_doc = build_report_html([make_trace(label=label)])
    assert f'data-verdict="{verdict}"' in html_doc


def test_script_tag_in_claim_text_is_escaped() -> None:
    html_doc = build_report_html([make_trace(claim="<script>alert('xss')</script>")])
    assert "<script>alert" not in html_doc
    assert "&lt;script&gt;alert" in html_doc


def test_multi_trace_report_renders_each_claim_and_counters() -> None:
    traces = [
        make_trace(run_id="01RUNAAA", label="supported"),
        make_trace(run_id="01RUNBBB", label="contradicted"),
        make_trace(run_id="01RUNCCC", label="partial"),
    ]
    html_doc = build_report_html(traces)
    assert html_doc.count('<article class="claim"') == 3
    for counter_id in ("cnt-ok", "cnt-warn", "cnt-bad"):
        assert f'id="{counter_id}"' in html_doc


def test_missing_optional_fields_tolerated() -> None:
    # A failed or non-verify run can have a sparse trace: no model, no
    # retrieval, an empty output. The report must still render.
    bare = {
        "schema_version": 2,
        "run_id": "01BARERUN",
        "directive": "research",
        "skill": "verify_claim",
        "workspace": "demo",
        "corpus_version": 1,
        "started_at": "2026-06-01T08:00:00Z",
        "status": "error",
        "inputs": {},
        "output": {},
    }
    html_doc = build_report_html([bare])
    assert "01BARERUN" in html_doc
    assert html_doc.count('<article class="claim"') == 1


def test_reasoning_chunks_and_missing_information_rendered() -> None:
    trace = make_trace(label="partial", confidence=0.74)
    trace["output"]["contradicting_chunks"] = [
        {
            "chunk_id": "chunk_BBB",
            "evidence_id": "ev_2",
            "evidence_title": "Q3 call",
            "evidence_source_path": "calls/q3.md",
            "headings_path": "Q3",
            "text": "Backlog declined 8% in Q3.",
        }
    ]
    trace["output"]["missing_information"] = "No backlog data after Q3."

    html_doc = build_report_html([trace])

    assert "Matches the release-notes chunk verbatim." in html_doc
    assert "chunk_AAA" in html_doc
    assert "notes/release.md" in html_doc
    assert "The Skills API shipped in October 2025." in html_doc
    assert '<div class="chunk bad">' in html_doc
    assert "Backlog declined 8% in Q3." in html_doc
    assert "No backlog data after Q3." in html_doc
    # Supporting chunks render before contradicting chunks.
    assert html_doc.index("chunk_AAA") < html_doc.index("chunk_BBB")


def test_footer_totals_tokens_and_shows_replay_lineage() -> None:
    replayed = make_trace(run_id="01REPLAYED")
    replayed["inputs"]["_replay_of"] = "01ORIGINAL"
    other = make_trace(run_id="01OTHERRUN")

    html_doc = build_report_html([replayed, other])

    # 2 llm_calls x (900 in, 120 out) from the fixture.
    assert "1,800" in html_doc
    assert "240" in html_doc
    assert "01ORIGINAL" in html_doc
    assert "replay" in html_doc
    assert "generated by orc report" in html_doc


def test_inline_css_and_js_are_embedded() -> None:
    html_doc = build_report_html([make_trace()])
    # "landing-as-audit" is the header comment of trace.css; resolveVerdicts
    # is the trace.js entry that flips pending pills. Both must arrive inline —
    # the artifact may never depend on external files.
    assert "<style>" in html_doc
    assert "landing-as-audit" in html_doc
    assert "<script>" in html_doc
    assert "resolveVerdicts" in html_doc
    assert 'href="trace.css"' not in html_doc
    assert 'src="trace.js"' not in html_doc


def test_report_handles_unbreakable_tokens_and_many_runs() -> None:
    """Real traces carry long unbreakable tokens (URLs, DOIs, file paths) the
    mockup never had; without word-breaking overrides the centered grid
    overflows and clips off the LEFT viewport edge. And a 13-run report must
    not dump every run id into the topbar."""
    traces = [make_trace(run_id=f"01KTYCD5EZSNV3APT9DZA9M7Y{i}") for i in range(5)]
    html = build_report_html(traces)
    # word-break overrides present after the verbatim asset css
    assert "overflow-wrap" in html
    # topbar summarizes instead of listing all five ids
    head = html[: html.index("<main")]
    assert "5 runs" in head
    assert head.count("01KTYCD5EZSNV3APT9DZA9M7Y") <= 1
