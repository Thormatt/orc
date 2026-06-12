# orc eval + tiered verification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make orc's verification gate measurable on a user-owned labeled gold set, then use that measurement to calibrate a cheap→expensive tiered verification router.

**Architecture:** A new `src/orc/metrics/` library (extracted from the benchmark) computes confusion/scores/calibration. A per-workspace `gold_claim` table (schema v2) stores human-confirmed labels. `orc eval run` scores the gate against the gold set; `orc eval calibrate` derives a tiered escalation threshold; a `tiered_verify` meta-mode escalates cheap→expensive using that threshold. Eval runs are themselves traced and replayable.

**Tech Stack:** Python 3.11+, click CLI, SQLite (per-workspace `orc.db`), the existing fake-LLM test harness (`tests/_fake_llm.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-orc-eval-tiered-verification-design.md`

**Conventions (apply to every task):**
- TDD: write the failing test, run it, confirm it fails for the right reason, implement minimally, confirm green, commit.
- Run the full suite (`uv run pytest -q`) and `uv run ruff check src tests` before each commit; both must be clean.
- Frozen dataclasses, keyword-only kwargs, docstrings explaining WHY. Commit subjects ≤50 chars, body explains why, end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Branch: `feat/eval-tiered-verification` (already created).

---

## Stage 1 — Metrics library

Extract the benchmark's private scoring into an importable package and add calibration. Verdicts here use the 4-label vocabulary directly (`supported`/`contradicted`/`not_found`/`partial`); correctness is exact match of predicted vs expected label.

### Task 1.1: Confusion + scores

**Files:**
- Create: `src/orc/metrics/__init__.py`
- Create: `src/orc/metrics/scoring.py`
- Test: `tests/unit/test_metrics_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_metrics_scoring.py
from orc.metrics.scoring import LabeledResult, confusion, scores


def test_confusion_counts_exact_label_matches() -> None:
    results = [
        LabeledResult(predicted="supported", expected="supported"),
        LabeledResult(predicted="supported", expected="not_found"),
        LabeledResult(predicted="not_found", expected="not_found"),
        LabeledResult(predicted="not_found", expected="supported"),
        LabeledResult(predicted=None, expected="supported"),  # errored, skipped
    ]
    cm = confusion(results, positive="supported")
    assert cm == {"tp": 1, "fp": 1, "tn": 1, "fn": 1}


def test_scores_precision_recall_f1_accuracy() -> None:
    s = scores({"tp": 3, "fp": 1, "tn": 4, "fn": 2})
    assert s["accuracy"] == 0.7
    assert s["precision"] == 0.75
    assert round(s["recall"], 4) == 0.6
    assert round(s["f1"], 4) == 0.6667


def test_scores_empty_is_zero() -> None:
    assert scores({"tp": 0, "fp": 0, "tn": 0, "fn": 0})["f1"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_metrics_scoring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'orc.metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/orc/metrics/__init__.py
"""Scoring + calibration metrics shared by benchmarks and `orc eval`."""
```

```python
# src/orc/metrics/scoring.py
"""Confusion matrix and precision/recall/F1 over exact-label predictions.

Positive class is caller-chosen (e.g. "supported"); everything else is the
negative class. Predictions of None (the claim errored) are skipped, not
counted as wrong — an eval distinguishes "judged incorrectly" from "could not
judge"."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabeledResult:
    predicted: str | None
    expected: str


def confusion(results: list[LabeledResult], *, positive: str) -> dict[str, int]:
    tp = fp = tn = fn = 0
    for r in results:
        if r.predicted is None:
            continue
        pred_pos = r.predicted == positive
        exp_pos = r.expected == positive
        if pred_pos and exp_pos:
            tp += 1
        elif pred_pos and not exp_pos:
            fp += 1
        elif not pred_pos and not exp_pos:
            tn += 1
        else:
            fn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def scores(cm: dict[str, int]) -> dict[str, float]:
    tp, fp, tn, fn = cm["tp"], cm["fp"], cm["tn"], cm["fn"]
    n = tp + fp + tn + fn
    if n == 0:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"accuracy": (tp + tn) / n, "precision": precision, "recall": recall, "f1": f1}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_metrics_scoring.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/orc/metrics tests/unit/test_metrics_scoring.py
git commit -m "feat(metrics): confusion + scores library"
```

### Task 1.2: Calibration (reliability bins + ECE)

**Files:**
- Create: `src/orc/metrics/calibration.py`
- Test: `tests/unit/test_metrics_calibration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_metrics_calibration.py
from orc.metrics.calibration import ConfidenceResult, expected_calibration_error, reliability_bins


def test_reliability_bins_group_by_confidence_decile() -> None:
    # Two claims at ~0.95 (one right), two at ~0.55 (both right).
    results = [
        ConfidenceResult(confidence=0.95, correct=True),
        ConfidenceResult(confidence=0.92, correct=False),
        ConfidenceResult(confidence=0.55, correct=True),
        ConfidenceResult(confidence=0.51, correct=True),
    ]
    bins = reliability_bins(results, n_bins=10)
    top = next(b for b in bins if b.lo <= 0.95 < b.hi or b.hi == 1.0 and b.lo <= 0.95)
    assert top.count == 2
    assert top.accuracy == 0.5
    assert round(top.mean_confidence, 3) == 0.935


def test_ece_is_weighted_gap_between_confidence_and_accuracy() -> None:
    # Perfectly calibrated: confidence == accuracy in every bin -> ECE 0.
    perfect = [ConfidenceResult(confidence=1.0, correct=True) for _ in range(4)]
    assert expected_calibration_error(reliability_bins(perfect, n_bins=10)) == 0.0
    # Overconfident: conf 1.0 but half wrong -> ECE 0.5.
    over = (
        [ConfidenceResult(confidence=1.0, correct=True) for _ in range(2)]
        + [ConfidenceResult(confidence=1.0, correct=False) for _ in range(2)]
    )
    assert expected_calibration_error(reliability_bins(over, n_bins=10)) == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_metrics_calibration.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'orc.metrics.calibration'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/orc/metrics/calibration.py
"""Confidence calibration: do the gate's confidence scores mean what they say?

A well-calibrated judge that reports 0.9 confidence is right ~90% of the time.
reliability_bins groups predictions by confidence and reports actual accuracy
per bin; ECE is the count-weighted average gap between stated confidence and
realized accuracy. This is the signal `orc eval calibrate` uses to choose a
tier-1 escalation threshold."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    correct: bool


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    count: int
    mean_confidence: float
    accuracy: float


def reliability_bins(results: list[ConfidenceResult], *, n_bins: int = 10) -> list[Bin]:
    width = 1.0 / n_bins
    out: list[Bin] = []
    for i in range(n_bins):
        lo = i * width
        hi = 1.0 if i == n_bins - 1 else (i + 1) * width
        # Top bin is closed on the right so confidence==1.0 lands somewhere.
        members = [
            r for r in results
            if r.confidence >= lo and (r.confidence < hi or (hi == 1.0 and r.confidence <= hi))
        ]
        if not members:
            continue
        count = len(members)
        out.append(
            Bin(
                lo=lo,
                hi=hi,
                count=count,
                mean_confidence=sum(r.confidence for r in members) / count,
                accuracy=sum(1 for r in members if r.correct) / count,
            )
        )
    return out


def expected_calibration_error(bins: list[Bin]) -> float:
    total = sum(b.count for b in bins)
    if total == 0:
        return 0.0
    return sum(b.count * abs(b.mean_confidence - b.accuracy) for b in bins) / total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_metrics_calibration.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/orc/metrics/calibration.py tests/unit/test_metrics_calibration.py
git commit -m "feat(metrics): confidence calibration (reliability bins + ECE)"
```

### Task 1.3: Rewire the benchmark to the shared library

**Files:**
- Modify: `benchmarks/faithfulness/run.py` (replace private `_confusion`/`_scores` with imports from `orc.metrics.scoring`; adapt the PASS/FAIL binary attrs by constructing `LabeledResult(predicted=r.orc_binary, expected=r.ground_truth)` with `positive="PASS"`).

- [ ] **Step 1: Confirm the benchmark tests exist and pass on main**

Run: `uv run pytest tests/ -q -k benchmark` (if none, the benchmark has no unit tests — then this task only needs the import swap + a manual `python -c` smoke).
Expected: baseline recorded.

- [ ] **Step 2: Swap the implementation, keep `_confusion`/`_scores` as thin shims**

```python
# benchmarks/faithfulness/run.py — replace the bodies, keep the names so the
# rest of the file is untouched:
from orc.metrics.scoring import LabeledResult, confusion as _confusion_lib, scores as _scores

def _confusion(results, binary_attr):
    labeled = [
        LabeledResult(predicted=getattr(r, binary_attr), expected=r.ground_truth)
        for r in results
    ]
    return _confusion_lib(labeled, positive="PASS")
```

Keep `_scores` pointing at the library (it already returns accuracy/precision/recall/f1 — rename keys in the benchmark's own report assembly if it reads `precision_pass`; grep `precision_pass` in `run.py` and update those readers to `precision`).

- [ ] **Step 3: Smoke the scoring path**

Run: `uv run python -c "from benchmarks.faithfulness.run import _confusion, _scores; print(_scores(_confusion([], 'orc_binary')))"`
Expected: `{'accuracy': 0.0, 'precision': 0.0, 'recall': 0.0, 'f1': 0.0}`

- [ ] **Step 4: Full suite**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/faithfulness/run.py
git commit -m "refactor(benchmarks): use orc.metrics scoring library"
```

---

## Stage 2 — Gold store (schema v2 + CLI)

### Task 2.1: schema v2 + gold_claim/eval_run/tiered_policy tables + migration

**Files:**
- Modify: `src/orc/storage/schema.sql` (append three tables; bump header to v2)
- Modify: `src/orc/storage/db.py` (`SCHEMA_VERSION = 2`; add `ensure_schema(conn)` that re-runs the idempotent `IF NOT EXISTS` script + re-stamps when stored version < 2)
- Modify: `src/orc/storage/workspace.py` (`resolve()` calls `ensure_schema` so existing v1 workspaces gain the tables on open)
- Test: `tests/unit/test_schema_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_schema_migration.py
from orc.storage import db
from orc.storage import workspace as ws_module


def test_existing_v1_workspace_gains_gold_tables_on_resolve(orc_home, monkeypatch) -> None:
    # Create at v1 by forcing the old version, then resolve under v2 code.
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
    ws_module.create("legacy")
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    ws_module.resolve("legacy")  # must migrate
    from orc.paths import workspace_db_path
    with db.open_connection(workspace_db_path("legacy")) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"gold_claim", "eval_run", "tiered_policy"} <= names
        ver = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()["value"]
        assert ver == "2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_schema_migration.py -q`
Expected: FAIL — `gold_claim` not in table set.

- [ ] **Step 3: Implement**

Append to `src/orc/storage/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS gold_claim (
    gold_id            TEXT PRIMARY KEY,
    workspace          TEXT NOT NULL,
    claim              TEXT NOT NULL,
    expected_label     TEXT NOT NULL,
    corpus_version     INTEGER NOT NULL,
    relevant_chunk_ids TEXT,
    source             TEXT NOT NULL,
    source_run_id      TEXT,
    note               TEXT,
    added_at           TEXT NOT NULL,
    added_by           TEXT
);
CREATE INDEX IF NOT EXISTS idx_gold_claim_workspace ON gold_claim(workspace);

CREATE TABLE IF NOT EXISTS eval_run (
    eval_id      TEXT PRIMARY KEY,
    workspace    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tiered_policy (
    workspace               TEXT PRIMARY KEY,
    tier1_model             TEXT NOT NULL,
    tier2_model             TEXT NOT NULL,
    top_judge_model         TEXT,
    escalation_threshold    REAL NOT NULL,
    target                  REAL NOT NULL,
    calibrated_at           TEXT NOT NULL,
    calibrated_against_eval_id TEXT,
    n_gold                  INTEGER NOT NULL
);
```

In `src/orc/storage/db.py`: set `SCHEMA_VERSION = 2`; add

```python
def ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring a connection's schema up to SCHEMA_VERSION. All tables use
    CREATE TABLE IF NOT EXISTS, so re-running the script is the migration for
    additive v1->v2 (gold_claim/eval_run/tiered_policy). Re-stamps the version."""
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    stored = int(row["value"]) if row else 1
    if stored >= SCHEMA_VERSION:
        return
    bootstrap_schema(conn)
```

In `src/orc/storage/workspace.py` `resolve()`: after opening the connection, call `db.ensure_schema(conn)` before returning the Workspace.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_schema_migration.py -q && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/orc/storage tests/unit/test_schema_migration.py
git commit -m "feat(storage): schema v2 — gold/eval/policy tables + migration"
```

### Task 2.2: Gold store module (insert / list)

**Files:**
- Create: `src/orc/eval/__init__.py`
- Create: `src/orc/eval/gold.py`
- Test: `tests/unit/test_gold_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gold_store.py
from orc.eval import gold
from orc.storage import workspace as ws_module


def test_add_and_list_gold_claim(orc_home) -> None:
    ws_module.create("demo")
    gid = gold.add(
        "demo", claim="The sky is blue", expected_label="supported",
        corpus_version=0, source="import", note="seed",
    )
    [g] = gold.list_gold("demo")
    assert g.gold_id == gid
    assert g.claim == "The sky is blue"
    assert g.expected_label == "supported"
    assert g.relevant_chunk_ids is None
    assert g.source == "import"


def test_add_rejects_unknown_label(orc_home) -> None:
    ws_module.create("demo")
    import pytest
    with pytest.raises(ValueError, match="expected_label"):
        gold.add("demo", claim="x", expected_label="maybe", corpus_version=0, source="import")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gold_store.py -q`
Expected: FAIL — `No module named 'orc.eval'`

- [ ] **Step 3: Implement**

```python
# src/orc/eval/gold.py
"""Per-workspace gold-set store: human-confirmed (claim -> verdict) labels."""

from __future__ import annotations

import json
from dataclasses import dataclass

from orc.core.clock import now_iso
from orc.core.ids import new_id
from orc.paths import workspace_db_path
from orc.storage.db import open_connection, transaction

VALID_LABELS = frozenset({"supported", "contradicted", "not_found", "partial"})


@dataclass(frozen=True)
class GoldClaim:
    gold_id: str
    workspace: str
    claim: str
    expected_label: str
    corpus_version: int
    relevant_chunk_ids: list[str] | None
    source: str
    source_run_id: str | None
    note: str | None
    added_at: str
    added_by: str | None


def add(
    workspace: str,
    *,
    claim: str,
    expected_label: str,
    corpus_version: int,
    source: str,
    relevant_chunk_ids: list[str] | None = None,
    source_run_id: str | None = None,
    note: str | None = None,
    added_by: str | None = None,
) -> str:
    if expected_label not in VALID_LABELS:
        raise ValueError(f"expected_label must be one of {sorted(VALID_LABELS)}")
    gold_id = new_id()
    with open_connection(workspace_db_path(workspace)) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO gold_claim(gold_id, workspace, claim, expected_label, "
            "corpus_version, relevant_chunk_ids, source, source_run_id, note, "
            "added_at, added_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                gold_id, workspace, claim, expected_label, corpus_version,
                json.dumps(relevant_chunk_ids) if relevant_chunk_ids else None,
                source, source_run_id, note, now_iso(), added_by,
            ),
        )
    return gold_id


