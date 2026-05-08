"""Directive registry.

A directive is a versioned config + module that bundles skills, defaults, and a manifest.
The CLI and MCP surfaces look up skills via this registry, never by hard-coded import.

Adding a new directive (`marketing`, `code-review`, `db-doctor`, …) means dropping a
new package under `orc.directives.<name>/` whose `__init__.py` calls `register(...)`.
"""

from __future__ import annotations

from orc.directives.base import DirectiveSpec, Skill, SkillResult

_REGISTRY: dict[str, DirectiveSpec] = {}


def register(spec: DirectiveSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"Directive already registered: {spec.name!r}")
    _REGISTRY[spec.name] = spec


def get(name: str) -> DirectiveSpec:
    _ensure_loaded()
    if name not in _REGISTRY:
        raise KeyError(f"Unknown directive: {name!r}. Known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def list_directives() -> list[DirectiveSpec]:
    _ensure_loaded()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def _ensure_loaded() -> None:
    """Force-import bundled directive packages so they self-register on first use."""
    if _REGISTRY:
        return
    import orc.directives.research  # noqa: F401  (registers as side effect)


__all__ = ["DirectiveSpec", "Skill", "SkillResult", "register", "get", "list_directives"]
