import pytest

from orc.eval import gold
from orc.storage import workspace as ws_module


def test_add_and_list_gold_claim(orc_home) -> None:
    ws_module.create("demo")
    gid = gold.add(
        "demo",
        claim="The sky is blue",
        expected_label="supported",
        corpus_version=0,
        source="import",
        note="seed",
    )
    [g] = gold.list_gold("demo")
    assert g.gold_id == gid
    assert g.claim == "The sky is blue"
    assert g.expected_label == "supported"
    assert g.relevant_chunk_ids is None
    assert g.source == "import"


def test_add_preserves_relevant_chunk_ids(orc_home) -> None:
    ws_module.create("demo")
    gold.add(
        "demo",
        claim="x",
        expected_label="supported",
        corpus_version=3,
        source="promoted",
        relevant_chunk_ids=["01ABC", "01DEF"],
        source_run_id="01RUN",
    )
    [g] = gold.list_gold("demo")
    assert g.relevant_chunk_ids == ["01ABC", "01DEF"]
    assert g.corpus_version == 3
    assert g.source_run_id == "01RUN"


def test_add_rejects_unknown_label(orc_home) -> None:
    ws_module.create("demo")
    with pytest.raises(ValueError, match="expected_label"):
        gold.add("demo", claim="x", expected_label="maybe", corpus_version=0, source="import")
