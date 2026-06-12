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
