"""Global configuration. Read from ~/.orc/config.toml when present, env vars override."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass

from orc.paths import config_path


@dataclass(frozen=True)
class Config:
    default_workspace: str = "default"
    default_verify_model: str = "claude-sonnet-4-6"
    default_research_model: str = "claude-sonnet-4-6"
    default_extract_model: str = "claude-haiku-4-5"
    default_retrieval_k: int = 10
    default_retrieval_pool: int = 50

    @classmethod
    def load(cls) -> Config:
        path = config_path()
        data: dict[str, object] = {}
        if path.exists():
            with path.open("rb") as f:
                data = tomllib.load(f)

        return cls(
            default_workspace=os.environ.get(
                "ORC_DEFAULT_WORKSPACE",
                str(data.get("default_workspace", "default")),
            ),
            default_verify_model=os.environ.get(
                "ORC_VERIFY_MODEL",
                str(data.get("verify_model", "claude-sonnet-4-6")),
            ),
            default_research_model=os.environ.get(
                "ORC_RESEARCH_MODEL",
                str(data.get("research_model", "claude-sonnet-4-6")),
            ),
            default_extract_model=os.environ.get(
                "ORC_EXTRACT_MODEL",
                str(data.get("extract_model", "claude-haiku-4-5")),
            ),
            default_retrieval_k=int(data.get("retrieval_k", 10)),
            default_retrieval_pool=int(data.get("retrieval_pool", 50)),
        )
