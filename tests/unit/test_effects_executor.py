"""Executor registry, capability allow-list, guarded run_action, fs.write_file."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc import effects
from orc.effects.action import Action, ActionValidationError
from orc.effects.base import (
    ExecutorNotAllowedError,
    ExecutorNotFoundError,
    MissingCredentialError,
)
from orc.paths import workspace_effects_dir


def _allow(orc_home: Path, workspace: str, executor_ids: list[str]) -> None:
    """Write a config.toml enabling executors for a workspace."""
    quoted = ", ".join(f'"{e}"' for e in executor_ids)
    (orc_home / "config.toml").write_text(
        f"[workspace.{workspace}.effects]\nallowed = [{quoted}]\n"
    )


def test_registry_has_fs_write_file_and_rejects_unknown() -> None:
    assert effects.get("fs.write_file").id == "fs.write_file"
    with pytest.raises(ExecutorNotFoundError):
        effects.get("does.not.exist")


def test_allowed_for_reads_config_deny_by_default(orc_home: Path) -> None:
    assert effects.allowed_for("research") == set()  # no config -> deny
    _allow(orc_home, "research", ["fs.write_file"])
    assert effects.allowed_for("research") == {"fs.write_file"}


def test_run_action_writes_file_within_workspace_sandbox(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    action = Action(
        executor="fs.write_file",
        version=1,
        params={"path": "report.txt", "content": "hello"},
        idempotency_key="k1",
    )
    result = effects.run_action("research", action)
    written = workspace_effects_dir("research") / "report.txt"
    assert written.read_text() == "hello"
    assert result["bytes_written"] == 5


def test_run_action_refuses_executor_not_in_allowlist(orc_home: Path) -> None:
    # config absent -> deny-by-default
    action = Action(
        executor="fs.write_file",
        version=1,
        params={"path": "x.txt", "content": "y"},
        idempotency_key="k2",
    )
    with pytest.raises(ExecutorNotAllowedError):
        effects.run_action("research", action)


def test_fs_write_file_rejects_path_traversal(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    action = Action(
        executor="fs.write_file",
        version=1,
        params={"path": "../../escape.txt", "content": "nope"},
        idempotency_key="k3",
    )
    with pytest.raises(ValueError):
        effects.run_action("research", action)
    assert not (orc_home / "escape.txt").exists()


def test_run_action_validates_params(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    action = Action(
        executor="fs.write_file",
        version=1,
        params={"path": "x.txt"},  # missing 'content'
        idempotency_key="k4",
    )
    with pytest.raises(ActionValidationError):
        effects.run_action("research", action)


def test_run_action_requires_credential_when_executor_declares_one(orc_home: Path) -> None:
    """The credential-separation mechanism: an executor that declares a required
    credential cannot run in a process whose env lacks that token — this is what
    stops the analysis plane from executing effects."""

    class _NeedsToken:
        id = "test.needs_token"
        version = 1
        params_schema = {"type": "object", "properties": {}}
        required_credential = "TEST_WRITE_TOKEN"

        def execute(self, *, params, credential, workspace):
            return {"saw_credential": credential}

    effects.register(_NeedsToken())
    _allow(orc_home, "research", ["test.needs_token"])
    action = Action(
        executor="test.needs_token",
        version=1,
        params={},
        idempotency_key="k5",
    )

    with pytest.raises(MissingCredentialError):
        effects.run_action("research", action)


def test_run_action_passes_credential_when_present(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NeedsToken:
        id = "test.needs_token2"
        version = 1
        params_schema = {"type": "object", "properties": {}}
        required_credential = "TEST_WRITE_TOKEN2"

        def execute(self, *, params, credential, workspace):
            return {"saw_credential": credential}

    effects.register(_NeedsToken())
    _allow(orc_home, "research", ["test.needs_token2"])
    monkeypatch.setenv("TEST_WRITE_TOKEN2", "s3cret")
    action = Action(
        executor="test.needs_token2",
        version=1,
        params={},
        idempotency_key="k6",
    )

    result = effects.run_action("research", action)
    assert result["saw_credential"] == "s3cret"
