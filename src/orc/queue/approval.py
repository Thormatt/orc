"""Approval queue.

Directives drop proposed actions here; humans accept/reject. The queue is the
boundary between "Orc analyzed and produced a recommendation" and "anything
mutates outside Orc's own database." Write-path MCPs (when they exist) drain
from approved entries — never directly from skill outputs.

Design notes:
- One table per workspace (per-workspace SQLite).
- Approvals are immutable once decided; reverting requires a new approval entry.
- `payload` and `proposed_action` are JSON strings — the schema doesn't constrain
  the shape because directives have different proposal types. The directive that
  enqueued is responsible for round-tripping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from orc.core.clock import now_iso
from orc.core.ids import new_id
from orc.errors import OrcError
from orc.paths import workspace_db_path
from orc.storage.db import open_connection, transaction

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS approval (
    approval_id     TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL,
    directive       TEXT NOT NULL,
    skill           TEXT NOT NULL,
    source_run_id   TEXT NOT NULL,
    status          TEXT NOT NULL,
    summary         TEXT NOT NULL,
    payload         TEXT NOT NULL,
    proposed_action TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT,
    decision_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approval(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approval_source_run ON approval(source_run_id);
"""

VALID_STATUS = {"pending", "approved", "rejected", "expired"}


class ApprovalNotFoundError(OrcError):
    pass


class ApprovalAlreadyDecidedError(OrcError):
    pass


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
    created_at: str
    decided_at: str | None
    decided_by: str | None
    decision_note: str | None


def ensure_approval_table(workspace: str) -> None:
    """Idempotent: create the approval table if missing. Safe to call repeatedly."""
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn:
        conn.executescript(_TABLE_DDL)


def enqueue(
    workspace: str,
    *,
    directive: str,
    skill: str,
    source_run_id: str,
    summary: str,
    payload: dict[str, Any],
    proposed_action: dict[str, Any] | None = None,
) -> str:
    """Add a pending approval. Returns the new approval_id."""
    ensure_approval_table(workspace)
    approval_id = new_id()
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO approval(approval_id, workspace, directive, skill, source_run_id, "
            "status, summary, payload, proposed_action, created_at) "
            "VALUES (?,?,?,?,?, 'pending', ?, ?, ?, ?)",
            (
                approval_id,
                workspace,
                directive,
                skill,
                source_run_id,
                summary,
                json.dumps(payload, default=str),
                json.dumps(proposed_action, default=str) if proposed_action is not None else None,
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
    return _row_to_approval(row)


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
    return [_row_to_approval(r) for r in rows]


def accept(
    workspace: str,
    approval_id: str,
    *,
    decided_by: str | None = None,
    note: str | None = None,
) -> Approval:
    return _decide(workspace, approval_id, "approved", decided_by, note)


def reject(
    workspace: str,
    approval_id: str,
    *,
    decided_by: str | None = None,
    note: str | None = None,
) -> Approval:
    return _decide(workspace, approval_id, "rejected", decided_by, note)


def _decide(
    workspace: str,
    approval_id: str,
    new_status: str,
    decided_by: str | None,
    note: str | None,
) -> Approval:
    if new_status not in VALID_STATUS:
        raise ValueError(f"Invalid status: {new_status!r}")
    ensure_approval_table(workspace)
    db_path = workspace_db_path(workspace)
    with open_connection(db_path) as conn, transaction(conn):
        row = conn.execute(
            "SELECT status FROM approval WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ApprovalNotFoundError(f"Approval not found: {approval_id}")
        if row["status"] != "pending":
            raise ApprovalAlreadyDecidedError(
                f"Approval {approval_id} is already {row['status']}"
            )
        conn.execute(
            "UPDATE approval SET status = ?, decided_at = ?, decided_by = ?, decision_note = ? "
            "WHERE approval_id = ?",
            (new_status, now_iso(), decided_by, note, approval_id),
        )
    return get(workspace, approval_id)


def _row_to_approval(row: object) -> Approval:
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
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        decision_note=row["decision_note"],
    )
