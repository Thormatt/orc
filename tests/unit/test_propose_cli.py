"""`orc propose` — the approval queue's front door.

Stages a validated, allow-listed action from the command line so a human (or a
script) can drive the propose -> approve -> execute loop without writing a skill.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from orc.cli import main
from orc.queue import approval as q
from orc.storage import workspace as ws_module


def _allow(orc_home: Path, workspace: str, executor_ids: list[str]) -> None:
    quoted = ", ".join(f'"{e}"' for e in executor_ids)
    (orc_home / "config.toml").write_text(
        f"[workspace.{workspace}.effects]\nallowed = [{quoted}]\n"
    )


def test_propose_json_emits_pending_approval(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")

    result = CliRunner().invoke(
        main,
        [
            "propose",
            "fs.write_file",
            "--params",
            '{"path": "out.txt", "content": "hi"}',
            "--summary",
            "write out.txt",
            "-w",
            "research",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pending"
    assert payload["workspace"] == "research"
    assert payload["executor"] == "fs.write_file"

    appr = q.get("research", payload["approval_id"])
    assert appr.status == "pending"
    assert appr.proposed_action["executor"] == "fs.write_file"
    assert appr.proposed_action["params"] == {"path": "out.txt", "content": "hi"}
    assert appr.source_run_id == payload["run_id"]


def test_propose_reads_params_from_file(orc_home: Path, tmp_path: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    params_file = tmp_path / "params.json"
    params_file.write_text('{"path": "from-file.txt", "content": "filed"}')

    result = CliRunner().invoke(
        main,
        [
            "propose",
            "fs.write_file",
            "--params",
            f"@{params_file}",
            "--summary",
            "write from file",
            "-w",
            "research",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    appr = q.get("research", payload["approval_id"])
    assert appr.proposed_action["params"] == {"path": "from-file.txt", "content": "filed"}


def test_propose_errors_on_missing_params_file(orc_home: Path, tmp_path: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")

    result = CliRunner().invoke(
        main,
        [
            "propose",
            "fs.write_file",
            "--params",
            f"@{tmp_path / 'nope.json'}",
            "--summary",
            "x",
            "-w",
            "research",
        ],
    )

    assert result.exit_code != 0
    assert "nope.json" in result.output


def test_propose_human_output_shows_next_steps(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")

    result = CliRunner().invoke(
        main,
        [
            "propose",
            "fs.write_file",
            "--params",
            '{"path": "out.txt", "content": "hi"}',
            "--summary",
            "write out.txt",
            "-w",
            "research",
        ],
    )

    assert result.exit_code == 0, result.output
    [appr] = q.list_approvals("research")
    assert appr.approval_id in result.output
    assert f"orc approve show {appr.approval_id} -w research" in result.output
    assert f"orc approve accept {appr.approval_id} -w research" in result.output
    assert f"orc execute {appr.approval_id} -w research" in result.output


def test_propose_unknown_executor_lists_known_ids(orc_home: Path) -> None:
    ws_module.create("research")

    result = CliRunner().invoke(
        main,
        ["propose", "no.such", "--params", "{}", "--summary", "x", "-w", "research"],
    )

    assert result.exit_code != 0
    assert "no.such" in result.output
    assert "Known executors:" in result.output
    assert "fs.write_file" in result.output


def test_propose_not_allowed_shows_config_snippet(orc_home: Path) -> None:
    # fs.write_file exists but is not enabled for this workspace -> deny,
    # with copy-pasteable instructions to enable it.
    ws_module.create("research")

    result = CliRunner().invoke(
        main,
        [
            "propose",
            "fs.write_file",
            "--params",
            '{"path": "p", "content": "c"}',
            "--summary",
            "x",
            "-w",
            "research",
        ],
    )

    assert result.exit_code != 0
    assert "config.toml" in result.output
    assert "[workspace.research.effects]" in result.output
    assert 'allowed = ["fs.write_file"]' in result.output


def test_propose_invalid_params_mentions_missing_field(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")

    result = CliRunner().invoke(
        main,
        [
            "propose",
            "fs.write_file",
            "--params",
            '{"path": "p"}',
            "--summary",
            "x",
            "-w",
            "research",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid params for fs.write_file" in result.output
    assert "content" in result.output  # the missing required field
    assert '"required"' in result.output  # the schema is shown


def test_propose_rejects_malformed_params_json(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    result = CliRunner().invoke(
        main,
        ["propose", "fs.write_file", "--params", "{not json", "--summary", "s", "-w", "research"],
    )
    assert result.exit_code != 0
    assert "JSON" in result.output
    assert q.list_approvals("research") == []


def test_propose_rejects_non_object_params(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    result = CliRunner().invoke(
        main,
        ["propose", "fs.write_file", "--params", "[1, 2]", "--summary", "s", "-w", "research"],
    )
    assert result.exit_code != 0
    assert "JSON object" in result.output


def test_propose_unknown_workspace_fails_cleanly(orc_home: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["propose", "fs.write_file", "--params", "{}", "--summary", "s", "-w", "ghost"],
    )
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_propose_passes_idempotency_key_and_approvers(orc_home: Path) -> None:
    _allow(orc_home, "research", ["fs.write_file"])
    ws_module.create("research")
    result = CliRunner().invoke(
        main,
        [
            "propose", "fs.write_file",
            "--params", '{"path": "r.md", "content": "hi"}',
            "--summary", "ship",
            "-w", "research",
            "--idempotency-key", "key-1",
            "--approvers", "2",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    approval_id = json.loads(result.output)["approval_id"]
    item = q.get("research", approval_id)
    assert item.proposed_action["idempotency_key"] == "key-1"
    assert item.approvers_required == 2
