"""Run context manager tests."""

from __future__ import annotations

import json
from pathlib import Path

from orc.paths import workspace_db_path, workspace_traces_dir
from orc.runs import open_run
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection
from orc.storage.trace_store import list_runs


def test_run_writes_trace_json_and_run_row(orc_home: Path) -> None:
    ws = ws_module.create("demo")
    with open_run(
        ws,
        directive="research",
        skill="search_evidence",
        inputs={"query": "anything", "k": 5},
    ) as run:
        run.record("note", {"hello": "world"})
        run.close(output={"chunks": []})

    # 1. Run row in db
    with open_connection(workspace_db_path("demo")) as conn:
        row = conn.execute(
            "SELECT directive, skill, status, started_at, ended_at FROM run WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    assert row["status"] == "ok"
    assert row["directive"] == "research"
    assert row["skill"] == "search_evidence"
    assert row["ended_at"] is not None

    # 2. Trace JSON on disk under year/month
    traces = list(workspace_traces_dir("demo").rglob(f"{run.run_id}.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text())
    from orc.runs.trace_schema import LATEST_TRACE_SCHEMA_VERSION

    assert trace["schema_version"] == LATEST_TRACE_SCHEMA_VERSION
    assert trace["run_id"] == run.run_id
    assert trace["status"] == "ok"
    assert trace["events"][0]["key"] == "note"


def test_run_records_error_on_exception(orc_home: Path) -> None:
    ws = ws_module.create("demo")
    error_caught = False
    try:
        with open_run(ws, directive="research", skill="search_evidence", inputs={}) as run:
            run_id = run.run_id
            raise RuntimeError("boom")
    except RuntimeError:
        error_caught = True

    assert error_caught
    with open_connection(workspace_db_path("demo")) as conn:
        row = conn.execute(
            "SELECT status, error_message FROM run WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert row["status"] == "error"
    assert "boom" in row["error_message"]


def test_list_runs_orders_newest_first(orc_home: Path) -> None:
    ws = ws_module.create("demo")
    for i in range(3):
        with open_run(ws, directive="research", skill="search_evidence", inputs={"i": i}) as run:
            run.close(output={})
    rows = list_runs("demo", limit=10)
    assert len(rows) == 3
    timestamps = [r["started_at"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)
