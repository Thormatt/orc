"""Executor registry + per-workspace capability allow-list.

Allow-list is **deny-by-default**: an executor must be explicitly enabled for a
workspace in config.toml before it can be proposed or executed there:

    [workspace.research.effects]
    allowed = ["fs.write_file"]
"""

from __future__ import annotations

import tomllib

from orc.effects.base import Executor, ExecutorNotFoundError
from orc.paths import config_path

_REGISTRY: dict[str, Executor] = {}
_loaded = False


def register(executor: Executor) -> None:
    _REGISTRY[executor.id] = executor


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    import orc.effects.builtin  # noqa: F401  (import registers built-in executors)


def get(executor_id: str) -> Executor:
    _ensure_loaded()
    try:
        return _REGISTRY[executor_id]
    except KeyError:
        raise ExecutorNotFoundError(f"Unknown executor: {executor_id!r}") from None


def all_executors() -> list[Executor]:
    _ensure_loaded()
    return list(_REGISTRY.values())


def allowed_for(workspace: str) -> set[str]:
    """Executor ids enabled for a workspace in config.toml. Empty (deny) if unset."""
    path = config_path()
    if not path.exists():
        return set()
    with path.open("rb") as f:
        data = tomllib.load(f)
    try:
        allowed = data["workspace"][workspace]["effects"]["allowed"]
    except (KeyError, TypeError):
        return set()
    return set(allowed)


def is_allowed(workspace: str, executor_id: str) -> bool:
    return executor_id in allowed_for(workspace)
