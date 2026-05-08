"""Default Anthropic model selection per skill, with override precedence:
CLI flag > env var > directive manifest > module default.
"""

from __future__ import annotations

import os

DEFAULT_VERIFY_MODEL = "claude-sonnet-4-6"
DEFAULT_RESEARCH_MODEL = "claude-sonnet-4-6"
DEFAULT_EXTRACT_MODEL = "claude-haiku-4-5"


def resolve_verify_model(override: str | None) -> str:
    return override or os.environ.get("ORC_VERIFY_MODEL") or DEFAULT_VERIFY_MODEL


def resolve_research_model(override: str | None) -> str:
    return override or os.environ.get("ORC_RESEARCH_MODEL") or DEFAULT_RESEARCH_MODEL


def resolve_extract_model(override: str | None) -> str:
    return override or os.environ.get("ORC_EXTRACT_MODEL") or DEFAULT_EXTRACT_MODEL