def list_gold(workspace: str) -> list[GoldClaim]:
    with open_connection(workspace_db_path(workspace)) as conn:
        rows = conn.execute(
            "SELECT * FROM gold_claim WHERE workspace=? ORDER BY added_at", (workspace,)
        ).fetchall()
    return [
        GoldClaim(
            gold_id=r["gold_id"], workspace=r["workspace"], claim=r["claim"],
            expected_label=r["expected_label"], corpus_version=r["corpus_version"],
            relevant_chunk_ids=json.loads(r["relevant_chunk_ids"]) if r["relevant_chunk_ids"] else None,
            source=r["source"], source_run_id=r["source_run_id"], note=r["note"],
            added_at=r["added_at"], added_by=r["added_by"],
        )
        for r in rows
    ]
```

Note: confirm `orc.core.clock.now_iso` and `orc.core.ids.new_id` exist (grep; they are used across the codebase). If the clock helper has a different name, match it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_gold_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orc/eval tests/unit/test_gold_store.py
git commit -m "feat(eval): gold-set store (add + list)"
```

### Task 2.3: Gold CLI — import / label / list

**Files:**
- Create: `src/orc/cli_commands/eval_cmd.py` (a click group `eval` with subcommands; named `eval_cmd` to avoid shadowing builtin)
- Modify: `src/orc/cli.py` (register `eval_cmd.eval_group`)
- Test: `tests/unit/test_eval_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_eval_cli.py
import json
from click.testing import CliRunner
from orc.cli import main
from orc.eval import gold
from orc.storage import workspace as ws_module


def test_eval_import_seeds_gold_from_yaml(orc_home, tmp_path) -> None:
    ws_module.create("demo")
    f = tmp_path / "claims.yaml"
    f.write_text(
        "- id: c1\n  text: The sky is blue\n  expected: supported\n"
        "- id: c2\n  text: Pigs fly\n  expected: not_found\n"
    )
    res = CliRunner().invoke(main, ["eval", "import", str(f), "-w", "demo"])
    assert res.exit_code == 0, res.output
    labels = {g.expected_label for g in gold.list_gold("demo")}
    assert labels == {"supported", "not_found"}


def test_eval_label_promotes_a_real_verdict(orc_home, monkeypatch) -> None:
    # Build one verify run via the fake-LLM idiom (reuse the helper pattern from
    # tests/unit/test_verify_claim_modes.py), capture its run_id, then promote.
    ...  # see test_verify_claim_modes.py for _run_skill + fake client setup
```

