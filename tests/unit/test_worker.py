"""Effect-plane worker: drain_once executes approved actions, backs off on failure."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from orc import effects
from orc.cli import main
from orc.effects.worker import drain_once
from orc.paths import workspace_effects_dir
from orc.queue import approval as q
from orc.storage import workspace as ws_module


def _allow(orc_home: Path, workspace: str, executor_ids: list[str]) -> None:
    quoted = ", ".join(f'"{e}"' for e in executor_ids)
    (orc_home / "config.toml").write_text(
        f"[workspace.{workspace}.effects]\nallowed = [{quoted}]\n"
    )


def _enqueue_approved(workspace: str, *, executor: str, params: dict, idem: str) -> str:
    approval_id = q.enqueue(
        workspace,
        directive="research",
        skill="verify_claim",
        source_run_id="run-x",
        summary="do it",
        payload={},
        proposed_action={
            "executor": executor,
            "version": 1,
            "params": params,
            "idempotency_key": idem,
        },
    )
    q.accept(workspace, approval_id, decided_by="alice")
    return approval_id


def test_drain_once_executes_all_approved(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    a1 = _enqueue_approved(
        "research", executor="fs.write_file",
        params={"path": "a.txt", "content": "A"}, idem="d1",
    )
    a2 = _enqueue_approved(
        "research", executor="fs.write_file",
        params={"path": "b.txt", "content": "B"}, idem="d2",
    )

    summary = drain_once("research", lease_owner="worker-test")

    assert summary == {"succeeded": 2, "failed": 0}
    out = workspace_effects_dir("research")
    assert (out / "a.txt").read_text() == "A"
    assert (out / "b.txt").read_text() == "B"
    assert q.get_execution("research", a1)["exec_status"] == "succeeded"
    assert q.get_execution("research", a2)["exec_status"] == "succeeded"


def test_drain_once_skips_unapproved(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    q.enqueue(
        "research", directive="research", skill="verify_claim",
        source_run_id="r", summary="pending", payload={},
        proposed_action={
            "executor": "fs.write_file", "version": 1,
            "params": {"path": "p.txt", "content": "P"}, "idempotency_key": "d3",
        },
    )
    summary = drain_once("research", lease_owner="worker-test")
    assert summary == {"succeeded": 0, "failed": 0}


def test_drain_once_records_failure_once_and_backs_off(orc_home: Path) -> None:
    """A failing action is attempted exactly once per pass (backoff prevents
    same-pass re-lease) and left retryable for the next cycle."""

    class _Boom:
        id = "test.boom"
        version = 1
        params_schema = {"type": "object", "properties": {}}
        required_credential = None

        def execute(self, *, params, credential, workspace):
            raise RuntimeError("kaboom")

    effects.register(_Boom())
    _allow(orc_home, "research", ["test.boom"])
    ws_module.create("research")
    aid = _enqueue_approved("research", executor="test.boom", params={}, idem="d4")

    summary = drain_once("research", lease_owner="worker-test", max_attempts=3)

    assert summary == {"succeeded": 0, "failed": 1}  # attempted once, not looped
    ex = q.get_execution("research", aid)
    assert ex["exec_status"] == "pending"  # retryable next cycle
    assert ex["attempts"] == 1


def test_worker_cli_once_drains(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    _enqueue_approved(
        "research", executor="fs.write_file",
        params={"path": "cli.txt", "content": "C"}, idem="d5",
    )
    result = CliRunner().invoke(main, ["worker", "--once", "-w", "research"])
    assert result.exit_code == 0, result.output
    assert (workspace_effects_dir("research") / "cli.txt").read_text() == "C"
