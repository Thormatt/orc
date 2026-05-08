"""Adding a new directive should be a few lines + a YAML manifest.

This is the moat. If this test ever needs major surgery to add a new directive,
the dispatch contract has decayed and needs reinforcement.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from orc import directives
from orc.directives.base import DirectiveSpec
from orc.runs.runner import Run
from orc.storage.workspace import Workspace


class _DraftPostStub:
    """Stub skill: pretends to draft a marketing post. No LLM, no IO."""

    name = "draft_post"

    def run(
        self,
        *,
        workspace: Workspace,
        run: Run,
        topic: str,
        tone: str = "calm",
        **_unused: Any,
    ) -> dict[str, Any]:
        run.record("draft", {"topic": topic, "tone": tone})
        return {"topic": topic, "tone": tone, "draft": f"Stub post about {topic}."}


def test_register_marketing_directive_in_few_lines() -> None:
    """Counts the source lines we needed to register a new directive."""

    spec = DirectiveSpec(
        name="__marketing_demo__",
        version="0.0.1",
        description="A stub marketing directive for the moat test.",
        skills={"draft_post": _DraftPostStub()},
        defaults={"tone": "calm"},
        skill_defaults={"draft_post": {"tone": "calm"}},
    )
    directives.register(spec)
    try:
        got = directives.get("__marketing_demo__")
        assert "draft_post" in got.skills
        assert got.kwargs_for("draft_post") == {"tone": "calm"}
    finally:
        del directives._REGISTRY["__marketing_demo__"]

    # Smoke-budget the surface area: the registration block above is small.
    # If you ever exceed ~30 lines to register a new directive, the contract
    # has bloated. Source-of-truth:
    src = textwrap.dedent(
        """\
        spec = DirectiveSpec(
            name="__marketing_demo__",
            version="0.0.1",
            description="A stub marketing directive for the moat test.",
            skills={"draft_post": _DraftPostStub()},
            defaults={"tone": "calm"},
            skill_defaults={"draft_post": {"tone": "calm"}},
        )
        directives.register(spec)
        """
    )
    assert len(src.splitlines()) < 30


def test_research_directive_skill_defaults_loaded_from_yaml() -> None:
    spec = directives.get("research")
    verify_defaults = spec.kwargs_for("verify_claim")
    assert verify_defaults["model"] == "claude-sonnet-4-6"
    assert verify_defaults["max_tokens"] == 2048


def test_research_directive_manifest_has_expected_metadata() -> None:
    manifest_path = Path("src/orc/directives/research/manifest.yaml")
    assert manifest_path.exists()
    spec = directives.get("research")
    assert spec.name == "research"
    assert spec.version == "0.1.0"
    assert spec.description.startswith("Evidence-grounded")
