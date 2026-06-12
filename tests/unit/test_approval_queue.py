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
    DuplicateApproverError,
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
        approval_module.accept(name, aid, decided_by="other")


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
    approval_module.accept(name, a1, decided_by="alice")

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


# ───────── multi-approver workflow (Article 14 §5) ─────────


def test_decided_by_required(orc_home: Path) -> None:
    """The regulation requires named natural persons; the module enforces that."""
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={},
    )
    with pytest.raises(ValueError):
        approval_module.accept(name, aid, decided_by=None)
    with pytest.raises(ValueError):
        approval_module.accept(name, aid, decided_by="")


def test_two_approver_flow_first_accept_stays_pending(orc_home: Path) -> None:
    """First accept on a 2-approver approval leaves status pending."""
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name,
        directive="research",
        skill="verify_claim",
        source_run_id="r1",
        summary="biometric match — Annex III §1(a)",
        payload={"subject_id": "abc"},
        approvers_required=2,
    )
    a1 = approval_module.accept(name, aid, decided_by="alice", note="visual match confirmed")
    assert a1.status == "pending"
    assert a1.accept_count == 1
    assert a1.approvers_required == 2
    assert a1.progress == "1/2"
    assert a1.decided_at is None
    assert len(a1.decisions) == 1


def test_two_approver_flow_second_accept_flips_status(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={}, approvers_required=2,
    )
    approval_module.accept(name, aid, decided_by="alice")
    a2 = approval_module.accept(name, aid, decided_by="bob", note="second verification")

    assert a2.status == "approved"
    assert a2.accept_count == 2
    assert a2.progress == "2/2"
    assert a2.decided_by == "bob"  # the deciding approver
    assert a2.decision_note == "second verification"
    assert a2.decided_at is not None
    assert {d.decided_by for d in a2.decisions} == {"alice", "bob"}


def test_any_single_rejection_blocks(orc_home: Path) -> None:
    """Even on a 2-approver flow, one reject from anyone immediately blocks."""
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={}, approvers_required=2,
    )
    approval_module.accept(name, aid, decided_by="alice")
    a = approval_module.reject(name, aid, decided_by="bob", note="not satisfied")
    assert a.status == "rejected"
    assert a.reject_count == 1
    assert a.accept_count == 1
    assert a.decided_by == "bob"


def test_duplicate_approver_blocked(orc_home: Path) -> None:
    """Article 14 §5 requires distinct natural persons; same person can't vote twice."""
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={}, approvers_required=2,
    )
    approval_module.accept(name, aid, decided_by="alice")
    with pytest.raises(DuplicateApproverError):
        approval_module.accept(name, aid, decided_by="alice", note="second try")
    # Status remains pending
    a = approval_module.get(name, aid)
    assert a.status == "pending"
    assert a.accept_count == 1


def test_invalid_approvers_required(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    with pytest.raises(ValueError):
        approval_module.enqueue(
            name, directive="research", skill="verify_claim", source_run_id="r1",
            summary="x", payload={}, approvers_required=0,
        )


def test_decisions_have_full_audit_trail(orc_home: Path) -> None:
    """Every decision is recorded with name + timestamp + note for Article 12 logging."""
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={}, approvers_required=2,
    )
    approval_module.accept(name, aid, decided_by="alice", note="first")
    approval_module.accept(name, aid, decided_by="bob", note="second")

    a = approval_module.get(name, aid)
    decisions_by = {d.decided_by: d for d in a.decisions}
    assert decisions_by["alice"].decision == "accept"
    assert decisions_by["alice"].note == "first"
    assert decisions_by["alice"].decided_at is not None
    assert decisions_by["bob"].decision == "accept"
    assert decisions_by["bob"].note == "second"


def test_cli_two_approver_progress_shown(orc_home: Path) -> None:
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="biometric match", payload={}, approvers_required=2,
    )
    runner = CliRunner()
    # First accept: pending still
    res = runner.invoke(
        main, ["approve", "accept", aid, "-w", name, "--by", "alice"]
    )
    assert res.exit_code == 0
    assert "pending" in res.output
    assert "1/2" in res.output

    # Second accept by different person: approved
    res = runner.invoke(
        main, ["approve", "accept", aid, "-w", name, "--by", "bob"]
    )
    assert res.exit_code == 0
    assert "approved" in res.output
    assert "2/2" in res.output


def test_cli_show_includes_decisions(orc_home: Path) -> None:
    import json

    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={}, approvers_required=2,
    )
    approval_module.accept(name, aid, decided_by="alice", note="first")
    runner = CliRunner()
    res = runner.invoke(main, ["approve", "show", aid, "-w", name])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["approvers_required"] == 2
    assert payload["approvers_progress"] == "1/2"
    assert payload["accept_count"] == 1
    assert payload["reject_count"] == 0
    assert len(payload["decisions"]) == 1
    assert payload["decisions"][0]["decided_by"] == "alice"


def test_backward_compat_default_single_approver(orc_home: Path) -> None:
    """An approval enqueued without approvers_required behaves like before."""
    name = _seed_workspace(orc_home)
    aid = approval_module.enqueue(
        name, directive="research", skill="verify_claim", source_run_id="r1",
        summary="x", payload={},
    )
    a = approval_module.get(name, aid)
    assert a.approvers_required == 1
    decided = approval_module.accept(name, aid, decided_by="alice")
    assert decided.status == "approved"
    assert decided.progress == "1/1"


def test_approve_list_json_emits_machine_readable_array(orc_home: Path) -> None:
    """Scripts (and baton) need a parseable pending check, not a rich table."""
    import json as json_lib

    name = _seed_workspace(orc_home)
    approval_module.enqueue(
        name,
        directive="research",
        skill="t",
        source_run_id="01HXYZ123",
        summary="machine readable",
        payload={},
    )

    result = CliRunner().invoke(main, ["approve", "list", "-w", name, "--json"])
    assert result.exit_code == 0, result.output
    items = json_lib.loads(result.output)
    assert isinstance(items, list) and len(items) == 1
    item = items[0]
    assert item["status"] == "pending"
    assert item["summary"] == "machine readable"
    assert {"approval_id", "approvers_required", "accept_count", "reject_count",
            "directive", "skill", "source_run_id", "created_at"} <= set(item)
