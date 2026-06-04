"""Executor protocol and effect-plane errors."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from orc.errors import OrcError


class ExecutorNotFoundError(OrcError):
    """No executor is registered under the given id."""


class ExecutorNotAllowedError(OrcError):
    """The executor exists but is not in the workspace's capability allow-list."""


class MissingCredentialError(OrcError):
    """The executor requires a write credential absent from this process's env.

    This is the mechanism that stops the analysis plane from executing effects: it
    simply does not have the token exported, so `execute()` cannot run.
    """


@runtime_checkable
class Executor(Protocol):
    id: str
    version: int
    params_schema: dict[str, Any]
    # Env var the effect plane must hold to run this executor. None = no external
    # credential needed (e.g. a workspace-confined filesystem write).
    required_credential: str | None

    def execute(
        self, *, params: dict[str, Any], credential: str | None, workspace: str
    ) -> dict[str, Any]: ...