(For the second test, follow the exact fake-LLM run setup in `tests/unit/test_verify_claim_modes.py` — create a workspace, ingest a corpus, run `verify_claim` under a `FakeAnthropic`, take `result["_run_id"]`, then invoke `["eval", "label", run_id, "--verdict", "supported", "-w", "demo"]` and assert a promoted `GoldClaim` exists with `source="promoted"`, `source_run_id=run_id`, and `corpus_version` pulled from the trace.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_eval_cli.py -q`
Expected: FAIL — `No such command 'eval'`.

- [ ] **Step 3: Implement**

```python
# src/orc/cli_commands/eval_cmd.py
"""`orc eval ...` — gold set, gate measurement, and tiered calibration."""

from __future__ import annotations

import json as json_lib
from pathlib import Path

import click
import yaml

from orc.errors import WorkspaceNotFoundError
from orc.eval import gold
from orc.storage import workspace as ws_module
from orc.storage.trace_store import load_trace


@click.group("eval")
def eval_group() -> None:
    """Measure and calibrate the verification gate against a gold set."""


@eval_group.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--workspace", "-w", default=None)
def import_command(path: Path, workspace: str | None) -> None:
    """Seed gold claims from a YAML file (id/text/expected[/relevant_chunk_ids/note])."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    items = yaml.safe_load(path.read_text()) or []
    n = 0
    for item in items:
        gold.add(
            ws.name,
            claim=item["text"],
            expected_label=item["expected"],
            corpus_version=ws.corpus_version,
            relevant_chunk_ids=item.get("relevant_chunk_ids"),
            source="import",
            note=item.get("note"),
        )
        n += 1
    click.echo(f"Imported {n} gold claim(s) into {ws.name}")


@eval_group.command("label")
@click.argument("run_id")
@click.option("--verdict", required=True,
              type=click.Choice(["supported", "contradicted", "not_found", "partial"]))
@click.option("--relevant", "relevant", multiple=True, help="Relevant chunk id (repeatable)")
@click.option("--workspace", "-w", default=None)
@click.option("--note", default=None)
def label_command(run_id, verdict, relevant, workspace, note) -> None:
    """Promote/correct a real verdict into the gold set."""
    trace = load_trace(run_id)
    claim = (trace.get("inputs") or {}).get("claim") or trace.get("output", {}).get("claim")
    if not claim:
        raise click.ClickException(f"Run {run_id} has no claim to label")
    gold.add(
        trace["workspace"],
        claim=claim,
        expected_label=verdict,
        corpus_version=trace["corpus_version"],
        relevant_chunk_ids=list(relevant) or None,
        source="promoted",
        source_run_id=run_id,
        note=note,
    )
    click.echo(f"Labelled run {run_id} as {verdict} in {trace['workspace']}")


@eval_group.command("gold")
@click.argument("action", type=click.Choice(["list"]))
@click.option("--workspace", "-w", default=None)
@click.option("--json", "as_json", is_flag=True)
def gold_command(action, workspace, as_json) -> None:
    """Inspect the gold set (currently: list)."""
    try:
        ws = ws_module.resolve(workspace)
    except WorkspaceNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    items = gold.list_gold(ws.name)
    stale = [g for g in items if g.relevant_chunk_ids and g.corpus_version < ws.corpus_version]
    if as_json:
        click.echo(json_lib.dumps([
            {"gold_id": g.gold_id, "claim": g.claim, "expected_label": g.expected_label,
             "corpus_version": g.corpus_version, "source": g.source,
             "stale_chunk_labels": g in stale}
            for g in items], indent=2))
        return
    for g in items:
        flag = "  [stale chunk labels]" if g in stale else ""
        click.echo(f"{g.gold_id}  {g.expected_label:<12} {g.claim[:60]}{flag}")
```

Register in `src/orc/cli.py`: `from orc.cli_commands import eval_cmd` and `main.add_command(eval_cmd.eval_group)`. Confirm `yaml` (pyyaml) is already a dependency (it is — used by manifests).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_eval_cli.py -q && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/orc/cli_commands/eval_cmd.py src/orc/cli.py tests/unit/test_eval_cli.py
git commit -m "feat(cli): orc eval import/label/gold list"
```

---

## Stage 3 — `orc eval run` / `show`

### Task 3.1: Eval runner (judge accuracy + calibration + retrieval recall)

**Files:**
- Create: `src/orc/eval/runner.py` (`run_eval(workspace, *, mode=None, k=10) -> EvalReport`)
- Test: `tests/unit/test_eval_runner.py`

The runner, for each gold claim: opens a Run tagged `inputs={"_eval_id": eval_id, "gold_id": ...}`, calls `verify_claim.run(workspace, run, claim=g.claim, mode=mode, corpus_version=g.corpus_version)`, records `LabeledResult(predicted=verdict_label, expected=g.expected_label)` and `ConfidenceResult(confidence, correct)`. For gold with `relevant_chunk_ids`, compute recall@k = |retrieved ∩ relevant| / |relevant| using `result["retrieval_chunk_ids"]`. Aggregates via `orc.metrics`. Persists an `eval_run` row.

- [ ] **Step 1: Write the failing test** — script a `FakeAnthropic` returning known verdicts for two gold claims (one correct, one wrong), assert `report.judge["f1"]`, `report.calibration.ece`, and `report.retrieval_recall` match hand-computed values, and that an `eval_run` row was written. Reuse the corpus-ingest + fake-client setup from `tests/unit/test_verify_claim_modes.py`.

- [ ] **Step 2: Run — FAIL** (`No module named 'orc.eval.runner'`).

- [ ] **Step 3: Implement** `run_eval` per the description above, returning a frozen `EvalReport(eval_id, n, judge: dict, per_mode: dict, per_domain: dict, calibration_ece: float, reliability: list[Bin], retrieval_recall: float | None, stale_entries: int)`. Persist `eval_run(eval_id, workspace, created_at, config_json, metrics_json)`. Each per-claim verify is a normal traced Run (so the eval is replayable claim-by-claim).

- [ ] **Step 4: Run — PASS**, then full suite.

- [ ] **Step 5: Commit** `feat(eval): run_eval — judge accuracy, calibration, recall`.

### Task 3.2: `orc eval run` / `orc eval show` CLI

**Files:**
- Modify: `src/orc/cli_commands/eval_cmd.py` (`run` + `show` subcommands)
- Modify: `src/orc/eval/runner.py` (add `load_eval(workspace, eval_id) -> EvalReport`)
- Test: extend `tests/unit/test_eval_cli.py`

- [ ] **Step 1** Failing CLI test: import a 2-claim gold set, run `["eval", "run", "-w", "demo", "--json"]` under a fake client, assert the JSON carries `judge.f1`, `calibration.ece`, `n`; then `["eval", "show", eval_id, "-w", "demo", "--json"]` round-trips the same metrics.
- [ ] **Step 2** Run — FAIL (`No such command 'run'`).
- [ ] **Step 3** Implement `run_command` (rich table by default: per-mode/domain scores, an ECE line, a reliability table, recall@k, a stale-entries warning; `--json` emits the metrics dict) and `show_command` (loads the persisted `eval_run`).
- [ ] **Step 4** Run — PASS + full suite.
- [ ] **Step 5** Commit `feat(cli): orc eval run/show`.

---

## Stage 4 — tiered_verify

### Task 4.1: Extract existing meta-modes into `modes/`

**Files:**
- Create: `src/orc/directives/research/skills/modes/__init__.py`
- Create: `src/orc/directives/research/skills/modes/decomposed.py` (move `_run_decomposed` + `_decompose_claim`)
- Create: `src/orc/directives/research/skills/modes/arithmetic.py` (move `_run_arithmetic`)
- Modify: `src/orc/directives/research/skills/verify_claim.py` (import the moved helpers; the dispatcher stays)
- Test: existing `tests/unit/test_verify_claim_modes.py` must stay green unchanged.

- [ ] **Step 1** Run the existing mode tests to record the green baseline: `uv run pytest tests/unit/test_verify_claim_modes.py -q`.
- [ ] **Step 2** Move `_run_decomposed`/`_decompose_claim` to `modes/decomposed.py` and `_run_arithmetic` to `modes/arithmetic.py`, re-exporting them from `verify_claim` (import at top). Keep signatures identical (they already take `self`/explicit kwargs). No behavior change.
- [ ] **Step 3** Run the same tests — still PASS unchanged (this is a pure refactor; no new test, the existing suite is the guard).
- [ ] **Step 4** Full suite + ruff.
- [ ] **Step 5** Commit `refactor(verify): extract meta-modes into modes/`.

### Task 4.2: tiered_verify meta-mode

**Files:**
- Create: `src/orc/directives/research/skills/modes/tiered.py` (`run_tiered(...)`)
- Modify: `src/orc/directives/research/skills/verify_claim.py` (dispatch `mode == "tiered"` to `run_tiered`)
- Create: `src/orc/eval/policy.py` (`load_policy(workspace) -> TieredPolicy | None`, `save_policy(...)`)
- Test: `tests/unit/test_tiered_verify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tiered_verify.py — sketch
# Fake client scripted so Tier-1 (binary) returns faithful=True confidence=0.99
# -> accept without escalating (only ONE llm call recorded).
def test_tier1_accept_above_threshold(...):
    ...
    assert result["label"] == "supported"
    assert result["tier"] == 1
    # exactly one verdict call (no escalation)

# Tier-1 confidence 0.60 < threshold -> escalate; Tier-2 evidence verdict wins,
# trace records both verdicts + escalation reason + the tier-2 model.
def test_low_confidence_escalates_to_tier2(...):
    ...
    assert result["tier"] == 2
    assert result["escalated"] is True
    # tier-2 used the configured top_judge_model when set
```

- [ ] **Step 2** Run — FAIL (`mode "tiered"` unknown / no `run_tiered`).
- [ ] **Step 3** Implement `run_tiered`: load `TieredPolicy` (or a documented default `escalation_threshold=0.9`, `tier1_model="claude-haiku-4-5"`, `tier2_model="claude-sonnet-4-6"`, warn once if no policy). Tier 1 = `verify_claim.run(..., mode="binary", model=policy.tier1_model)`. If `confidence >= threshold` return it tagged `tier=1, escalated=False`. Else Tier 2 = `verify_claim.run(..., mode="evidence", model=policy.top_judge_model or policy.tier2_model)`, return tagged `tier=2, escalated=True`, and `run.record("tiered", {...both verdicts, threshold, reason...})`. Add `"tiered"` to the valid-mode set; optionally let `route_to_mode` map a domain to it.
- [ ] **Step 4** Run — PASS + full suite.
- [ ] **Step 5** Commit `feat(verify): tiered_verify meta-mode`.

---

## Stage 5 — `orc eval calibrate`

### Task 5.1: Threshold sweep + achievability guard

**Files:**
- Create: `src/orc/eval/calibrate.py` (`calibrate(workspace, *, target=0.95, tier1_model, tier2_model, top_judge=None) -> CalibrationResult`)
- Test: `tests/unit/test_calibrate.py`

`calibrate` runs the gold set through Tier 1 only (Haiku binary), collects `ConfidenceResult`s, then sweeps candidate thresholds (the distinct observed confidences) and, for each, computes the accuracy of *accepted* claims (confidence ≥ threshold). It returns the lowest threshold whose accepted-accuracy ≥ target, the escalation rate at that threshold, and `achievable: bool`. When no threshold reaches the target it returns `achievable=False` with the max accepted-accuracy and the threshold that achieved it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_calibrate.py — pure function over a scripted reliability curve
from orc.eval.calibrate import sweep_threshold
from orc.metrics.calibration import ConfidenceResult


def test_sweep_finds_lowest_threshold_meeting_target() -> None:
    results = [
        ConfidenceResult(0.99, True), ConfidenceResult(0.98, True),
        ConfidenceResult(0.80, True), ConfidenceResult(0.79, False),
    ]
    r = sweep_threshold(results, target=0.95)
    assert r.achievable is True
    assert r.threshold == 0.98          # accepting >=0.98 -> 2/2 correct
    assert r.escalation_rate == 0.5     # 2 of 4 fall below


def test_sweep_reports_unachievable_target() -> None:
    results = [ConfidenceResult(0.99, False), ConfidenceResult(0.98, True)]
    r = sweep_threshold(results, target=0.95)
    assert r.achievable is False
    assert round(r.max_accuracy, 3) == 1.0 or r.max_accuracy <= 1.0  # best accepted subset
```

- [ ] **Step 2** Run — FAIL (`No module named 'orc.eval.calibrate'`).
- [ ] **Step 3** Implement `sweep_threshold` (pure, fully unit-testable) + `calibrate` (runs Tier 1 over the gold set via a fresh eval, then sweeps). Define `CalibrationResult(achievable, threshold, escalation_rate, accepted_accuracy, max_accuracy)`.
- [ ] **Step 4** Run — PASS + full suite.
- [ ] **Step 5** Commit `feat(eval): calibrate threshold sweep + guard`.

### Task 5.2: `orc eval calibrate` CLI → tiered_policy

**Files:**
- Modify: `src/orc/cli_commands/eval_cmd.py` (`calibrate` subcommand)
- Modify: `src/orc/eval/policy.py` (`save_policy`)
- Test: extend `tests/unit/test_eval_cli.py` + `tests/unit/test_tiered_verify.py` (policy read-back)

- [ ] **Step 1** Failing test: with a scripted gold set + fake client, `["eval", "calibrate", "-w", "demo", "--target", "0.95"]` writes a `tiered_policy` row whose `escalation_threshold` matches the sweep, prints the escalation rate, and on an unachievable target prints the guard message and a nonzero-but-graceful note. Then `run_tiered` reads that policy back (assert `load_policy("demo").escalation_threshold`).
- [ ] **Step 2** Run — FAIL (`No such command 'calibrate'`).
- [ ] **Step 3** Implement `calibrate_command`: run `calibrate`, on success `save_policy(...)` and echo the threshold + escalation rate; on `achievable=False` echo the guard message (*"Tier 1 cannot reach {target} at any cutoff (max {max_accuracy:.2f}); escalating all claims — lower --target or improve the gold set"*) and still save a policy with the best threshold so behavior is defined.
- [ ] **Step 4** Run — PASS + full suite.
- [ ] **Step 5** Commit `feat(cli): orc eval calibrate -> tiered_policy`.

---

## Stage 6 — Docs

### Task 6.1: Coverage ceiling, CHANGELOG, README

**Files:**
- Modify: `README.md` (commands: `orc eval import|label|run|show|calibrate`, `verify --mode tiered`; one coverage-ceiling sentence about eval measuring against the user's own labels)
- Modify: `docs/compliance/eu-ai-act.md`, `docs/positioning/competitive.md` (the same one-line caveat)
- Modify: `CHANGELOG.md` (Unreleased: gold set, `orc eval`, tiered verification, calibration)

- [ ] **Step 1** Add the README command lines + the coverage-ceiling sentence: *"`orc eval` measures judge accuracy and retrieval recall against your own labelled gold set — it quantifies how well the gate matches your labels and cannot detect faithful-but-wrong corpus content (no gold set can)."*
- [ ] **Step 2** Mirror the one-line caveat into the two docs; add CHANGELOG Unreleased bullets.
- [ ] **Step 3** Run `uv run pytest -q` (docs change nothing) + a manual `uv run orc eval --help` to confirm the command tree renders.
- [ ] **Step 4** Commit `docs: document orc eval + tiered verification`.

### Task 6.2: PR

- [ ] Push `feat/eval-tiered-verification`; open a PR summarizing the gold set → eval → calibrate → tiered loop, with the suite/ruff results and the calibrate achievability-guard behavior. Do not merge.

---

## Self-review notes

- **Spec coverage:** gold store (2.1–2.3) ✓; import+promote (2.3) ✓; judge accuracy + calibration + retrieval recall (3.1) ✓; corpus-version frozen retrieval recall (3.1, uses `g.corpus_version`) ✓; eval as traced/replayable runs (3.1) ✓; tiered_verify + configurable cross-family top judge (4.2) ✓; modes/ extraction (4.1) ✓; calibrate + achievability guard + escalation rate (5.1–5.2) ✓; tiered_policy in orc.db (2.1, 5.2) ✓; metrics extraction (1.1–1.3) ✓; coverage-ceiling honesty (6.1) ✓; schema-v2 migration (2.1) ✓.
- **Type consistency:** `LabeledResult`/`ConfidenceResult`/`Bin`/`GoldClaim`/`TieredPolicy`/`CalibrationResult`/`EvalReport` named consistently across tasks; `verify_claim.run()` kwargs (`mode`, `model`, `corpus_version`) match the real signature confirmed in the spec.
- **Stage independence:** 1 ships alone (library); 2 ships alone (gold store usable); 3 needs 1+2; 4 needs nothing new but is most useful after 3; 5 needs 3+4. Each stage is independently green.
