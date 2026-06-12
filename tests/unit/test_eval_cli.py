"""`orc eval` gold-set CLI: import from YAML, promote a real verdict, list."""

from __future__ import annotations

from click.testing import CliRunner

from orc import directives
from orc.cli import main
from orc.eval import gold
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.runs import open_run
from orc.storage import workspace as ws_module
from tests._fake_llm import FakeAnthropic, make_verdict_response


def test_eval_import_seeds_gold_from_yaml(orc_home, tmp_path) -> None:
    ws_module.create("demo")
    f = tmp_path / "claims.yaml"
    f.write_text(
        "- id: c1\n  text: The sky is blue\n  expected: supported\n"
        "- id: c2\n  text: Pigs fly\n  expected: not_found\n"
    )
    res = CliRunner().invoke(main, ["eval", "import", str(f), "-w", "demo"])
    assert res.exit_code == 0, res.output
    labels = {g.expected_label for g in gold.list_gold("demo")}
    assert labels == {"supported", "not_found"}


def test_eval_label_promotes_a_real_verdict(orc_home, tmp_path, monkeypatch) -> None:
    # Build one real verify run, then promote its verdict into the gold set.
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.md").write_text("# Doc\n\nThe sky is blue on a clear day.\n")
    do_ingest(ws, str(corpus))

    fake = FakeAnthropic(responses=[make_verdict_response(label="supported", confidence=0.9)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        result = skill.run(workspace=ws, run=run, claim="The sky is blue")
        run.close(output=result)
    run_id = run.run_id

    res = CliRunner().invoke(main, ["eval", "label", run_id, "--verdict", "contradicted", "-w", "demo"])
    assert res.exit_code == 0, res.output

    [g] = gold.list_gold("demo")
    assert g.expected_label == "contradicted"  # human corrected the model
    assert g.claim == "The sky is blue"
    assert g.source == "promoted"
    assert g.source_run_id == run_id
    assert g.corpus_version == ws.corpus_version


def test_eval_label_unknown_run_fails_cleanly(orc_home) -> None:
    ws_module.create("demo")
    res = CliRunner().invoke(main, ["eval", "label", "01NOSUCHRUN", "--verdict", "supported", "-w", "demo"])
    assert res.exit_code != 0
    assert "01NOSUCHRUN" in res.output


def test_eval_gold_list_json(orc_home) -> None:
    import json

    ws_module.create("demo")
    gold.add("demo", claim="x", expected_label="supported", corpus_version=0, source="import")
    res = CliRunner().invoke(main, ["eval", "gold", "list", "-w", "demo", "--json"])
    assert res.exit_code == 0, res.output
    [item] = json.loads(res.output)
    assert item["expected_label"] == "supported"
    assert item["stale_chunk_labels"] is False


def test_eval_run_and_show_roundtrip(orc_home, tmp_path, monkeypatch) -> None:
    import json as json_lib

    from orc.paths import workspace_db_path
    from orc.storage.db import open_connection

    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.md").write_text("# Doc\n\nThe sky is blue on a clear day.\n")
    do_ingest(ws, str(corpus))
    with open_connection(workspace_db_path("demo")) as conn:
        chunk_id = conn.execute("SELECT chunk_id FROM chunk ORDER BY seq LIMIT 1").fetchone()["chunk_id"]
    cv = ws_module.resolve("demo").corpus_version
    gold.add("demo", claim="The sky is blue", expected_label="supported", corpus_version=cv, source="import")

    fake = FakeAnthropic(responses=[
        make_verdict_response(label="supported", confidence=0.9, supporting_chunk_ids=[chunk_id]),
    ])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    res = CliRunner().invoke(main, ["eval", "run", "-w", "demo", "--json"])
    assert res.exit_code == 0, res.output
    payload = json_lib.loads(res.output)
    assert payload["n"] == 1
    assert payload["accuracy"] == 1.0

    res2 = CliRunner().invoke(main, ["eval", "show", payload["eval_id"], "-w", "demo"])
    assert res2.exit_code == 0, res2.output
    assert payload["eval_id"] in res2.output


def test_eval_run_with_no_gold_fails_cleanly(orc_home) -> None:
    ws_module.create("demo")
    res = CliRunner().invoke(main, ["eval", "run", "-w", "demo"])
    assert res.exit_code != 0
    assert "gold" in res.output.lower()


def test_eval_calibrate_writes_policy_and_tiered_reads_it(orc_home, tmp_path, monkeypatch) -> None:
    from orc.eval.policy import load_policy

    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.md").write_text("# Doc\n\nThe sky is blue on a clear day.\n")
    do_ingest(ws, str(corpus))
    cv = ws_module.resolve("demo").corpus_version
    gold.add("demo", claim="The sky is blue", expected_label="supported", corpus_version=cv, source="import")
    gold.add("demo", claim="The grass is blue", expected_label="not_found", corpus_version=cv, source="import")

    from tests._fake_llm import FakeContentBlock, FakeResponse

    def _binary(faithful, confidence):
        return FakeResponse(content=[FakeContentBlock(
            type="tool_use", name="record_binary_verdict",
            input={"faithful": faithful, "confidence": confidence, "reasoning": "r"})])

    # Tier-1 binary: claim 1 supported@0.97 (correct), claim 2 unfaithful@0.96 (correct).
    fake = FakeAnthropic(responses=[_binary(True, 0.97), _binary(False, 0.96)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    res = CliRunner().invoke(main, ["eval", "calibrate", "-w", "demo", "--target", "0.95"])
    assert res.exit_code == 0, res.output
    assert "Calibrated" in res.output

    policy = load_policy("demo")
    assert policy is not None
    assert policy.target == 0.95
    assert 0.0 < policy.escalation_threshold <= 0.97
