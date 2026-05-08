"""Skill protocol and DirectiveSpec contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from orc.runs.runner import Run
from orc.storage.workspace import Workspace


class Skill(Protocol):
    name: str

    def run(self, *, workspace: Workspace, run: Run, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SkillResult:
    """Optional structured wrapper for skill outputs. Skills may also return plain dicts."""

    output: dict[str, Any]


@dataclass(frozen=True)
class DirectiveSpec:
    name: str
    version: str
    description: str
    skills: dict[str, Skill]
    defaults: dict[str, Any] = field(default_factory=dict)
    skill_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)

    def kwargs_for(self, skill_name: str) -> dict[str, Any]:
        """Manifest-supplied default kwargs for a skill. Caller may override."""
        return dict(self.skill_defaults.get(skill_name, {}))
