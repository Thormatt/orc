"""Shared pytest fixtures.

Sets ORC_HOME to a per-test temp directory so tests never touch the user's real ~/.orc/.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._fake_embedder import FakeEmbedder

# Every env var that lets orc.llm.client.get_client() construct a live provider.
# get_client() PREFERS OPENROUTER_API_KEY over ANTHROPIC_API_KEY, and ORC_PROVIDER
# can force either path, so stripping only the Anthropic key is not enough.
_LIVE_LLM_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "ORC_PROVIDER")


@pytest.fixture
def orc_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "orc-home"
    home.mkdir()
    monkeypatch.setenv("ORC_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _no_live_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block accidental network calls in tests that don't explicitly opt in."""
    if not os.environ.get("ORC_TEST_ALLOW_LIVE_LLM"):
        for var in _LIVE_LLM_ENV_VARS:
            monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_embedder() -> Iterator[FakeEmbedder]:
    """Install a deterministic FakeEmbedder via the embedder factory hook.

    Tests script semantic hits through fake.vocabulary (keyword -> dimension).
    The factory is reset afterwards so the cache never leaks across tests.
    """
    from orc.retrieval.embedder import set_embedder_factory
    from tests._fake_embedder import FakeEmbedder

    fake = FakeEmbedder(dim=8)
    set_embedder_factory(lambda model_id: fake)
    yield fake
    set_embedder_factory(None)
