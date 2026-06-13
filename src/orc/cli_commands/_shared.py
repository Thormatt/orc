"""Helpers shared by CLI command modules."""

from __future__ import annotations

import click

from orc.errors import WorkspaceNotFoundError
from orc.storage import workspace as ws_module


def resolve_workspace(name: str | None) -> ws_module.Workspace:
    """Resolve a workspace name (or the env default) to a Workspace, mapping
    WorkspaceNotFoundError to a clean CLI error."""
    try:
        return ws_module.resolve(name)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
