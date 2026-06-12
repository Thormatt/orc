"""Directive registry tests."""

from __future__ import annotations

import sys

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


def test_get_builtin_directive_after_early_thirdparty_register() -> None:
    """A third-party register() before the first get() must not skip bundled loading.

    The lazy loader used to treat "registry is non-empty" as "bundled directives are
    loaded", so an early external registration short-circuited the research import
    and get("research") raised KeyError. Simulate fresh module state (registry empty,
    loader flag reset, research package not yet imported), register first, then get.
    """
    saved_registry = dict(directives._REGISTRY)
    saved_loaded = getattr(directives, "_loaded", False)
    saved_module = sys.modules.pop("orc.directives.research", None)
    directives._REGISTRY.clear()
    directives._loaded = False
    try:
        directives.register(
            DirectiveSpec(
                name="__early_bird__",
                version="0.0.1",
                description="registered before the first get()",
                skills={},
            )
        )
        spec = directives.get("research")
        assert spec.name == "research"
    finally:
        # Drop the module (re-)imported during the test, then restore the exact
        # pre-test world so identity of specs/modules is preserved for other tests.
        sys.modules.pop("orc.directives.research", None)
        if saved_module is not None:
            sys.modules["orc.directives.research"] = saved_module
        directives._REGISTRY.clear()
        directives._REGISTRY.update(saved_registry)
        directives._loaded = saved_loaded


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
