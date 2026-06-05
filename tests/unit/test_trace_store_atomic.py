"""Trace writes must be atomic.

A trace JSON is the audit record. A crash mid-write must never leave a truncated
file that breaks `load_trace`/audit export — the write either fully lands or the
previous file is preserved untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orc.paths import trace_json_path
from orc.storage import trace_store

STARTED_AT = "2026-01-02T03:04:05Z"


def test_write_trace_json_leaves_no_temp_file(orc_home: Path) -> None:
    path = trace_store.write_trace_json("ws", "run-1", STARTED_AT, {"ok": True})

    assert json.loads(path.read_text()) == {"ok": True}
    siblings = list(path.parent.iterdir())
    assert siblings == [path], f"unexpected leftover files: {siblings}"


def test_write_trace_json_preserves_prior_on_failure(
    orc_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = trace_store.write_trace_json("ws", "run-1", STARTED_AT, {"version": 1})
    assert json.loads(path.read_text()) == {"version": 1}

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated crash during commit")

    monkeypatch.setattr(trace_store.os, "replace", boom)

    with pytest.raises(OSError):
        trace_store.write_trace_json("ws", "run-1", STARTED_AT, {"version": 2})

    # The original trace must be intact, not truncated or half-written.
    assert json.loads(path.read_text()) == {"version": 1}
    assert path == trace_json_path("ws", "run-1", STARTED_AT)
