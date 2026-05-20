"""Audit-export tests."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from orc.audit.export import (
    EXPORT_MANIFEST_VERSION,
    AuditExportError,
    export_workspace,
)
from orc.ingest.pipeline import ingest as do_ingest
from orc.llm import client as client_module
from orc.queue import approval as approval_module
from orc.runs import open_run
from orc.runs.trace_schema import (
    LATEST_TRACE_SCHEMA_VERSION,
    SUPPORTED_TRACE_SCHEMA_VERSIONS,
)
from orc.storage import workspace as ws_module
from orc.storage.trace_store import find_trace_path
from tests._fake_llm import FakeAnthropic, make_verdict_response


def _seed_workspace(orc_home: Path, tmp_path: Path) -> str:
    """Make a workspace with: 1 evidence doc, 1 verify_claim run, 1 approval."""
    ws = ws_module.create("demo")
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.md").write_text("# Doc\n\nSkills API ships in October 2025.\n")
    do_ingest(ws, str(corpus))
    return ws.name


def _make_verify_run(name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    fake = FakeAnthropic(responses=[make_verdict_response(label="not_found", confidence=0.5)])
    monkeypatch.setattr(client_module, "_client", fake)
    monkeypatch.setattr(client_module, "_factory", None)
    from orc import directives

    ws = ws_module.resolve(name)
    skill = directives.get("research").skills["verify_claim"]
    with open_run(ws, directive="research", skill="verify_claim", inputs={"claim": "x"}) as run:
        run.record_effective_kwargs({"claim": "x", "model": "claude-sonnet-4-6", "k": 10})
        result = skill.run(workspace=ws, run=run, claim="x")
        run.close(output=result)
    return run.run_id


def _make_approval(name: str, source_run_id: str) -> str:
    approval_module.ensure_approval_table(name)
    return approval_module.enqueue(
        workspace=name,
        directive="research",
        skill="verify_claim",
        source_run_id=source_run_id,
        summary="ship a thing",
        payload={"claim": "x"},
    )


def _untar(path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as tar:
        for m in tar.getmembers():
            f = tar.extractfile(m)
            if f is None:
                continue
            out[m.name] = f.read()
    return out


def test_export_produces_expected_files(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed_workspace(orc_home, tmp_path)
    run_id = _make_verify_run(name, monkeypatch)
    _make_approval(name, run_id)

    out = tmp_path / "audit.tar.gz"
    manifest = export_workspace(name, output_path=out)
    assert out.exists()
    members = _untar(out)

    expected_top_level = {
        "manifest.json",
        "README.md",
        "workspace.json",
        "runs.csv",
        "evidence.csv",
        "approvals.csv",
    }
    assert expected_top_level.issubset(members.keys())

    # At least one trace file, in the YYYY/MM layout.
    trace_files = [n for n in members if n.startswith("traces/") and n.endswith(".json")]
    assert len(trace_files) == 1
    assert run_id in trace_files[0]

    # Manifest reports correct counts.
    assert manifest.counts["runs"] == 1
    assert manifest.counts["evidence"] == 1
    assert manifest.counts["approvals"] == 1
    assert manifest.counts["trace_files"] == 1
    assert manifest.workspace == name
    assert manifest.export_manifest_version == EXPORT_MANIFEST_VERSION
    assert LATEST_TRACE_SCHEMA_VERSION in manifest.trace_schema_versions_seen


def test_export_manifest_hashes_match_file_contents(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: integrity is the whole point of the bundle. Recompute every
    sha256 against the bundle contents and confirm they match the manifest."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit.tar.gz"
    export_workspace(name, output_path=out)

    members = _untar(out)
    manifest = json.loads(members["manifest.json"])
    for path, expected_hash in manifest["files"].items():
        actual = hashlib.sha256(members[path]).hexdigest()
        assert actual == expected_hash, f"hash mismatch for {path}"


def test_export_default_bundle_does_not_include_workspace_db_or_evidence(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default export must stay light — no workspace DB, no evidence files.
    Compliance buyers opt in to self-contained bundles with --include-evidence."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit.tar.gz"
    manifest = export_workspace(name, output_path=out)

    members = _untar(out)
    workspace_members = [m for m in members if m.startswith("workspace/")]
    assert workspace_members == []
    assert manifest.self_contained is False


def test_export_include_evidence_bundles_db_and_evidence(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --include-evidence the tarball must carry workspace/orc.db plus
    every ingested evidence file, all hashed in manifest.files."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit-full.tar.gz"
    manifest = export_workspace(name, output_path=out, include_evidence=True)

    assert manifest.self_contained is True
    members = _untar(out)
    assert "workspace/orc.db" in members
    evidence_members = [m for m in members if m.startswith("workspace/evidence/")]
    assert len(evidence_members) >= 1, f"no evidence files in bundle: {list(members)}"

    # Every workspace/* file is hashed and the hashes match the actual bytes.
    for path in [*evidence_members, "workspace/orc.db"]:
        assert path in manifest.files, f"{path} missing from manifest.files"
        actual = hashlib.sha256(members[path]).hexdigest()
        assert actual == manifest.files[path], f"hash mismatch for {path}"


def test_self_contained_bundle_places_traces_under_workspace(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the high-severity finding: a self-contained bundle puts
    traces under workspace/traces/ so that `orc replay` (which resolves via
    workspace_traces_dir) actually finds them after the auditor extracts the
    workspace/ subtree into ~/.orc/workspaces/<name>/."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit-self-contained.tar.gz"
    manifest = export_workspace(name, output_path=out, include_evidence=True)

    members = _untar(out)
    top_level_traces = [
        m for m in members if m.startswith("traces/") and not m.startswith("workspace/")
    ]
    workspace_traces = [m for m in members if m.startswith("workspace/traces/")]

    assert workspace_traces, "self-contained bundle must have traces under workspace/"
    assert not top_level_traces, (
        f"self-contained bundle should not duplicate traces at top level: {top_level_traces}"
    )
    # All workspace traces are listed in manifest.files.
    for path in workspace_traces:
        assert path in manifest.files, f"{path} missing from manifest.files"


def test_inspectable_bundle_keeps_traces_at_top_level(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --include-evidence is NOT set, traces stay at the top level so the
    bundle reads naturally as an inspection artifact."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit-inspect.tar.gz"
    export_workspace(name, output_path=out)

    members = _untar(out)
    top_level_traces = [m for m in members if m.startswith("traces/")]
    workspace_traces = [m for m in members if m.startswith("workspace/traces/")]
    assert top_level_traces, "inspectable bundle should have traces at top level"
    assert not workspace_traces, "inspectable bundle should not put traces under workspace/"


def test_self_contained_downgraded_when_evidence_file_missing(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle must NOT claim self_contained=True if an expected evidence file
    is missing from disk. The manifest records the missing paths so an
    auditor can see exactly what's incomplete."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)

    # Delete one ingested evidence file to simulate corruption / accidental
    # cleanup between ingest and export.
    from orc.paths import workspace_evidence_dir

    ev_dir = workspace_evidence_dir(name)
    ev_files = list(ev_dir.rglob("*"))
    ev_files = [p for p in ev_files if p.is_file()]
    assert ev_files, "fixture should have at least one evidence file"
    ev_files[0].unlink()

    out = tmp_path / "audit-incomplete.tar.gz"
    manifest = export_workspace(name, output_path=out, include_evidence=True)

    assert manifest.self_contained is False, (
        "bundle missing source files must not claim self_contained=True"
    )
    assert len(manifest.missing_evidence_files) >= 1


def test_db_backup_via_sqlite_api_not_torn_read(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bundled orc.db must be a transactionally consistent copy — the
    SQLite Online Backup API guarantees this even if the source DB is in
    WAL mode with pending writes. Sanity-check: the copy opens cleanly and
    has the expected schema."""
    import sqlite3

    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit-db.tar.gz"
    export_workspace(name, output_path=out, include_evidence=True)
    members = _untar(out)

    extracted = tmp_path / "orc-copy.db"
    extracted.write_bytes(members["workspace/orc.db"])
    conn = sqlite3.connect(str(extracted))
    try:
        # Workspace + run tables should be queryable.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "workspace" in tables
        assert "run" in tables
        # The run we created during the test should be present.
        n_runs = conn.execute("SELECT count(*) FROM run").fetchone()[0]
        assert n_runs >= 1
    finally:
        conn.close()


