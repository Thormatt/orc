"""The eval runner scores the gate against a workspace's gold set."""

from __future__ import annotations

import pytest
from click.testing import CliRunner  # noqa: F401  (kept for parity; unused here)

from orc.eval import gold
from orc.eval.runner import recall, run_eval
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.storage import workspace as ws_module
from tests._fake_llm import FakeAnthropic, make_verdict_response


def test_recall_at_k_is_intersection_over_relevant() -> None:
    assert recall(retrieved=["a", "b", "c"], relevant=["b", "d"]) == 0.5
    assert recall(retrieved=["a", "b"], relevant=["a", "b"]) == 1.0
    assert recall(retrieved=[], relevant=["a"]) == 0.0
    assert recall(retrieved=["a"], relevant=[]) is None  # nothing to recall


def _setup(orc_home, tmp_path) -> tuple[str, str]:
    from orc.paths import workspace_db_path
    from orc.storage.db import open_connection

    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.md").write_text("# Doc\n\nThe sky is blue on a clear day.\n")
    do_ingest(ws, str(corpus))
    with open_connection(workspace_db_path("demo")) as conn:
        chunk_id = conn.execute("SELECT chunk_id FROM chunk ORDER BY seq LIMIT 1").fetchone()["chunk_id"]
    # Ingest bumps corpus_version; gold must pin the version where chunks exist.
    cv = ws_module.resolve("demo").corpus_version
    return ws.name, chunk_id, cv


def test_run_eval_scores_judge_accuracy_and_persists(orc_home, tmp_path, monkeypatch) -> None:
    name, chunk_id, cv = _setup(orc_home, tmp_path)
    # Multi-word claims so BM25 retrieves the chunk (single-char claims drop out).
    gold.add(name, claim="The sky is blue", expected_label="supported", corpus_version=cv, source="import")
    gold.add(name, claim="The sky is green", expected_label="contradicted", corpus_version=cv, source="import")

    # Model says supported (citing a real chunk so the guard keeps it) to both:
    # claim 1 correct, claim 2 wrong -> accuracy 0.5.
    fake = FakeAnthropic(responses=[
        make_verdict_response(label="supported", confidence=0.9, supporting_chunk_ids=[chunk_id]),
        make_verdict_response(label="supported", confidence=0.6, supporting_chunk_ids=[chunk_id]),
    ])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    report = run_eval(name, mode="evidence")
    assert report.n == 2
    assert report.accuracy == 0.5
    # one correct at 0.9, one wrong at 0.6 -> ECE = (|0.9-1| + |0.6-0|)/2 = 0.35
    assert round(report.calibration_ece, 4) == 0.35

    # The eval is itself persisted and reloadable.
    from orc.eval.runner import load_eval
    again = load_eval(name, report.eval_id)
    assert again.accuracy == report.accuracy
    assert again.n == 2


def test_run_eval_empty_gold_raises(orc_home) -> None:
    ws_module.create("demo")
    with pytest.raises(ValueError, match="no gold"):
        run_eval("demo", mode="evidence")
