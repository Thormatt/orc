"""`orc verify` CLI: the --mode flag routes to the verify mode."""

from __future__ import annotations

from click.testing import CliRunner

from orc.cli import main
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.storage import workspace as ws_module
from tests._fake_llm import FakeAnthropic, FakeContentBlock, FakeResponse


def _binary(*, faithful: bool, confidence: float) -> FakeResponse:
    return FakeResponse(content=[FakeContentBlock(
        type="tool_use", name="record_binary_verdict",
        input={"faithful": faithful, "confidence": confidence, "reasoning": "r"})])


def test_verify_mode_flag_routes_to_binary(orc_home, tmp_path, monkeypatch) -> None:
    ws = ws_module.create("demo")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.md").write_text("# Doc\n\nThe sky is blue on a clear day.\n")
    do_ingest(ws, str(corpus))
    fake = FakeAnthropic(responses=[_binary(faithful=True, confidence=0.9)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)

    res = CliRunner().invoke(
        main, ["verify", "The sky is blue", "-w", "demo", "--mode", "binary", "--json"]
    )
    assert res.exit_code == 0, res.output
    assert "No such option" not in res.output
    import json
    assert json.loads(res.output)["label"] in {"supported", "not_found"}
