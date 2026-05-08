"""Smoke tests for path helpers."""

from __future__ import annotations

from pathlib import Path

from orc import paths


def test_orc_home_respects_env(orc_home: Path) -> None:
    assert paths.orc_home() == orc_home


def test_workspace_layout_is_consistent(orc_home: Path) -> None:
    name = "demo"
    assert paths.workspace_root(name) == orc_home / "workspaces" / "demo"
    assert paths.workspace_db_path(name) == orc_home / "workspaces" / "demo" / "orc.db"
    assert paths.workspace_evidence_dir(name) == orc_home / "workspaces" / "demo" / "evidence"
    assert paths.workspace_traces_dir(name) == orc_home / "workspaces" / "demo" / "traces"


def test_trace_json_path_uses_year_month(orc_home: Path) -> None:
    p = paths.trace_json_path("demo", "01HXY", "2026-05-07T14:32:11.234Z")
    assert p == orc_home / "workspaces" / "demo" / "traces" / "2026" / "05" / "01HXY.json"
