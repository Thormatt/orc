"""Shared pytest fixtures.

Sets ORC_HOME to a per-test temp directory so tests never touch the user's real ~/.orc/.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def orc_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "orc-home"
    home.mkdir()
    monkeypatch.setenv("ORC_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _no_real_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block accidental network calls in tests that don't explicitly opt in."""
    if not os.environ.get("ORC_TEST_ALLOW_LIVE_LLM"):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
