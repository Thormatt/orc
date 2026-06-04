"""Execution lifecycle for approved actions: lease, mark executed/failed, idempotency."""

from __future__ import annotations

from pathlib import Path

from orc.queue import approval as q
from orc.storage import workspace as ws_module


def _approved(workspace: str, *, idem: str = "idem-1") -> str:
    """Enqueue an approval carrying an Action, then approve it. Returns approval_id."""
    approval_id = q.enqueue(
        workspace,
        directive="research",
        skill="verify_claim",
        source_run_id="run-x",
        summary="write a report",
        payload={},
        proposed_action={
            "executor": "fs.write_file",
            "version": 1,
            "params": {"path": "r.txt", "content": "x"},
            "idempotency_key": idem,
        },
    )
    q.accept(workspace, approval_id, decided_by="alice")
    return approval_id


def test_lease_one_returns_approved_and_is_exclusive(orc_home: Path) -> None:
    ws_module.create("research")
    approval_id = _approved("research")

    leased = q.lease_one("research", lease_owner="worker-1", ttl_seconds=300)
    assert leased is not None
    assert leased.approval_id == approval_id

    # Second lease finds nothing — the row is held.
    assert q.lease_one("research", lease_owner="worker-2", ttl_seconds=300) is None


def test_stale_lease_is_reclaimable(orc_home: Path) -> None:
    ws_module.create("research")
    _approved("research")

    first = q.lease_one("research", lease_owner="worker-1", ttl_seconds=-1)  # already expired
    assert first is not None
    second = q.lease_one("research", lease_owner="worker-2", ttl_seconds=300)
    assert second is not None  # reclaimed after the stale lease


def test_pending_unapproved_is_not_leasable(orc_home: Path) -> None:
    ws_module.create("research")
    q.enqueue(
        "research",
        directive="research",
        skill="verify_claim",
        source_run_id="run-x",
        summary="s",
        payload={},
        proposed_action={
            "executor": "fs.write_file",
            "version": 1,
            "params": {"path": "r.txt", "content": "x"},
            "idempotency_key": "i",
        },
    )
    assert q.lease_one("research", lease_owner="w", ttl_seconds=300) is None


def test_mark_executed_records_result(orc_home: Path) -> None:
    ws_module.create("research")
    approval_id = _approved("research")
    q.lease_one("research", lease_owner="w", ttl_seconds=300)

    q.mark_executed("research", approval_id, result={"bytes_written": 1})
    ex = q.get_execution("research", approval_id)
    assert ex["exec_status"] == "succeeded"
    assert ex["result"] == {"bytes_written": 1}
    # A succeeded action is never leased again.
    assert q.lease_one("research", lease_owner="w2", ttl_seconds=300) is None


def test_mark_failed_retries_then_dies(orc_home: Path) -> None:
    ws_module.create("research")
    approval_id = _approved("research")
    q.lease_one("research", lease_owner="w", ttl_seconds=300)

    status1 = q.mark_failed("research", approval_id, error="boom", max_attempts=2)
    assert status1 == "pending"  # retry available -> leasable again
    q.lease_one("research", lease_owner="w", ttl_seconds=300)
    status2 = q.mark_failed("research", approval_id, error="boom again", max_attempts=2)
    assert status2 == "dead"
    assert q.lease_one("research", lease_owner="w", ttl_seconds=300) is None


def test_duplicate_idempotency_key_cannot_both_execute(orc_home: Path) -> None:
    ws_module.create("research")
    _approved("research", idem="dup")
    _approved("research", idem="dup")  # same idempotency key, different approval

    first = q.lease_one("research", lease_owner="w", ttl_seconds=300)
    assert first is not None
    # The second approval shares the idempotency key; it must not also lease.
    second = q.lease_one("research", lease_owner="w", ttl_seconds=300)
    assert second is None
