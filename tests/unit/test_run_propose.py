"""Run.propose(): the analysis plane stages a validated, policy-checked proposal."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.effects.action import ActionValidationError
from orc.effects.base import ExecutorNotAllowedError, ExecutorNotFoundError
from orc.queue import approval as q
from orc.runs import open_run
from orc.storage import workspace as ws_module


def _allow(orc_home: Path, workspace: str, executor_ids: list[str]) -> None:
    quoted = ", ".join(f'"{e}"' for e in executor_ids)
    (orc_home / "config.toml").write_text(
        f"[workspace.{workspace}.effects]\nallowed = [{quoted}]\n"
    )


def test_propose_enqueues_pending_approval_and_records_event(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws = ws_module.create("research")
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        approval_id = run.propose(
            executor="fs.write_file",
            params={"path": "out.txt", "content": "hi"},
            summary="write out.txt",
        )
        run.close(output={"approval_id": approval_id})

    appr = q.get("research", approval_id)
    assert appr.status == "pending"
    assert appr.proposed_action["executor"] == "fs.write_file"
    assert appr.proposed_action["idempotency_key"]  # auto-generated
    assert appr.source_run_id == run.run_id


def test_propose_refuses_unknown_executor(orc_home: Path) -> None:
    ws = ws_module.create("research")
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        with pytest.raises(ExecutorNotFoundError):
            run.propose(executor="no.such", params={}, summary="x")
        run.close(output={})


def test_propose_refuses_executor_not_in_allowlist(orc_home: Path) -> None:
    # fs.write_file exists but is not enabled for this workspace -> deny.
    ws = ws_module.create("research")
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        with pytest.raises(ExecutorNotAllowedError):
            run.propose(
                executor="fs.write_file",
                params={"path": "p", "content": "c"},
                summary="x",
            )
        run.close(output={})


def test_propose_validates_params(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws = ws_module.create("research")
    with open_run(ws, directive="research", skill="verify_claim", inputs={}) as run:
        with pytest.raises(ActionValidationError):
            run.propose(
                executor="fs.write_file",
                params={"path": "p"},  # missing content
                summary="x",
            )
        run.close(output={})
