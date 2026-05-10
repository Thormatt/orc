"""Workflow / orchestrator-worker primitive tests."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from orc import directives
from orc.directives.base import DirectiveSpec
from orc.orchestrate import Step, Workflow
from orc.orchestrate.workflow import WorkflowError
from orc.runs import open_run
from orc.runs.runner import Run
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace
from orc.storage.workspace import Workspace

# ---------- Stub skills + directive used only by these tests ----------


class _EchoSkill:
    name = "echo"

    def run(self, *, workspace: Workspace, run: Run, value: Any = None, **_: Any) -> dict[str, Any]:
        run.record("echoed", {"value": value})
        return {"echoed": value}


class _AddSkill:
    name = "add"

    def run(self, *, workspace: Workspace, run: Run, a: int = 0, b: int = 0, **_: Any) -> dict[str, Any]:
        return {"sum": a + b}


class _SleepSkill:
    """Used to verify fanout actually parallelizes."""

    name = "sleep"

    def run(self, *, workspace: Workspace, run: Run, ms: int = 50, **_: Any) -> dict[str, Any]:
        time.sleep(ms / 1000.0)
        return {"slept_ms": ms}


class _BoomSkill:
    name = "boom"

    def run(self, *, workspace: Workspace, run: Run, **_: Any) -> dict[str, Any]:
        raise RuntimeError("kaboom")


@pytest.fixture
def test_directive() -> Iterator[DirectiveSpec]:
    spec = DirectiveSpec(
        name="__orchestrate_test__",
        version="0.0.1",
        description="Stub directive for orchestration tests",
        skills={
            "echo": _EchoSkill(),
            "add": _AddSkill(),
            "sleep": _SleepSkill(),
            "boom": _BoomSkill(),
        },
    )
    directives.register(spec)
    try:
        yield spec
    finally:
        del directives._REGISTRY["__orchestrate_test__"]


@pytest.fixture
def parent(orc_home: Path) -> Iterator[tuple[Workspace, Run]]:
    ws = ws_module.create("demo")
    with open_run(
        ws,
        directive="__orchestrate_test__",
        skill="parent",  # not registered; we never call it
        inputs={"role": "orchestrator"},
    ) as parent_run:
        yield ws, parent_run
        parent_run.close(output={"steps": "done"})


# ---------- Tests ----------


def test_run_step_opens_child_run_and_returns_output(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    result = wf.run_step(
        Step(skill="add", directive="__orchestrate_test__", inputs={"a": 2, "b": 3})
    )
    assert result.ok
    assert result.output == {"sum": 5}
    assert result.run_id  # non-empty
    assert result.run_id != parent_run.run_id


def test_child_trace_records_parent_lineage(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    result = wf.run_step(
        Step(
            skill="echo",
            directive="__orchestrate_test__",
            inputs={"value": "hi"},
            description="say hi",
        )
    )
    child_trace = load_trace(result.run_id)
    assert child_trace["inputs"]["_parent_run"] == parent_run.run_id
    assert child_trace["inputs"]["_step_index"] == 0
    assert child_trace["inputs"]["_description"] == "say hi"


def test_parent_run_records_step_event(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    wf.run_step(Step(skill="add", directive="__orchestrate_test__", inputs={"a": 1, "b": 2}))
    workflow_events = [e for e in parent_run.events if e["key"] == "workflow_step"]
    assert len(workflow_events) == 1
    ev = workflow_events[0]["value"]
    assert ev["skill"] == "add"
    assert ev["status"] == "ok"
    assert ev["child_run_id"]


def test_fanout_runs_in_parallel(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    steps = [Step(skill="sleep", directive="__orchestrate_test__", inputs={"ms": 100}) for _ in range(4)]
    start = time.monotonic()
    results = wf.fanout(steps, max_workers=4)
    elapsed = time.monotonic() - start
    # Sequential would be ~400ms; parallel should be ~100ms + scheduling overhead.
    # Loose bound to avoid flakiness on busy CI.
    assert elapsed < 0.30, f"fanout did not parallelize: elapsed={elapsed:.2f}s"
    assert all(r.ok for r in results)
    assert [r.output["slept_ms"] for r in results] == [100, 100, 100, 100]


def test_fanout_returns_results_in_step_order(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    steps = [
        Step(skill="echo", directive="__orchestrate_test__", inputs={"value": "first"}),
        Step(skill="echo", directive="__orchestrate_test__", inputs={"value": "second"}),
        Step(skill="echo", directive="__orchestrate_test__", inputs={"value": "third"}),
    ]
    results = wf.fanout(steps, max_workers=3)
    assert [r.output["echoed"] for r in results] == ["first", "second", "third"]


def test_failure_recorded_as_error_result(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    result = wf.run_step(Step(skill="boom", directive="__orchestrate_test__"))
    assert not result.ok
    assert result.status == "error"
    assert "kaboom" in (result.error or "")
    # parent records the failure too
    ev = [e for e in parent_run.events if e["key"] == "workflow_step"][-1]["value"]
    assert ev["status"] == "error"


def test_fail_fast_raises_on_step_error(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    with pytest.raises(WorkflowError):
        wf.run_step(Step(skill="boom", directive="__orchestrate_test__"), fail_fast=True)


def test_fail_fast_on_fanout(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    steps = [
        Step(skill="echo", directive="__orchestrate_test__", inputs={"value": "ok"}),
        Step(skill="boom", directive="__orchestrate_test__"),
    ]
    with pytest.raises(WorkflowError):
        wf.fanout(steps, fail_fast=True)


def test_unknown_skill_returns_error_result(test_directive, parent) -> None:
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    result = wf.run_step(Step(skill="not_a_skill", directive="__orchestrate_test__"))
    assert not result.ok
    assert "Unknown skill" in (result.error or "")


def test_step_with_inputs_helper(test_directive) -> None:
    base = Step(skill="add", directive="__orchestrate_test__", inputs={"a": 1})
    extended = base.with_inputs(b=2)
    assert extended.inputs == {"a": 1, "b": 2}
    # base is unchanged
    assert base.inputs == {"a": 1}


def test_child_run_is_independently_replayable(test_directive, parent) -> None:
    """The whole point of recording each step as a Run: trace files exist for each."""
    ws, parent_run = parent
    wf = Workflow(parent_run=parent_run, workspace=ws)
    result = wf.run_step(
        Step(skill="add", directive="__orchestrate_test__", inputs={"a": 7, "b": 8})
    )
    trace = load_trace(result.run_id)
    assert trace["status"] == "ok"
    assert trace["output"] == {"sum": 15}
    assert trace["skill"] == "add"
    assert trace["directive"] == "__orchestrate_test__"


def test_budget_tokens_passed_as_max_tokens(test_directive, parent) -> None:
    """budget_tokens on a Step becomes max_tokens on the skill kwargs (advisory).

    Skills can ignore it; the test just verifies the kwarg lands.
    """
    ws, parent_run = parent

    class _CaptureKwargs:
        name = "capture"
        captured: dict[str, Any] | None = None

        def run(self, *, workspace, run, **kwargs):
            type(self).captured = dict(kwargs)
            return {"k": list(kwargs.keys())}

    spec = DirectiveSpec(
        name="__orchestrate_capture__",
        version="0.0.1",
        description="capture",
        skills={"capture": _CaptureKwargs()},
    )
    directives.register(spec)
    try:
        wf = Workflow(parent_run=parent_run, workspace=ws)
        wf.run_step(
            Step(
                skill="capture",
                directive="__orchestrate_capture__",
                inputs={},
                budget_tokens=1234,
            )
        )
        assert _CaptureKwargs.captured is not None
        assert _CaptureKwargs.captured.get("max_tokens") == 1234
    finally:
        del directives._REGISTRY["__orchestrate_capture__"]
