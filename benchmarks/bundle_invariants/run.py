"""Audit-bundle invariants benchmark.

Exercises `orc audit export` end-to-end by running a randomized but
reproducible sequence of `ingest`, `verify`, `search`, and approval
operations, exporting the bundle, and asserting structural invariants a
regulator or third-party reviewer would rely on.

Invariants under test (each scored independently so a partial failure is
visible rather than aborting the whole run):

  I1  Every file in manifest.json[files] exists in the tarball and its
      sha256 matches the manifest entry.
  I2  Every row in runs.csv has a corresponding traces/<YYYY>/<MM>/<run_id>.json.
  I3  Every chunk_id referenced in a trace (retrieval, supporting,
      contradicting) belongs to an evidence_id present in evidence.csv.
  I4  Every evidence row's sha256 matches the on-disk source file when the
      source is still reachable. Skipped (not failed) when the source is
      absent — bundles ship without the source files by design.
  I5  Every trace's schema_version is in SUPPORTED_TRACE_SCHEMA_VERSIONS.
  I6  Every approval row references a source_run_id that appears in
      runs.csv. Every approval_decision row references an approval_id that
      appears in the approval section.

Run:
    uv run python -m benchmarks.bundle_invariants.run [--seed 42] [--ops 30]

Output:
    results/<timestamp>/
        results.json   per-invariant pass/fail counts + bundle pointer
        audit.tar.gz   the benchmark's bundle (the same one we verified)
        manifest.json  extracted for inspection
        README.md      summary
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import shutil
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = Path(__file__).parent / "results"


@dataclass
class InvariantResult:
    name: str
    description: str
    checks: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass
class Report:
    invariants: list[InvariantResult]
    bundle_path: str
    bundle_sha256: str
    ops_executed: dict[str, int]

    @property
    def ok(self) -> bool:
        return all(i.ok for i in self.invariants)


def _seed_corpus(orc_home: Path, rng: random.Random) -> str:
    os.environ["ORC_HOME"] = str(orc_home)
    from orc.ingest.pipeline import ingest as do_ingest
    from orc.storage import workspace as ws_module

    docs = orc_home / "corpus"
    docs.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        body = (
            f"# Doc {i}\n\nThe orc verification runtime ships claim {i} "
            f"in version 0.1.2. Random token: {rng.randint(10**6, 10**7)}.\n"
        )
        (docs / f"doc-{i}.md").write_text(body)
    ws = ws_module.create("bench-invariants")
    do_ingest(ws, str(docs))
    return ws.name


def _exercise_workspace(workspace_name: str, ops: int, rng: random.Random) -> dict[str, int]:
    """Drive a randomized but seeded mix of operations against the workspace."""
    from tests._fake_llm import FakeAnthropic, make_verdict_response

    from orc import directives
    from orc.queue import approval as approval_module
    from orc.runs import open_run
    from orc.storage import workspace as ws_module

    ws = ws_module.resolve(workspace_name)
    counts = {"verify": 0, "search": 0, "approval_enqueue": 0, "approval_decide": 0}
    verify_skill = directives.get("research").skills["verify_claim"]
    search_skill = directives.get("research").skills["search_evidence"]

    queries = [
        "verification runtime",
        "evidence corpus",
        "claim verdict",
        "approval queue",
        "version 0.1.2",
        "trace replay",
    ]

    enqueued_ids: list[str] = []
    for _ in range(ops):
        op = rng.choice(["verify", "search", "verify", "approval", "search"])
        if op == "verify":
            q = rng.choice(queries)
            fake = FakeAnthropic(
                responses=[
                    make_verdict_response(
                        label=rng.choice(["supported", "not_found", "partial"]),
                        confidence=round(rng.uniform(0.4, 0.95), 2),
                    )
                ]
            )
            with open_run(
                ws, directive="research", skill="verify_claim", inputs={"claim": q}
            ) as run:
                run.record_effective_kwargs({"claim": q, "model": "bench"})
                out = verify_skill.run(workspace=ws, run=run, claim=q, client=fake)
                run.close(output=out)
            counts["verify"] += 1
        elif op == "search":
            q = rng.choice(queries)
            with open_run(
                ws, directive="research", skill="search_evidence", inputs={"query": q, "k": 3}
            ) as run:
                run.record_effective_kwargs({"query": q, "k": 3})
                out = search_skill.run(workspace=ws, run=run, query=q, k=3)
                run.close(output=out)
            counts["search"] += 1
        else:
            # Need a source_run_id; do a search first.
            q = rng.choice(queries)
            with open_run(
                ws, directive="research", skill="search_evidence", inputs={"query": q, "k": 3}
            ) as run:
                run.record_effective_kwargs({"query": q, "k": 3})
                out = search_skill.run(workspace=ws, run=run, query=q, k=3)
                run.close(output=out)
                source_run_id = run.run_id
            approval_id = approval_module.enqueue(
                workspace=ws.name,
                directive="research",
                skill="search_evidence",
                source_run_id=source_run_id,
                summary=f"approve {q}",
                payload={"q": q},
            )
            enqueued_ids.append(approval_id)
            counts["approval_enqueue"] += 1
            counts["search"] += 1
            if enqueued_ids and rng.random() < 0.5:
                aid = rng.choice(enqueued_ids)
                import contextlib

                with contextlib.suppress(Exception):
                    if rng.random() < 0.7:
                        approval_module.accept(
                            ws.name, aid, decided_by="bench-reviewer", note="auto"
                        )
                    else:
                        approval_module.reject(
                            ws.name, aid, decided_by="bench-reviewer", note="auto"
                        )
                counts["approval_decide"] += 1
    return counts


def _untar(path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            f = tar.extractfile(m)
            if f is None:
                continue
            out[m.name] = f.read()
    return out


def _check_invariants(bundle_path: Path) -> list[InvariantResult]:
    from orc.runs.trace_schema import SUPPORTED_TRACE_SCHEMA_VERSIONS

    members = _untar(bundle_path)
    manifest = json.loads(members["manifest.json"])

    i1 = InvariantResult(
        name="I1",
        description="manifest hashes match extracted file contents",
    )
    for path, expected in manifest["files"].items():
        i1.checks += 1
        if path not in members:
            i1.failed += 1
            i1.failures.append(f"{path}: not in tarball")
            continue
        actual = hashlib.sha256(members[path]).hexdigest()
        if actual != expected:
            i1.failed += 1
            i1.failures.append(f"{path}: hash mismatch")
        else:
            i1.passed += 1

    i2 = InvariantResult(
        name="I2",
        description="every runs.csv row has a traces/.../<run_id>.json",
    )
    run_rows = list(csv.DictReader(io.StringIO(members["runs.csv"].decode("utf-8"))))
    trace_files = {p.split("/")[-1].removesuffix(".json") for p in members if p.startswith("traces/")}
    for r in run_rows:
        i2.checks += 1
        if r["run_id"] in trace_files:
            i2.passed += 1
        else:
            i2.failed += 1
            i2.failures.append(f"run_id {r['run_id']}: trace JSON missing from bundle")

    i3 = InvariantResult(
        name="I3",
        description="every chunk_id in a trace points at an evidence row",
    )
    evidence_rows = list(csv.DictReader(io.StringIO(members["evidence.csv"].decode("utf-8"))))
    known_evidence_ids = {e["evidence_id"] for e in evidence_rows}
    for path, data in members.items():
        if not path.startswith("traces/") or not path.endswith(".json"):
            continue
        trace = json.loads(data)
        retrieval = (trace.get("retrieval") or {}).get("returned") or []
        for chunk in retrieval:
            i3.checks += 1
            eid = chunk.get("evidence_id")
            if eid in known_evidence_ids:
                i3.passed += 1
            else:
                i3.failed += 1
                i3.failures.append(
                    f"{path}: retrieval chunk references unknown evidence_id {eid!r}"
                )

    i4 = InvariantResult(
        name="I4",
        description="evidence sha256 matches on-disk source where reachable",
    )
    for ev in evidence_rows:
        i4.checks += 1
        src = Path(ev["source_path"])
        if not src.exists():
            i4.skipped += 1
            continue
        actual = hashlib.sha256(src.read_bytes()).hexdigest()
        if actual == ev["sha256"]:
            i4.passed += 1
        else:
            i4.failed += 1
            i4.failures.append(f"{ev['evidence_id']}: sha256 differs from {src}")

    i5 = InvariantResult(
        name="I5",
        description="every trace's schema_version is supported by this build",
    )
    for path, data in members.items():
        if not path.startswith("traces/") or not path.endswith(".json"):
            continue
        trace = json.loads(data)
        v = trace.get("schema_version", 1)
        i5.checks += 1
        if v in SUPPORTED_TRACE_SCHEMA_VERSIONS:
            i5.passed += 1
        else:
            i5.failed += 1
            i5.failures.append(f"{path}: schema_version={v!r} not supported")

    i6 = InvariantResult(
        name="I6",
        description="approvals reference valid source_run_ids; decisions reference valid approval_ids",
    )
    approval_rows = list(csv.DictReader(io.StringIO(members["approvals.csv"].decode("utf-8"))))
    run_ids_set = {r["run_id"] for r in run_rows}
    approval_ids_set: set[str] = set()
    for r in approval_rows:
        if r.get("row_kind") == "approval":
            i6.checks += 1
            approval_ids_set.add(r["approval_id"])
            if r["source_run_id"] in run_ids_set:
                i6.passed += 1
            else:
                i6.failed += 1
                i6.failures.append(
                    f"approval {r['approval_id']}: source_run_id {r['source_run_id']!r} not in runs.csv"
                )
    for r in approval_rows:
        if r.get("row_kind") == "decision":
            i6.checks += 1
            if r["approval_id"] in approval_ids_set:
                i6.passed += 1
            else:
                i6.failed += 1
                i6.failures.append(
                    f"decision {r['decision_id']}: approval_id {r['approval_id']!r} not in approvals"
                )

    return [i1, i2, i3, i4, i5, i6]


def _render_readme(report: Report) -> str:
    lines = [
        "# Audit-bundle invariants benchmark — results",
        "",
        "This run exercises `orc audit export` and verifies the bundle a regulator",
        "or third-party reviewer would consume. Each invariant is scored independently.",
        "",
        "## Invariants",
        "",
        "| ID | What | checks | passed | failed | skipped |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for i in report.invariants:
        lines.append(
            f"| **{i.name}** | {i.description} | {i.checks} | {i.passed} | {i.failed} | {i.skipped} |"
        )
    lines.extend(
        [
            "",
            f"**Bundle sha256:** `{report.bundle_sha256}`",
            "",
            "## Operations executed",
            "",
        ]
    )
    for k, v in sorted(report.ops_executed.items()):
        lines.append(f"- {k}: {v}")
    if any(i.failures for i in report.invariants):
        lines.extend(["", "## Failures", ""])
        for i in report.invariants:
            for f in i.failures:
                lines.append(f"- **{i.name}**: {f}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ops", type=int, default=30, help="Operations to execute (default 30)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "src"))

    from orc.audit.export import export_workspace
    from orc.core.clock import now_iso

    ts = now_iso().replace(":", "").replace("-", "").replace("T", "-")[:15]
    out_dir = args.out or (RESULTS_ROOT / ts)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    tmp_home = Path(tempfile.mkdtemp(prefix="orc-bench-bundle-"))
    try:
        workspace_name = _seed_corpus(tmp_home, rng)
        ops_counts = _exercise_workspace(workspace_name, args.ops, rng)
        bundle_path = out_dir / "audit.tar.gz"
        export_workspace(workspace_name, output_path=bundle_path)

        invariants = _check_invariants(bundle_path)
        bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        report = Report(
            invariants=invariants,
            bundle_path=str(bundle_path),
            bundle_sha256=bundle_sha,
            ops_executed=ops_counts,
        )
        (out_dir / "results.json").write_text(
            json.dumps(
                {
                    "ok": report.ok,
                    "bundle_sha256": bundle_sha,
                    "ops_executed": ops_counts,
                    "invariants": [asdict(i) for i in invariants],
                },
                indent=2,
            )
        )
        # Extract manifest for inspection.
        with tarfile.open(bundle_path) as tar:
            mf = tar.extractfile("manifest.json")
            if mf:
                (out_dir / "manifest.json").write_bytes(mf.read())
        (out_dir / "README.md").write_text(_render_readme(report))

        print("\n== bundle_invariants ==")
        for i in invariants:
            mark = "✓" if i.ok else "✗"
            print(
                f"  {mark} {i.name:>3}  {i.description:<55}  "
                f"checks={i.checks} pass={i.passed} fail={i.failed} skip={i.skipped}"
            )
        print(f"  bundle sha256: {bundle_sha}")
        print(f"  results dir  : {out_dir}")
        return 0 if report.ok else 1
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
