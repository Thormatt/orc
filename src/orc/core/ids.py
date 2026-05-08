"""Sortable, opaque identifiers for runs, evidence, and chunks."""

from __future__ import annotations

from ulid import ULID


def new_id() -> str:
    return str(ULID())


def new_run_id() -> str:
    return new_id()


def new_evidence_id() -> str:
    return new_id()


def new_chunk_id() -> str:
    return new_id()
