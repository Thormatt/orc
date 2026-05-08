"""Replay engine.

Frozen (default): re-execute the recorded skill against the corpus_version snapshot
referenced by the original run. Best-effort — chunks deleted since the original run
won't re-appear.

Live: re-execute against the current corpus.

The replay produces a NEW run, with new run_id and trace, that records `_replay_of`
in its inputs so the lineage is preserved.
"""

from __future__ import annotations

from typing import Any

from orc import directives
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace


def replay(run_id: str, *, live: bool = False) -> dict[str, Any]:
    trace = load_trace(run_id)

    workspace_name = trace["workspace"]
    directive_name = trace["directive"]
    skill_name = trace["skill"]
    original_inputs: dict[str, Any] = dict(trace["inputs"])
    original_corpus_version = trace["corpus_version"]

    ws = ws_module.resolve(workspace_name)
    spec = directives.get(directive_name)
    skill = spec.skills[skill_name]

    skill_kwargs: dict[str, Any] = {**spec.kwargs_for(skill_name)}
    for key, value in original_inputs.items():
        if key == "workspace":
            continue
        skill_kwargs[key] = value
    if not live:
        skill_kwargs["corpus_version"] = original_corpus_version

    replay_inputs = {
        **original_inputs,
        "_replay_of": run_id,
        "_replay_mode": "frozen" if not live else "live",
        "_original_corpus_version": original_corpus_version,
    }

    with open_run(
        ws,
        directive=directive_name,
        skill=skill_name,
        inputs=replay_inputs,
    ) as run:
        result = skill.run(workspace=ws, run=run, **skill_kwargs)
        run.close(output=result)

    return {
        "original_run_id": run_id,
        "new_run_id": run.run_id,
        "mode": "frozen" if not live else "live",
        "original_corpus_version": original_corpus_version,
        "current_corpus_version": ws.corpus_version,
        "result": result,
    }
