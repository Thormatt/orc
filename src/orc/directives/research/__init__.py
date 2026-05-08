"""The `research` directive — evidence-grounded research and claim verification."""

from __future__ import annotations

from pathlib import Path

import yaml

from orc.directives import register
from orc.directives.base import DirectiveSpec
from orc.directives.research.skills import (
    extract_claims,
    research_topic,
    search_evidence,
    verify_claim,
)

_MANIFEST = yaml.safe_load((Path(__file__).parent / "manifest.yaml").read_text())

register(
    DirectiveSpec(
        name=_MANIFEST["name"],
        version=_MANIFEST["version"],
        description=_MANIFEST["description"],
        defaults=_MANIFEST.get("defaults", {}),
        skill_defaults=_MANIFEST.get("skills", {}) or {},
        skills={
            "search_evidence": search_evidence,
            "verify_claim": verify_claim,
            "research_topic": research_topic,
            "extract_claims": extract_claims,
        },
    )
)
