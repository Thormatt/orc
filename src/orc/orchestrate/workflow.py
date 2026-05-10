"""In-process orchestrator-worker pattern.

A `Workflow` lets one skill (the parent / lead planner) spawn other skills (workers)
with explicit input contracts and budgets. Each step opens its own Run, so each step's
trace is independently replayable. The parent's trace records every step as an event
so the workflow shape is reconstructable.

Deliberately bounded:
- Sequential `run_step` and bounded-parallel `fanout`. No free-form agent chat.
- No persistent agent identities — each step is a fresh skill invocation.
- No emergent coordination. Only explicit DAG-shaped composition.

What's not in v1 (architecture supports adding):
- Cross-process orchestration
- Streaming step outputs
- Auto-retry with backoff (caller decides)
- Idempotency tokens (relevant when skills take external actions)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from orc import directives
from orc.runs import open_run
from orc.runs.runner import Run
from orc.storage.workspace import Workspace


@dataclass(frozen=True)
class Step:
    """A unit of work the orchestrator hands to a worker skill."""

    skill: str
    directive: str = "research"
    inputs: dict[str, Any] = field(default_factory=dict)
    budget_tokens: int | None = None  # advisory: skills may pass to LLM max_tokens
    description: str | None = None    # human-readable label for traces / debugging

    def with_inputs(self, **extra: Any) -> Step:
        return Step(
            skill=self.skill,
            directive=self.directive,
            inputs={**self.inputs, **extra},
            budget_tokens=self.budget_tokens,
            description=self.description,
        )


@dataclass(frozen=True)
class StepResult:
    step: Step
    run_id: str
    status: str            # "ok" | "error"
    output: dict[str, Any]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class WorkflowError(Exception):
    """Raised when a step fails and `fail_fast=True`."""


class Workflow:
    """Orchestrate skill invocations under a parent Run.

    Usage from inside a parent skill:

        def synthesize(workspace, run, *, topic, **_):
            wf = Workflow(parent_run=run, workspace=workspace)
            lens_outputs = wf.fanout([
                Step("lens_structure",   inputs={"topic": topic}),
                Step("lens_performance", inputs={"topic": topic}),
                ...
            ])
            consolidated = wf.run_step(
                Step("consolidate", inputs={"lens_outputs": [r.output for r in lens_outputs]})
            )
            return consolidated.output
    """

    def __init__(self, *, parent_run: Run, workspace: Workspace) -> None:
        self.parent_run = parent_run
        self.workspace = workspace
        self.results: list[StepResult] = []

    def run_step(self, step: Step, *, fail_fast: bool = False) -> StepResult:
        result = self._execute(step)
        self.results.append(result)
        self.parent_run.record(
            "workflow_step",
            {
                "step_index": len(self.results) - 1,
                "skill": step.skill,
                "directive": step.directive,
                "child_run_id": result.run_id,
                "status": result.status,
                "error": result.error,
                "description": step.description,
            },
        )
        if not result.ok and fail_fast:
            raise WorkflowError(f"Step '{step.skill}' failed: {result.error}")
        return result

    def fanout(
        self,
        steps: list[Step],
        *,
        max_workers: int = 4,
        fail_fast: bool = False,
    ) -> list[StepResult]:
        """Execute steps in parallel. Returns results in input order.

        Each step opens its own SQLite connection; WAL mode handles concurrent reads
        and serializes writes via BEGIN IMMEDIATE.
        """
        if not steps:
            return []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(self._execute, steps))
        for i, (step, result) in enumerate(zip(steps, results, strict=True)):
            self.results.append(result)
            self.parent_run.record(
                "workflow_step",
                {
                    "step_index": len(self.results) - len(steps) + i,
                    "skill": step.skill,
                    "directive": step.directive,
                    "child_run_id": result.run_id,
                    "status": result.status,
                    "error": result.error,
                    "description": step.description,
                    "fanout": True,
                },
            )
        if fail_fast:
            failed = [r for r in results if not r.ok]
            if failed:
                raise WorkflowError(
                    f"{len(failed)} step(s) failed in fanout: "
                    + ", ".join(f"{f.step.skill}: {f.error}" for f in failed)
                )
        return results

    def _execute(self, step: Step) -> StepResult:
        spec = directives.get(step.directive)
        if step.skill not in spec.skills:
            return StepResult(
                step=step,
                run_id="",
                status="error",
                output={},
                error=f"Unknown skill {step.skill!r} in directive {step.directive!r}",
            )
        skill = spec.skills[step.skill]
        skill_kwargs: dict[str, Any] = {**spec.kwargs_for(step.skill), **step.inputs}
        if step.budget_tokens is not None:
            skill_kwargs.setdefault("max_tokens", step.budget_tokens)

        recorded_inputs: dict[str, Any] = {
            **step.inputs,
            "_parent_run": self.parent_run.run_id,
            "_step_index": len(self.results),
        }
        if step.description:
            recorded_inputs["_description"] = step.description

        try:
            with open_run(
                self.workspace,
                directive=step.directive,
                skill=step.skill,
                inputs=recorded_inputs,
            ) as sub_run:
                output = skill.run(workspace=self.workspace, run=sub_run, **skill_kwargs)
                sub_run.close(output=output)
                return StepResult(
                    step=step, run_id=sub_run.run_id, status="ok", output=output
                )
        except Exception as exc:
            return StepResult(
                step=step,
                run_id="",
                status="error",
                output={},
                error=f"{type(exc).__name__}: {exc}",
            )
