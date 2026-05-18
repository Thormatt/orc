"""Approval queue.

Directives drop proposed actions here; humans accept/reject. The queue is the
boundary between "Orc analyzed and produced a recommendation" and "anything
mutates outside Orc's own database." Write-path MCPs (when they exist) drain
from approved entries — never directly from skill outputs.

Multi-approver workflow (EU AI Act Article 14 §5):
- An approval row has `approvers_required` (default 1; set on enqueue for systems
  that require multi-person verification).
- Each decision (accept or reject) is recorded as a row in `approval_decision`,
  one per natural person.
- Status flips to `approved` when count(accept) >= approvers_required AND no
  reject has been recorded.
- Status flips to `rejected` as soon as ANY natural person rejects.

Design notes:
- One table per workspace (per-workspace SQLite).
- Approvals are immutable once decided; reverting requires a new approval entry.
- `payload` and `proposed_action` are JSON strings — the schema doesn't constrain
  the shape because directives have different proposal types. The directive that
  enqueued is responsible for round-tripping.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from orc.core.clock import now_iso
from orc.core.ids import new_id
from orc.errors import OrcError
from orc.paths import workspace_db_path
from orc.storage.db import open_connection, transaction

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS approval (
    approval_id        TEXT PRIMARY KEY,
    workspace          TEXT NOT NULL,
    directive          TEXT NOT NULL,
    skill              TEXT NOT NULL,
    source_run_id      TEXT NOT NULL,
    status             TEXT NOT NULL,
    summary            TEXT NOT NULL,
    payload            TEXT NOT NULL,
    proposed_action    TEXT,
    approvers_required INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    decided_at         TEXT,
    decided_by         TEXT,
    decision_note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approval(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approval_source_run ON approval(source_run_id);

CREATE TABLE IF NOT EXISTS approval_decision (
    decision_id     TEXT PRIMARY KEY,
    approval_id     TEXT NOT NULL REFERENCES approval(approval_id) ON DELETE CASCADE,
    decision        TEXT NOT NULL,
    decided_by      TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    note            TEXT,
    UNIQUE (approval_id, decided_by)
);
CREATE INDEX IF NOT EXISTS idx_approval_decision_approval ON approval_decision(approval_id);
"""

VALID_STATUS = {"pending", "approved", "rejected", "expired"}
ACCEPT = "accept"
REJECT = "reject"


class ApprovalNotFoundError(OrcError):
    pass


class ApprovalAlreadyDecidedError(OrcError):
    """The approval is no longer pending — a final decision is already recorded."""


class DuplicateApproverError(OrcError):
    """A natural person tried to record a second decision on the same approval."""


@dataclass(frozen=True)
class Decision:
    decision_id: str
    approval_id: str
    decision: str           # "accept" | "reject"
    decided_by: str
    decided_at: str
    note: str | None


@dataclass(frozen=True)
class Approval:
    approval_id: str
    workspace: str
    directive: str
    skill: str
    source_run_id: str
    status: str
    summary: str
    payload: dict[str, Any]
    proposed_action: dict[str, Any] | None
    approvers_required: int
    created_at: str
    decided_at: str | None
    decided_by: str | None
    decision_note: str | None
    decisions: list[Decision] = field(default_factory=list)

    @property
    def accept_count(self) -> int:
        return sum(1 for d in self.decisions if d.decision == ACCEPT)

    @property
    def reject_count(self) -> int:
        return sum(1 for d in self.decisions if d.decision == REJECT)

    @property
    def progress(self) -> str:
        return f"{self.accept_count}/{self.approvers_required}"


def ensure_approval_table(workspace: str) -> None:
    """Idempotent: create approval tables if missing, add new columns to existing.

    Safe to call repeatedly. Handles the migration from v0.1.0 (single-approver,
    no decisions table) to multi-approver schema for already-created workspaces.
    """
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn:
        conn.executescript(_TABLE_DDL)
        # Lazy migration: pre-multi-approver workspaces lack `approvers_required`.
        # Already-migrated tables raise sqlite3.OperationalError on duplicate column.
        with contextlib.suppress(Exception):
            conn.execute(
                "ALTER TABLE approval ADD COLUMN approvers_required INTEGER NOT NULL DEFAULT 1"
            )


def enqueue(
    workspace: str,
    *,
    directive: str,
    skill: str,
    source_run_id: str,
    summary: str,
    payload: dict[str, Any],
    proposed_action: dict[str, Any] | None = None,
    approvers_required: int = 1,
) -> str:
    """Add a pending approval. Returns the new approval_id.

    `approvers_required` defaults to 1 (backward-compat single-approver flow). Set
    higher (typically 2) for Annex III systems requiring multi-person verification
    per Article 14 §5.
    """
    if approvers_required < 1:
        raise ValueError("approvers_required must be >= 1")
    ensure_approval_table(workspace)
    approval_id = new_id()
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO approval(approval_id, workspace, directive, skill, source_run_id, "
            "status, summary, payload, proposed_action, approvers_required, created_at) "
            "VALUES (?,?,?,?,?, 'pending', ?, ?, ?, ?, ?)",
            (
                approval_id,
                workspace,
                directive,
                skill,
                source_run_id,
                summary,
                json.dumps(payload, default=str),
                json.dumps(proposed_action, default=str) if proposed_action is not None else None,
                approvers_required,
                now_iso(),
            ),
        )
    return approval_id


