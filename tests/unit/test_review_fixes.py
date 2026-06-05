"""Pre-merge hardening from the final effect-plane review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from orc import effects
from orc.cli import main
from orc.effects.action import Action, ActionValidationError, validate_params
from orc.effects.builtin import gmail as gmail_mod
from orc.queue import approval as q
from orc.queue.approval import ActionDeadError
from orc.storage import workspace as ws_module


def _allow(orc_home: Path, workspace: str, executor_ids: list[str]) -> None:
    quoted = ", ".join(f'"{e}"' for e in executor_ids)
    (orc_home / "config.toml").write_text(
        f"[workspace.{workspace}.effects]\nallowed = [{quoted}]\n"
    )


def _enqueue_approved(workspace: str, *, executor: str, params: dict, idem: str) -> str:
    approval_id = q.enqueue(
        workspace, directive="research", skill="verify_claim", source_run_id="r",
        summary="s", payload={},
        proposed_action={
            "executor": executor, "version": 1, "params": params, "idempotency_key": idem,
        },
    )
    q.accept(workspace, approval_id, decided_by="alice")
    return approval_id


# --- 1. dead actions must not be silently re-executed -------------------------


def test_begin_execution_refuses_dead(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    aid = _enqueue_approved(
        "research", executor="fs.write_file",
        params={"path": "p.txt", "content": "c"}, idem="dead-1",
    )
    # Drive it to dead.
    q.begin_execution("research", aid, lease_owner="w")
    assert q.mark_failed("research", aid, error="x", max_attempts=2) == "pending"
    q.begin_execution("research", aid, lease_owner="w")
    assert q.mark_failed("research", aid, error="x", max_attempts=2) == "dead"

    with pytest.raises(ActionDeadError):
        q.begin_execution("research", aid, lease_owner="w")


def test_execute_cli_refuses_dead(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    aid = _enqueue_approved(
        "research", executor="fs.write_file",
        params={"path": "p.txt", "content": "c"}, idem="dead-2",
    )
    q.begin_execution("research", aid, lease_owner="w")
    q.mark_failed("research", aid, error="x", max_attempts=1)  # -> dead immediately
    assert q.get_execution("research", aid)["exec_status"] == "dead"

    result = CliRunner().invoke(main, ["execute", aid, "-w", "research"])
    assert result.exit_code != 0
    assert "dead" in result.output.lower()
    assert q.get_execution("research", aid)["exec_status"] == "dead"  # unchanged


# --- 2. additionalProperties is rejected -------------------------------------


def test_validate_params_rejects_additional_properties() -> None:
    schema = {
        "type": "object",
        "required": ["path"],
        "additionalProperties": False,
        "properties": {"path": {"type": "string"}},
    }
    validate_params({"path": "ok"}, schema)  # no raise
    with pytest.raises(ActionValidationError):
        validate_params({"path": "ok", "evil": "extra"}, schema)


def test_fs_write_file_rejects_undeclared_params(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    bad = Action(
        executor="fs.write_file", version=1,
        params={"path": "p.txt", "content": "c", "symlink_target": "/etc/passwd"},
        idempotency_key="extra-1",
    )
    with pytest.raises(ActionValidationError):
        effects.run_action("research", bad)


# --- 3. Gmail HTTP errors must not leak the response body --------------------


def test_gmail_error_does_not_leak_response_body(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_module.create("research")
    _allow(orc_home, "research", ["gmail.send_draft"])
    monkeypatch.setenv("GMAIL_TOKEN", "ya29.fake")

    class _Resp:
        status_code = 403
        text = "SENSITIVE: account=victim@example.com scope=https://mail.google.com/"

        def raise_for_status(self) -> None:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)

        def json(self) -> dict[str, Any]:
            return {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _Resp:
        return _Resp()

    monkeypatch.setattr(gmail_mod.httpx, "post", fake_post)

    action = Action(
        executor="gmail.send_draft", version=1,
        params={"draft_id": "d1"}, idempotency_key="g-err-1",
    )
    with pytest.raises(Exception) as exc_info:
        effects.run_action("research", action)
    assert "victim@example.com" not in str(exc_info.value)
    assert "SENSITIVE" not in str(exc_info.value)
