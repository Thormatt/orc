"""Shared CLI helper tests."""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from orc.cli_commands._shared import resolve_workspace
from orc.storage import workspace as ws_module


def test_resolve_workspace_returns_existing_workspace(orc_home: Path) -> None:
    ws_module.create("demo")
    ws = resolve_workspace("demo")
    assert ws.name == "demo"


def test_resolve_workspace_maps_missing_workspace_to_click_exception(orc_home: Path) -> None:
    with pytest.raises(click.ClickException, match="nope"):
        resolve_workspace("nope")
