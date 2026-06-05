"""Guarded single-action execution — the only place an effect actually fires.

Every effect goes through here, on both the manual (`orc execute`) and (future)
`orc worker` paths. It enforces, in order: the per-workspace allow-list, the
executor's params schema, and the presence of the required write credential.
"""

from __future__ import annotations

import os
from typing import Any

from orc.effects.action import Action, validate_params
from orc.effects.base import ExecutorNotAllowedError, MissingCredentialError
from orc.effects.registry import get, is_allowed


def run_action(workspace: str, action: Action) -> dict[str, Any]:
    if not is_allowed(workspace, action.executor):
        raise ExecutorNotAllowedError(
            f"Executor {action.executor!r} is not in the allow-list for "
            f"workspace {workspace!r}"
        )
    executor = get(action.executor)
    validate_params(action.params, executor.params_schema)

    credential: str | None = None
    if executor.required_credential:
        credential = os.environ.get(executor.required_credential)
        if not credential:
            raise MissingCredentialError(
                f"Executor {action.executor!r} requires credential "
                f"{executor.required_credential!r}, which is absent from this "
                f"process's environment — the analysis plane cannot execute it."
            )

    return executor.execute(
        params=action.params, credential=credential, workspace=workspace
    )
