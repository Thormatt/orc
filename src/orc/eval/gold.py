"""Per-workspace gold-set store: human-confirmed (claim -> verdict) labels.

A gold entry pins the `corpus_version` it was labeled against, because
chunk-level relevance (`relevant_chunk_ids`) is only valid for that snapshot —
chunk IDs change on re-ingest. Judge-accuracy labels (the verdict) survive
re-ingest; retrieval-recall labels must be read frozen against this version."""

from __future__ import annotations

import json
from dataclasses import dataclass

from orc.core.clock import now_iso
from orc.core.ids import new_id
from orc.paths import workspace_db_path
from orc.storage.db import open_connection, transaction

VALID_LABELS = frozenset({"supported", "contradicted", "not_found", "partial"})


@dataclass(frozen=True)
class GoldClaim:
    gold_id: str
    workspace: str
    claim: str
    expected_label: str
    corpus_version: int
    relevant_chunk_ids: list[str] | None
    source: str
    source_run_id: str | None
    note: str | None
    added_at: str
    added_by: str | None


def add(
    workspace: str,
    *,
    claim: str,
    expected_label: str,
    corpus_version: int,
    source: str,
    relevant_chunk_ids: list[str] | None = None,
    source_run_id: str | None = None,
    note: str | None = None,
    added_by: str | None = None,
) -> str:
    if expected_label not in VALID_LABELS:
        raise ValueError(f"expected_label must be one of {sorted(VALID_LABELS)}")
    gold_id = new_id()
    with open_connection(workspace_db_path(workspace)) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO gold_claim(gold_id, workspace, claim, expected_label, "
            "corpus_version, relevant_chunk_ids, source, source_run_id, note, "
            "added_at, added_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                gold_id,
                workspace,
                claim,
                expected_label,
                corpus_version,
                json.dumps(relevant_chunk_ids) if relevant_chunk_ids else None,
                source,
                source_run_id,
                note,
                now_iso(),
                added_by,
            ),
        )
    return gold_id


def list_gold(workspace: str) -> list[GoldClaim]:
    with open_connection(workspace_db_path(workspace)) as conn:
        rows = conn.execute(
            "SELECT * FROM gold_claim WHERE workspace=? ORDER BY added_at, gold_id",
            (workspace,),
        ).fetchall()
    return [
        GoldClaim(
            gold_id=r["gold_id"],
            workspace=r["workspace"],
            claim=r["claim"],
            expected_label=r["expected_label"],
            corpus_version=r["corpus_version"],
            relevant_chunk_ids=json.loads(r["relevant_chunk_ids"])
            if r["relevant_chunk_ids"]
            else None,
            source=r["source"],
            source_run_id=r["source_run_id"],
            note=r["note"],
            added_at=r["added_at"],
            added_by=r["added_by"],
        )
        for r in rows
    ]
