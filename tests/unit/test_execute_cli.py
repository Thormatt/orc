"""`orc execute <approval_id>` — the effect-plane entry point.

This is the process that holds write credentials and carries out approved actions.
The analysis plane never runs it.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from orc import effects
from orc.cli import main
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
        summary="do the thing",
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


def test_execute_runs_approved_action(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    approval_id = _enqueue_approved(
        "research",
        executor="fs.write_file",
        params={"path": "out.txt", "content": "shipped"},
        idem="i1",
    )

    result = CliRunner().invoke(main, ["execute", approval_id, "-w", "research"])
    assert result.exit_code == 0, result.output
    assert (workspace_effects_dir("research") / "out.txt").read_text() == "shipped"
    assert q.get_execution("research", approval_id)["exec_status"] == "succeeded"


def test_execute_refuses_unapproved(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    approval_id = q.enqueue(
        "research",
        directive="research",
        skill="verify_claim",
        source_run_id="run-x",
        summary="pending one",
        payload={},
        proposed_action={
            "executor": "fs.write_file",
            "version": 1,
            "params": {"path": "p", "content": "c"},
            "idempotency_key": "i2",
        },
    )
    result = CliRunner().invoke(main, ["execute", approval_id, "-w", "research"])
    assert result.exit_code != 0
    assert "approved" in result.output.lower()


def test_execute_is_idempotent_on_second_run(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    approval_id = _enqueue_approved(
        "research",
        executor="fs.write_file",
        params={"path": "once.txt", "content": "v1"},
        idem="i3",
    )
    first = CliRunner().invoke(main, ["execute", approval_id, "-w", "research"])
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(main, ["execute", approval_id, "-w", "research"])
    assert second.exit_code == 0
    assert "already" in second.output.lower()


def test_execute_fails_without_required_credential(orc_home: Path) -> None:
    """The keystone: an executor needing a write token cannot run in a process
    whose env lacks it. `orc execute` surfaces this and records the failure."""

    class _NeedsToken:
        id = "test.cli_needs_token"
        version = 1
        params_schema = {"type": "object", "properties": {}}
        required_credential = "ORC_TEST_CLI_TOKEN"

        def execute(self, *, params, credential, workspace):
            return {"ok": True}

    effects.register(_NeedsToken())
    _allow(orc_home, "research", ["test.cli_needs_token"])
    ws_module.create("research")
    approval_id = _enqueue_approved(
        "research", executor="test.cli_needs_token", params={}, idem="i4"
    )

    result = CliRunner().invoke(main, ["execute", approval_id, "-w", "research"])
    assert result.exit_code != 0
    assert "credential" in result.output.lower()
    assert q.get_execution("research", approval_id)["exec_status"] in {"pending", "dead"}
