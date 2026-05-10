"""Approval queue tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from orc.cli import main
from orc.queue import approval as approval_module
from orc.queue.approval import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
)
from orc.storage import workspace as ws_module


def _seed_workspace(orc_home: Path) -> str:
    ws = ws_module.create("demo")
    return ws.name


def test_enqueue_and_get_roundtrip(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name,
        directive="research",
        skill="verify_claim",
        source_run_id="01HXYZ123",
        summary="Pause campaign 17 — CPA up 80% wow",
        payload={"campaign_id": "17", "evidence": ["chunk_x", "chunk_y"]},
        proposed_action={"op": "pause_campaign", "campaign_id": "17"},
    )
    a = approval_module.get(name, aid)
    assert a.status == "pending"
    assert a.summary.startswith("Pause campaign 17")
    assert a.payload["campaign_id"] == "17"
    assert a.proposed_action == {"op": "pause_campaign", "campaign_id": "17"}
    assert a.decided_at is None


def test_get_unknown_raises(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    with pytest.raises(ApprovalNotFoundError):
        approval_module.get(name, "not-a-real-id")


def test_list_pending_default(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    a1 = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="one", payload={"x": 1},
    )
    a2 = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r2",
        summary="two", payload={"x": 2},
    )
    items = approval_module.list_approvals(name)
    ids = {x.approval_id for x in items}
    assert a1 in ids and a2 in ids


def test_accept_decides_and_locks(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={},
    )
    a = approval_module.accept(name, aid, decided_by="thor", note="looks right")
    assert a.status == "approved"
    assert a.decided_at is not None
    assert a.decided_by == "thor"
    assert a.decision_note == "looks right"

    with pytest.raises(ApprovalAlreadyDecidedError):
        approval_module.accept(name, aid)


def test_reject_decides(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={},
    )
    a = approval_module.reject(name, aid, decided_by="thor")
    assert a.status == "rejected"
    assert a.decided_by == "thor"


def test_filter_by_status(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    a1 = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="will be approved", payload={},
    )
    a2 = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r2",
        summary="stays pending", payload={},
    )
    approval_module.accept(name, a1)

    pending = approval_module.list_approvals(name, status="pending")
    approved = approval_module.list_approvals(name, status="approved")
    assert {x.approval_id for x in pending} == {a2}
    assert {x.approval_id for x in approved} == {a1}


def test_invalid_status_filter_raises(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    with pytest.raises(ValueError):
        approval_module.list_approvals(name, status="bogus")


def test_table_created_lazily_on_existing_workspace(orc_home: Path) -> None:
    """Existing workspaces (created before the approval table existed) must still work.

    Simulates the upgrade case: drop the table, then call enqueue and verify it still works.
    """
    name = _seed_workspace(orc_home)
    from orc.paths import workspace_db_path
    from orc.storage.db import open_connection

    with open_connection(workspace_db_path(name)) as conn:
        conn.execute("DROP TABLE approval")

    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={},
    )
    a = approval_module.get(name, aid)
    assert a.status == "pending"


def test_cli_list_empty(orc_home: Path) -> None:
    _seed_workspace(orc_home)
    runner = CliRunner()
    result = runner.invoke(main, ["approve", "list", "-w", "demo"])
    assert result.exit_code == 0
    assert "No approvals" in result.output


def test_cli_accept_flow(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="needs review", payload={"k": "v"},
    )
    runner = CliRunner()
    res_show = runner.invoke(main, ["approve", "show", aid, "-w", name])
    assert res_show.exit_code == 0
    assert "needs review" in res_show.output

    res_list = runner.invoke(main, ["approve", "list", "-w", name])
    assert res_list.exit_code == 0
    assert aid[:6] in res_list.output

    res_accept = runner.invoke(
        main, ["approve", "accept", aid, "-w", name, "--note", "ship it", "--by", "thor"]
    )
    assert res_accept.exit_code == 0, res_accept.output
    assert "approved" in res_accept.output

    a = approval_module.get(name, aid)
    assert a.status == "approved"
    assert a.decided_by == "thor"
    assert a.decision_note == "ship it"


def test_cli_reject_flow(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="needs review", payload={},
    )
    runner = CliRunner()
    res = runner.invoke(main, ["approve", "reject", aid, "-w", name, "--note", "wrong"])
    assert res.exit_code == 0
    assert "rejected" in res.output
    a = approval_module.get(name, aid)
    assert a.status == "rejected"
