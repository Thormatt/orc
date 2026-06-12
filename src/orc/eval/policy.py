"""Tiered-verification policy: the calibrated escalation threshold and the
models for each tier, one row per workspace.

`orc eval calibrate` writes it from the gold set; `tiered_verify` reads it.
Stored in orc.db (not config.toml) because it is calibration *state* — derived
data stamped with which eval produced it — not policy a human hand-edits."""

from __future__ import annotations

from dataclasses import dataclass

from orc.core.clock import now_iso
from orc.paths import workspace_db_path
from orc.storage.db import open_connection, transaction


@dataclass(frozen=True)
class TieredPolicy:
    workspace: str
    tier1_model: str
    tier2_model: str
    top_judge_model: str | None
    escalation_threshold: float
    target: float
    calibrated_at: str
    calibrated_against_eval_id: str | None
    n_gold: int


def save_policy(
    workspace: str,
    *,
    tier1_model: str,
    tier2_model: str,
    top_judge_model: str | None,
    escalation_threshold: float,
    target: float,
    calibrated_against_eval_id: str | None,
    n_gold: int,
) -> None:
    with open_connection(workspace_db_path(workspace)) as conn, transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO tiered_policy(workspace, tier1_model, tier2_model, "
            "top_judge_model, escalation_threshold, target, calibrated_at, "
            "calibrated_against_eval_id, n_gold) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                workspace, tier1_model, tier2_model, top_judge_model,
                escalation_threshold, target, now_iso(),
                calibrated_against_eval_id, n_gold,
            ),
        )


def load_policy(workspace: str) -> TieredPolicy | None:
    with open_connection(workspace_db_path(workspace)) as conn:
        row = conn.execute(
            "SELECT * FROM tiered_policy WHERE workspace=?", (workspace,)
        ).fetchone()
    if row is None:
        return None
    return TieredPolicy(
        workspace=row["workspace"],
        tier1_model=row["tier1_model"],
        tier2_model=row["tier2_model"],
        top_judge_model=row["top_judge_model"],
        escalation_threshold=row["escalation_threshold"],
        target=row["target"],
        calibrated_at=row["calibrated_at"],
        calibrated_against_eval_id=row["calibrated_against_eval_id"],
        n_gold=row["n_gold"],
    )