def get(workspace: str, approval_id: str) -> Approval:
    ensure_approval_table(workspace)
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM approval WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ApprovalNotFoundError(f"Approval not found: {approval_id}")
        decisions = _decisions_for(conn, approval_id)
    return _row_to_approval(row, decisions)


def list_approvals(
    workspace: str,
    *,
    status: str | None = "pending",
    limit: int = 50,
) -> list[Approval]:
    ensure_approval_table(workspace)
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM approval ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            if status not in VALID_STATUS:
                raise ValueError(f"Invalid status filter: {status!r}")
            rows = conn.execute(
                "SELECT * FROM approval WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        out: list[Approval] = []
        for r in rows:
            decisions = _decisions_for(conn, r["approval_id"])
            out.append(_row_to_approval(r, decisions))
    return out


def accept(
    workspace: str,
    approval_id: str,
    *,
    decided_by: str | None = None,
    note: str | None = None,
) -> Approval:
    """Record one accept decision. Status flips to approved when accepts reach the threshold."""
    return _record_decision(workspace, approval_id, ACCEPT, decided_by, note)


def reject(
    workspace: str,
    approval_id: str,
    *,
    decided_by: str | None = None,
    note: str | None = None,
) -> Approval:
    """Record one reject decision. Any single rejection immediately blocks the approval."""
    return _record_decision(workspace, approval_id, REJECT, decided_by, note)


def _record_decision(
    workspace: str,
    approval_id: str,
    decision: str,
    decided_by: str | None,
    note: str | None,
) -> Approval:
    if decision not in (ACCEPT, REJECT):
        raise ValueError(f"Invalid decision: {decision!r}")
    if not decided_by:
        raise ValueError("decided_by is required: name the natural person making the decision")

    ensure_approval_table(workspace)
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn, transaction(conn):
        row = conn.execute(
            "SELECT status, approvers_required FROM approval WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ApprovalNotFoundError(f"Approval not found: {approval_id}")
        if row["status"] != "pending":
            raise ApprovalAlreadyDecidedError(
                f"Approval {approval_id} is already {row['status']}"
            )
        approvers_required = int(row["approvers_required"])

        # Check duplicate-approver before insert so we can give a clean error.
        dup = conn.execute(
            "SELECT 1 FROM approval_decision WHERE approval_id = ? AND decided_by = ?",
            (approval_id, decided_by),
        ).fetchone()
        if dup is not None:
            raise DuplicateApproverError(
                f"{decided_by!r} has already recorded a decision on approval {approval_id}"
            )

        now = now_iso()
        conn.execute(
            "INSERT INTO approval_decision(decision_id, approval_id, decision, decided_by, "
            "decided_at, note) VALUES (?,?,?,?,?,?)",
            (new_id(), approval_id, decision, decided_by, now, note),
        )

        # Re-tally and decide whether to flip status.
        counts = conn.execute(
            "SELECT decision, COUNT(*) AS n FROM approval_decision "
            "WHERE approval_id = ? GROUP BY decision",
            (approval_id,),
        ).fetchall()
        n_accept = next((r["n"] for r in counts if r["decision"] == ACCEPT), 0)
        n_reject = next((r["n"] for r in counts if r["decision"] == REJECT), 0)

        new_status: str | None = None
        if n_reject > 0:
            new_status = "rejected"
        elif n_accept >= approvers_required:
            new_status = "approved"

        if new_status is not None:
            conn.execute(
                "UPDATE approval SET status = ?, decided_at = ?, decided_by = ?, "
                "decision_note = ? WHERE approval_id = ?",
                (new_status, now, decided_by, note, approval_id),
            )
    return get(workspace, approval_id)


def _decisions_for(conn, approval_id: str) -> list[Decision]:
    rows = conn.execute(
        "SELECT decision_id, approval_id, decision, decided_by, decided_at, note "
        "FROM approval_decision WHERE approval_id = ? ORDER BY decided_at ASC",
        (approval_id,),
    ).fetchall()
    return [
        Decision(
            decision_id=r["decision_id"],
            approval_id=r["approval_id"],
            decision=r["decision"],
            decided_by=r["decided_by"],
            decided_at=r["decided_at"],
            note=r["note"],
        )
        for r in rows
    ]


def _row_to_approval(row: object, decisions: list[Decision]) -> Approval:
    # `approvers_required` is added by lazy migration; older rows may surface None.
    approvers_required: int | None
    try:
        approvers_required = row["approvers_required"]
    except (IndexError, KeyError):
        approvers_required = 1
    if approvers_required is None:
        approvers_required = 1
    return Approval(
        approval_id=row["approval_id"],
        workspace=row["workspace"],
        directive=row["directive"],
        skill=row["skill"],
        source_run_id=row["source_run_id"],
        status=row["status"],
        summary=row["summary"],
        payload=json.loads(row["payload"]),
        proposed_action=json.loads(row["proposed_action"]) if row["proposed_action"] else None,
        approvers_required=int(approvers_required),
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        decision_note=row["decision_note"],
        decisions=decisions,
    )
