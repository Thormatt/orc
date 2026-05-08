"""Filesystem layout helpers.

Layout:
  ~/.orc/
    config.toml
    workspaces/
      <name>/
        orc.db
        evidence/<evidence_id>.<ext>
        traces/<YYYY>/<MM>/<run_id>.json
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "ORC_HOME"


def orc_home() -> Path:
    """Root directory for all Orc state. Override with $ORC_HOME for tests."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".orc"


def workspaces_root() -> Path:
    return orc_home() / "workspaces"


def workspace_root(name: str) -> Path:
    return workspaces_root() / name


def workspace_db_path(name: str) -> Path:
    return workspace_root(name) / "orc.db"


def workspace_evidence_dir(name: str) -> Path:
    return workspace_root(name) / "evidence"


def workspace_traces_dir(name: str) -> Path:
    return workspace_root(name) / "traces"


def trace_json_path(workspace: str, run_id: str, started_at_iso: str) -> Path:
    """Path for a trace JSON file. started_at_iso must be UTC ISO 8601."""
    year = started_at_iso[:4]
    month = started_at_iso[5:7]
    return workspace_traces_dir(workspace) / year / month / f"{run_id}.json"


def config_path() -> Path:
    return orc_home() / "config.toml"
