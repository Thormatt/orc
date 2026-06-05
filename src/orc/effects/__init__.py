"""Effect plane: the executor registry and the Action contract.

Executors are to the effect plane what skills are to the analysis plane — small,
stateless, explicitly registered units with declared I/O contracts. The analysis
plane may *propose* actions (validated against this registry); only a separate
effect-plane process, holding the write credentials, *executes* them.

See docs/design/0001-isolated-write-paths.md.
"""

from __future__ import annotations

from orc.effects.action import Action, ActionValidationError, validate_params
from orc.effects.base import (
    Executor,
    ExecutorNotAllowedError,
    ExecutorNotFoundError,
    MissingCredentialError,
)
from orc.effects.registry import all_executors, allowed_for, get, is_allowed, register
from orc.effects.run import run_action

__all__ = [
    "Action",
    "ActionValidationError",
    "Executor",
    "ExecutorNotAllowedError",
    "ExecutorNotFoundError",
    "MissingCredentialError",
    "all_executors",
    "allowed_for",
    "get",
    "is_allowed",
    "register",
    "run_action",
    "validate_params",
]
