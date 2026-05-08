"""Directive registry tests."""

from __future__ import annotations

import pytest

from orc import directives
from orc.directives.base import DirectiveSpec


def test_research_directive_is_registered() -> None:
    spec = directives.get("research")
    assert spec.name == "research"
    assert spec.version == "0.1.0"
    assert "search_evidence" in spec.skills


def test_unknown_directive_errors() -> None:
    with pytest.raises(KeyError):
        directives.get("does-not-exist")


def test_register_rejects_duplicate() -> None:
    spec = DirectiveSpec(
        name="__test_dup__",
        version="0.0.1",
        description="test",
        skills={},
    )
    directives.register(spec)
    with pytest.raises(ValueError):
        directives.register(spec)
    # Cleanup so other tests aren't polluted
    del directives._REGISTRY["__test_dup__"]


def test_list_directives_includes_research() -> None:
    names = {d.name for d in directives.list_directives()}
    assert "research" in names


def test_adding_a_directive_is_a_few_lines() -> None:
    """Smoke test for the moat: registering a new directive is trivial."""
    spec = DirectiveSpec(
        name="__sample_marketing__",
        version="0.0.1",
        description="hypothetical marketing directive",
        skills={},  # would map skill names to Skill objects in a real impl
        defaults={"tone": "calm"},
    )
    directives.register(spec)
    try:
        got = directives.get("__sample_marketing__")
        assert got.defaults == {"tone": "calm"}
    finally:
        del directives._REGISTRY["__sample_marketing__"]
