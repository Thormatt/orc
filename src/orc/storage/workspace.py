"""Workspace lifecycle: create, resolve, list."""

from __future__ import annotations

import os
from dataclasses import dataclass

from orc.core.clock import now_iso
from orc.errors import WorkspaceExistsError, WorkspaceNotFoundError
from orc.paths import (
    workspace_db_path,
    workspace_evidence_dir,
    workspace_root,
    workspace_traces_dir,
    workspaces_root,
)
from orc.storage.db import (
    SCHEMA_VERSION,
    bootstrap_schema,
    ensure_schema,
    open_connection,
    transaction,
)


@dataclass(frozen=True)
class Workspace:
    name: str
    schema_version: int
    created_at: str
    embedding_model: str | None
    corpus_version: int

    @property
    def has_embeddings(self) -> bool:
        return self.embedding_model is not None


def create(name: str, *, embedding_model: str | None = None) -> Workspace:
    if not _is_valid_name(name):
        raise ValueError(
            f"Invalid workspace name: {name!r} (use alphanumerics, '-', '_'; max 64 chars)"
        )

    root = workspace_root(name)
    if root.exists():
        raise WorkspaceExistsError(f"Workspace {name!r} already exists at {root}")

    root.mkdir(parents=True)
    workspace_evidence_dir(name).mkdir()
    workspace_traces_dir(name).mkdir()

    db_path = workspace_db_path(name)
    created_at = now_iso()

    with open_connection(db_path, create_dir=True) as conn:
        bootstrap_schema(conn)
        with transaction(conn):
            conn.execute(
                "INSERT INTO workspace(name, schema_version, created_at, embedding_model, "
                "corpus_version) VALUES (?, ?, ?, ?, 0)",
                (name, SCHEMA_VERSION, created_at, embedding_model),
            )

    return Workspace(
        name=name,
        schema_version=SCHEMA_VERSION,
        created_at=created_at,
        embedding_model=embedding_model,
        corpus_version=0,
    )


def resolve(name: str | None) -> Workspace:
    """Open an existing workspace by name. None falls back to ORC_DEFAULT_WORKSPACE or 'default'."""
    resolved_name = name if name is not None else os.environ.get("ORC_DEFAULT_WORKSPACE", "default")
    # Validate before touching the filesystem: the name may come straight from an
    # untrusted MCP/LLM caller. An invalid name (traversal, separators, etc.) must
    # never build a path, and the error must not echo a resolved path (probe oracle).
    if not _is_valid_name(resolved_name):
        raise WorkspaceNotFoundError(f"Workspace {resolved_name!r} not found")
    db_path = workspace_db_path(resolved_name)
    if not db_path.exists():
        raise WorkspaceNotFoundError(f"Workspace {resolved_name!r} not found")

    with open_connection(db_path) as conn:
        # Additive migrations run on open so a workspace created by an older orc
        # gains new tables (gold_claim/eval_run/tiered_policy) the first time a
        # newer orc touches it. No-op once current.
        ensure_schema(conn)
        row = conn.execute(
            "SELECT name, schema_version, created_at, embedding_model, corpus_version "
            "FROM workspace WHERE name = ?",
            (resolved_name,),
        ).fetchone()
    if row is None:
        raise WorkspaceNotFoundError(
            f"Workspace {resolved_name!r} db exists but has no metadata row"
        )
    return _row_to_workspace(row)


def list_all() -> list[Workspace]:
    root = workspaces_root()
    if not root.exists():
        return []
    out: list[Workspace] = []
    for child in sorted(root.iterdir()):
        if not (child / "orc.db").exists():
            continue
        try:
            out.append(resolve(child.name))
        except WorkspaceNotFoundError:
            continue
    return out


def _row_to_workspace(row: object) -> Workspace:
    return Workspace(
        name=row["name"],
        schema_version=row["schema_version"],
        created_at=row["created_at"],
        embedding_model=row["embedding_model"],
        corpus_version=row["corpus_version"],
    )


def _is_valid_name(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in name)