def test_export_manifest_hashes_cover_every_tar_member_except_manifest(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the audit-export README's integrity claim: every file in
    the tarball except manifest.json (which can't hash itself) must be listed
    in manifest.files. README.md was previously omitted."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit.tar.gz"
    export_workspace(name, output_path=out)

    members = _untar(out)
    manifest = json.loads(members["manifest.json"])
    hashed = set(manifest["files"].keys())
    in_tar = set(members.keys()) - {"manifest.json"}
    missing = in_tar - hashed
    assert missing == set(), f"these tar members are not hashed: {missing}"


def test_export_refuses_when_trace_schema_unsupported(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsupported trace version is exactly the kind of silent compat
    failure audit export must not paper over."""
    name = _seed_workspace(orc_home, tmp_path)
    run_id = _make_verify_run(name, monkeypatch)

    # Tamper one trace JSON to claim a future schema version.
    trace_path = find_trace_path(name, run_id)
    payload = json.loads(trace_path.read_text())
    payload["schema_version"] = 99
    trace_path.write_text(json.dumps(payload))

    with pytest.raises(Exception) as excinfo:
        export_workspace(name, output_path=tmp_path / "audit.tar.gz")
    # Could be TraceSchemaError or AuditExportError depending on wrapping;
    # either way the message must call out the offending version.
    assert "99" in str(excinfo.value)


def test_export_refuses_when_trace_json_missing(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the run table lists a run with no JSON on disk, the bundle would be
    structurally incomplete — export must abort, not ship a half-bundle."""
    name = _seed_workspace(orc_home, tmp_path)
    run_id = _make_verify_run(name, monkeypatch)

    # Delete the trace JSON to simulate corruption.
    find_trace_path(name, run_id).unlink()
    with pytest.raises(AuditExportError) as excinfo:
        export_workspace(name, output_path=tmp_path / "audit.tar.gz")
    assert run_id in str(excinfo.value)


def test_export_unknown_workspace_raises(orc_home: Path, tmp_path: Path) -> None:
    with pytest.raises(AuditExportError):
        export_workspace("does-not-exist", output_path=tmp_path / "audit.tar.gz")


def test_export_range_filters_runs_by_started_at(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The range_from / range_to options bound the run rows included."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)

    # Use a `range_to` in the distant past so no runs match.
    out = tmp_path / "audit-empty.tar.gz"
    manifest = export_workspace(
        name,
        output_path=out,
        range_to="1970-01-01T00:00:00Z",
    )
    assert manifest.counts["runs"] == 0
    assert manifest.counts["trace_files"] == 0


def test_export_csv_contains_header_and_row(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed_workspace(orc_home, tmp_path)
    run_id = _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit.tar.gz"
    export_workspace(name, output_path=out)

    members = _untar(out)
    runs_csv = members["runs.csv"].decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(runs_csv)))
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["skill"] == "verify_claim"


def test_export_manifest_reports_supported_schema_versions(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)
    out = tmp_path / "audit.tar.gz"
    manifest = export_workspace(name, output_path=out)
    assert manifest.trace_schema_versions_supported == list(SUPPORTED_TRACE_SCHEMA_VERSIONS)


def test_cli_audit_export_creates_bundle(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: invoke `orc audit export` via Click and confirm the bundle
    lands where requested and contains the expected manifest."""
    from click.testing import CliRunner

    from orc.cli import main

    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)

    out = tmp_path / "cli-audit.tar.gz"
    runner = CliRunner()
    result = runner.invoke(
        main, ["audit", "export", "--workspace", name, "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    members = _untar(out)
    assert "manifest.json" in members
    manifest = json.loads(members["manifest.json"])
    assert manifest["workspace"] == name


def test_export_is_deterministic_for_identical_inputs(
    orc_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundles are signed by hash; identical inputs must produce identical
    file contents inside the tar (the outer gzip header is not stable, but
    inner contents and manifest hashes must be)."""
    name = _seed_workspace(orc_home, tmp_path)
    _make_verify_run(name, monkeypatch)

    out1 = tmp_path / "audit-1.tar.gz"
    out2 = tmp_path / "audit-2.tar.gz"
    export_workspace(name, output_path=out1)
    export_workspace(name, output_path=out2)

    m1 = _untar(out1)
    m2 = _untar(out2)
    # exported_at differs between bundles, so manifest.json itself differs.
    # Every other file in the bundle must be byte-identical.
    for k in set(m1) | set(m2):
        if k == "manifest.json":
            continue
        if k == "README.md":
            # README embeds exported_at too
            continue
        assert m1[k] == m2[k], f"non-deterministic file in bundle: {k}"
