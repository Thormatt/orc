"""Trace JSON schema versioning and replay-safety checks.

The trace JSON written by `Run.build_trace_payload` is the contract between an
orc run and any consumer that re-reads it later: `orc replay`, `orc audit
export`, the upcoming hosted runtime, and any external auditor tooling. The
version is recorded explicitly so consumers can branch on it instead of
guessing from field presence.

Version history
---------------
v1 — initial format shipped with 0.1.0. Top-level fields:
     `run_id, directive, skill, workspace, corpus_version, started_at,
     ended_at, status, model, inputs, events, retrieval, llm_calls, output,
     error_message`.
v2 — adds `effective_kwargs` (the kwargs the skill was actually invoked with,
     after manifest defaults were merged with caller overrides). Pinned at
     write time, so a manifest change between original and replay can no
     longer silently shift behavior. Additive to v1 readers — older traces
     can still be loaded via the legacy reconstruction path in `replay()`.

Compatibility rules
-------------------
- New traces are always written at `LATEST_TRACE_SCHEMA_VERSION`.
- Consumers accept any version in `SUPPORTED_TRACE_SCHEMA_VERSIONS`.
- Consumers must refuse traces with versions outside the supported set —
  silently treating an unknown version as "close enough" is the kind of bug
  audit-export must never have.
- A missing `schema_version` field is treated as v1 (defensive, for early
  traces written before the field existed).
"""

from __future__ import annotations

from typing import Any

LATEST_TRACE_SCHEMA_VERSION = 2
SUPPORTED_TRACE_SCHEMA_VERSIONS: tuple[int, ...] = (1, 2)


class TraceSchemaError(ValueError):
    """Raised when a trace JSON cannot be safely consumed by the current build.

    Carries the offending version and the supported set so the caller can
    produce an actionable error for an operator or auditor.
    """

    def __init__(self, found: Any, *, context: str) -> None:
        self.found = found
        self.context = context
        super().__init__(
            f"{context} refuses trace with schema_version={found!r}: "
            f"this build of orc supports {SUPPORTED_TRACE_SCHEMA_VERSIONS}. "
            f"Upgrade orc or use a compatible build to consume this trace."
        )


def assert_supported(payload: dict[str, Any], *, context: str = "operation") -> int:
    """Validate `payload['schema_version']` and return it.

    A missing field is treated as v1. An unsupported version raises
    TraceSchemaError so the operator sees a clear failure rather than a
    silent "this trace was replayed" with wrong semantics.
    """
    version = payload.get("schema_version", 1)
    if version not in SUPPORTED_TRACE_SCHEMA_VERSIONS:
        raise TraceSchemaError(version, context=context)
    return version
