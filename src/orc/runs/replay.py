"""Replay engine.

Frozen (default): re-execute the recorded skill against the corpus_version snapshot
referenced by the original run. Best-effort — chunks deleted since the original run
won't re-appear.

Live: re-execute against the current corpus.

The replay produces a NEW run, with new run_id and trace, that records `_replay_of`
in its inputs so the lineage is preserved.

Kwargs precedence on replay:
  1. `effective_kwargs` from the original trace if present (the snapshot of manifest
     defaults + caller overrides at execution time). This is the load-bearing path —
     a manifest change between original and replay must not silently shift behavior.
  2. Fallback for older traces that predate `effective_kwargs`: merge current manifest
     defaults with the recorded `inputs`, stripping internal `_*` metadata keys.
"""

from __future__ import annotations

import warnings
from typing import Any

from orc import directives
from orc.runs import open_run
from orc.runs.trace_schema import assert_supported
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace


def replay(run_id: str, *, live: bool = False) -> dict[str, Any]:
    trace = load_trace(run_id)
    schema_version = assert_supported(trace, context=f"orc replay {run_id}")

    workspace_name = trace["workspace"]
    directive_name = trace["directive"]
    skill_name = trace["skill"]
    original_inputs: dict[str, Any] = dict(trace.get("inputs") or {})
    original_corpus_version = trace["corpus_version"]
    original_effective_kwargs = trace.get("effective_kwargs")

    ws = ws_module.resolve(workspace_name)
    spec = directives.get(directive_name)
    skill = spec.skills[skill_name]

    skill_kwargs = _resolve_replay_kwargs(
        spec=spec,
        skill_name=skill_name,
        original_effective_kwargs=original_effective_kwargs,
        original_inputs=original_inputs,
    )
    if not live:
        skill_kwargs["corpus_version"] = original_corpus_version

    replay_inputs = {
        **original_inputs,
        "_replay_of": run_id,
        "_replay_mode": "frozen" if not live else "live",
        "_original_corpus_version": original_corpus_version,
        "_kwargs_source": "effective_kwargs" if original_effective_kwargs else "legacy_inputs",
    }

    with open_run(
        ws,
        directive=directive_name,
        skill=skill_name,
        inputs=replay_inputs,
    ) as run:
        run.record_effective_kwargs(skill_kwargs)
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)

    if not live:
        _warn_on_retrieval_method_drift(original_trace=trace, new_retrieval=run.retrieval)

    return {
        "original_run_id": run_id,
        "new_run_id": run.run_id,
        "mode": "frozen" if not live else "live",
        "original_corpus_version": original_corpus_version,
        "current_corpus_version": ws.corpus_version,
        "kwargs_source": "effective_kwargs" if original_effective_kwargs else "legacy_inputs",
        "original_schema_version": schema_version,
        "result": result,
    }


def _warn_on_retrieval_method_drift(
    *,
    original_trace: dict[str, Any],
    new_retrieval: dict[str, Any] | None,
) -> None:
    """Frozen replay promises reproduction; a retrieval method change (e.g.
    hybrid_rrf -> bm25 because embedding deps are absent at replay time) means
    the chunk pool may differ even with corpus_version pinned. Surface it
    rather than letting the drift pass silently."""
    original_method = (original_trace.get("retrieval") or {}).get("method")
    new_method = (new_retrieval or {}).get("method")
    if original_method and new_method and original_method != new_method:
        warnings.warn(
            f"Frozen replay used a different retrieval method than the original "
            f"run: {original_method!r} -> {new_method!r}. Retrieved chunks may "
            "differ; check embedding dependencies and chunk_vec state.",
            RuntimeWarning,
            stacklevel=3,
        )


def _resolve_replay_kwargs(
    *,
    spec: Any,
    skill_name: str,
    original_effective_kwargs: dict[str, Any] | None,
    original_inputs: dict[str, Any],
) -> dict[str, Any]:
    if original_effective_kwargs is not None:
        # Pinned snapshot: use exactly what the original run executed with.
        return dict(original_effective_kwargs)
    # Legacy trace: best-effort reconstruction. Manifest defaults are read from
    # the *current* spec, which can drift — but it's the best we can do.
    kwargs: dict[str, Any] = {**spec.kwargs_for(skill_name)}
    for key, value in original_inputs.items():
        if key == "workspace" or key.startswith("_"):
            continue
        kwargs[key] = value
    return kwargs
