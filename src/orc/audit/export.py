"""Audit-export: bundle a workspace's traces, run rows, evidence manifest,
approval queue, and runtime metadata into a single tarball that a deployer can
hand to a regulator, customer, or internal compliance reviewer.

The shape of the bundle is the contract — any change must bump the export
manifest version. Article 12 of the EU AI Act ("automatic recording of events
… records of decisions, justifications") and Article 26(6) ("keep logs … for
appropriate period") both expect a self-contained, time-bounded artifact that
a third party can read without access to the live system.

Layout:

    audit-<workspace>-<ts>.tar.gz
    ├── manifest.json          # this bundle's metadata + sha256 of every file
    ├── README.md              # human-readable index
    ├── workspace.json         # workspace row at export time
    ├── runs.csv               # one row per Run, filtered by date range
    ├── evidence.csv           # evidence manifest with sha256
    ├── approvals.csv          # approval queue with decisions
    └── traces/<YYYY>/<MM>/<run_id>.json   # full trace JSONs

Reproducibility:
- Every trace included is validated against `trace_schema.assert_supported`.
  An unsupported version aborts the export rather than producing a bundle
  with mixed semantics.
- The tar is built with fixed mtimes and sorted entry order so two exports
  of identical inputs produce byte-identical bundles (modulo gzip metadata).
- `manifest.json` records the orc version, python version, supported trace
  schema versions, and sha256 of every other file in the bundle.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import platform
import sqlite3
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from orc import __version__
from orc.core.clock import now_iso
from orc.errors import WorkspaceNotFoundError
from orc.paths import workspace_db_path, workspace_evidence_dir, workspace_traces_dir
from orc.runs.trace_schema import (
    SUPPORTED_TRACE_SCHEMA_VERSIONS,
    assert_supported,
)
from orc.storage import workspace as ws_module
from orc.storage.db import open_connection

EXPORT_MANIFEST_VERSION = 1
_FIXED_MTIME = 1577836800  # 2020-01-01 00:00 UTC — deterministic, recognizable.


class AuditExportError(Exception):
    """Raised when an export cannot be produced safely."""


@dataclass
class ExportManifest:
    """Top-level metadata for an audit bundle."""

    export_manifest_version: int
    orc_version: str
    python_version: str
    platform: str
    workspace: str
    exported_at: str
    range_from: str | None
    range_to: str | None
    trace_schema_versions_supported: list[int]
    trace_schema_versions_seen: list[int]
    counts: dict[str, int]
    self_contained: bool = False
    missing_evidence_files: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # path -> sha256

    def to_json(self) -> str:
        d = asdict(self)
        # Sort the files dict so two equivalent bundles produce identical JSON.
        d["files"] = dict(sorted(d["files"].items()))
        return json.dumps(d, indent=2, sort_keys=False)


def export_workspace(
    workspace_name: str,
    *,
    output_path: Path,
    range_from: str | None = None,
    range_to: str | None = None,
    include_evidence: bool = False,
) -> ExportManifest:
    """Bundle a workspace's audit-relevant state into a tar.gz at `output_path`.

    range_from / range_to filter by `run.started_at` (ISO 8601 strings,
    inclusive). They also filter approvals by `created_at`.

    When include_evidence=True the bundle also contains the workspace SQLite DB
    and every ingested evidence file (mounted under `workspace/`). The bundle
    is then self-contained: extract `workspace/` to ~/.orc/workspaces/<name>/
    and `orc replay <run_id>` runs without access to the original workspace.
    Default false to keep bundles light; opt in for external auditor handoff
    where the recipient has no access to the operator's infra.

    Returns the manifest written into the bundle. Raises AuditExportError
    on any problem that would make the bundle unsafe to ship.
    """
    try:
        ws = ws_module.resolve(workspace_name)
    except WorkspaceNotFoundError as exc:
        raise AuditExportError(str(exc)) from exc

    db_path = workspace_db_path(ws.name)
    if not db_path.exists():
        raise AuditExportError(f"Workspace {ws.name!r} has no orc.db at {db_path}")

    runs = _collect_runs(ws.name, range_from, range_to)
    evidence = _collect_evidence(ws.name)
    approvals, decisions = _collect_approvals(ws.name, range_from, range_to)
    workspace_row = _collect_workspace_row(ws.name)

    trace_payloads, schema_versions_seen = _collect_trace_payloads(
        ws.name, [r["run_id"] for r in runs]
    )

    files: dict[str, bytes] = {}
    files["workspace.json"] = json.dumps(workspace_row, indent=2).encode("utf-8")
    files["runs.csv"] = _rows_to_csv(runs).encode("utf-8")
    files["evidence.csv"] = _rows_to_csv(evidence).encode("utf-8")
    files["approvals.csv"] = _rows_to_csv(_join_approvals(approvals, decisions)).encode("utf-8")

    # Traces: top-level when bundle is inspect-only. Under workspace/traces/
    # when include_evidence=True, so that extracting `workspace/` into
    # ~/.orc/workspaces/<name>/ produces a workspace `orc replay` can find
    # traces in. Replay resolves via workspace_traces_dir(name) which is
    # exactly ~/.orc/workspaces/<name>/traces/.
    trace_prefix = "workspace/traces/" if include_evidence else "traces/"
    for rel_path, payload in trace_payloads.items():
        # rel_path looks like "traces/2026/05/<run_id>.json" — strip the
        # leading "traces/" and replace with the chosen prefix.
        suffix = rel_path[len("traces/") :] if rel_path.startswith("traces/") else rel_path
        files[f"{trace_prefix}{suffix}"] = json.dumps(payload, indent=2).encode("utf-8")

    evidence_files_count = 0
    missing_evidence_files: list[str] = []
    if include_evidence:
        evidence_blobs, missing_evidence_files = _collect_evidence_blobs(ws.name, evidence)
        evidence_files_count = sum(
            1 for k in evidence_blobs if k.startswith("workspace/evidence/")
        )
        files.update(evidence_blobs)

    # `self_contained` is only true when (a) caller asked for it AND (b) every
    # evidence row in the DB actually had a file on disk that we packed.
    # A bundle that claims self_contained but is missing required source
    # files would be a worse-than-useless audit artifact.
    is_self_contained = include_evidence and not missing_evidence_files

    manifest = ExportManifest(
        export_manifest_version=EXPORT_MANIFEST_VERSION,
        orc_version=__version__,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        workspace=ws.name,
        exported_at=now_iso(),
        range_from=range_from,
        range_to=range_to,
        trace_schema_versions_supported=list(SUPPORTED_TRACE_SCHEMA_VERSIONS),
        trace_schema_versions_seen=sorted(schema_versions_seen),
        self_contained=is_self_contained,
        missing_evidence_files=missing_evidence_files,
        counts={
            "runs": len(runs),
            "evidence": len(evidence),
            "approvals": len(approvals),
            "approval_decisions": len(decisions),
            "trace_files": len(trace_payloads),
            "evidence_files": evidence_files_count,
            "missing_evidence_files": len(missing_evidence_files),
        },
    )
    for path, data in sorted(files.items()):
        manifest.files[path] = hashlib.sha256(data).hexdigest()

    # README is rendered last and depends on the manifest, but the readme
    # bytes themselves still need a hash so the integrity claim covers every
    # file in the tarball except manifest.json (which can't hash itself).
    readme = _render_readme(manifest)
    readme_bytes = readme.encode("utf-8")
    manifest.files["README.md"] = hashlib.sha256(readme_bytes).hexdigest()
    manifest_bytes = manifest.to_json().encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_tarball(
        output_path,
        entries=[
            ("manifest.json", manifest_bytes),
            ("README.md", readme_bytes),
            *sorted(files.items()),
        ],
    )
    return manifest


# ───────────── data collection ─────────────────────────────────


def _collect_workspace_row(name: str) -> dict[str, Any]:
    db = workspace_db_path(name)
    with open_connection(db) as conn:
        row = conn.execute(
            "SELECT name, schema_version, created_at, embedding_model, corpus_version "
            "FROM workspace WHERE name = ?",
            (name,),
        ).fetchone()
    return dict(row) if row else {}


def _collect_runs(
    name: str, range_from: str | None, range_to: str | None
) -> list[dict[str, Any]]:
    db = workspace_db_path(name)
    query = (
        "SELECT run_id, directive, skill, workspace, corpus_version, started_at, "
        "ended_at, status, model, total_input_tokens, total_output_tokens, "
        "total_cache_read, total_cache_creation, output_summary, error_message "
        "FROM run"
    )
    params: list[str] = []
    clauses: list[str] = []
    if range_from is not None:
        clauses.append("started_at >= ?")
        params.append(range_from)
    if range_to is not None:
        clauses.append("started_at <= ?")
        params.append(range_to)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY started_at ASC, run_id ASC"
    with open_connection(db) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _collect_evidence(name: str) -> list[dict[str, Any]]:
    db = workspace_db_path(name)
    with open_connection(db) as conn:
        rows = conn.execute(
            "SELECT evidence_id, source_path, stored_path, sha256, mime_type, title, "
            "ingested_at, corpus_version "
            "FROM evidence ORDER BY ingested_at ASC, evidence_id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def _collect_evidence_blobs(
    name: str, evidence_rows: list[dict[str, Any]]
) -> tuple[dict[str, bytes], list[str]]:
    """Read the workspace SQLite DB + every ingested evidence file.

    Returns (blobs, missing_evidence_files):
      - blobs: paths keyed under `workspace/` (an auditor extracts that
        subtree into `~/.orc/workspaces/<name>/` and replay is well-defined).
      - missing_evidence_files: stored_path values from the evidence table
        whose backing file was not found on disk. The caller uses this to
        downgrade the manifest's `self_contained` flag rather than ship a
        bundle that claims offline completeness while omitting required
        source files.

    The DB is copied via the SQLite Online Backup API rather than read_bytes()
    so a concurrent writer or pending WAL frames don't produce a torn copy.
    """
    blobs: dict[str, bytes] = {}
    missing: list[str] = []

    db_path = workspace_db_path(name)
    if db_path.exists():
        blobs["workspace/orc.db"] = _backup_sqlite_db(db_path)

    evidence_root = workspace_evidence_dir(name)
    # Walk every file the evidence rows reference and check it exists on disk.
    # We pack the actual stored file (via stored_path) under
    # workspace/evidence/<basename> so the path inside the bundle matches the
    # workspace layout. If stored_path is outside the evidence root we skip
    # (defensive) — this should not happen in practice because ingest copies
    # everything into the evidence dir.
    seen_paths: set[str] = set()
    for row in evidence_rows:
        stored = row.get("stored_path")
        if not stored:
            continue
        src = Path(stored)
        if not src.exists() or not src.is_file():
            missing.append(stored)
            continue
        try:
            rel = src.resolve().relative_to(evidence_root.resolve())
        except ValueError:
            # stored_path lives outside the evidence dir — record as missing
            # from the bundle's perspective rather than slip it under
            # workspace/evidence/ at the wrong path.
            missing.append(stored)
            continue
        key = f"workspace/evidence/{rel.as_posix()}"
        if key in seen_paths:
            continue
        blobs[key] = src.read_bytes()
        seen_paths.add(key)

    # Also include any stray files under the evidence dir that aren't in the
    # evidence table — keeps the bundle a faithful snapshot of the directory.
    if evidence_root.exists():
        for path in sorted(evidence_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(evidence_root)
            key = f"workspace/evidence/{rel.as_posix()}"
            if key in seen_paths:
                continue
            blobs[key] = path.read_bytes()
            seen_paths.add(key)

    return blobs, missing


def _backup_sqlite_db(db_path: Path) -> bytes:
    """Return the bytes of the workspace SQLite DB using the online backup
    API so a WAL-mode database with pending writes is captured consistently.

    Read_bytes() on a WAL-mode file can produce a copy that's missing the
    most recent transactions (still living in the .wal sidecar) or, in the
    worst case, an inconsistent page mix. The backup API is the supported
    way to grab a transactionally consistent snapshot.
    """
    src = sqlite3.connect(str(db_path))
    try:
        # Write to a temp file then read its bytes — the backup API needs a
        # destination Connection, and tempfile gives us a guaranteed-unique
        # path that doesn't collide with concurrent exports.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            dest = sqlite3.connect(str(tmp_path))
            try:
                src.backup(dest)
            finally:
                dest.close()
            return tmp_path.read_bytes()
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
    finally:
        src.close()


def _collect_approvals(
    name: str, range_from: str | None, range_to: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    db = workspace_db_path(name)
    query = (
        "SELECT approval_id, workspace, directive, skill, source_run_id, status, "
        "summary, approvers_required, created_at, decided_at, decided_by, decision_note "
        "FROM approval"
    )
    params: list[str] = []
    clauses: list[str] = []
    if range_from is not None:
        clauses.append("created_at >= ?")
        params.append(range_from)
    if range_to is not None:
        clauses.append("created_at <= ?")
        params.append(range_to)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at ASC, approval_id ASC"
    with open_connection(db) as conn:
        approvals = [dict(r) for r in conn.execute(query, params).fetchall()]
        if approvals:
            ids = [a["approval_id"] for a in approvals]
            placeholders = ",".join("?" * len(ids))
            decisions = [
                dict(r)
                for r in conn.execute(
                    f"SELECT decision_id, approval_id, decision, decided_by, "
                    f"decided_at, note FROM approval_decision "
                    f"WHERE approval_id IN ({placeholders}) "
                    f"ORDER BY decided_at ASC, decision_id ASC",
                    ids,
                ).fetchall()
            ]
        else:
            decisions = []
    return approvals, decisions


def _join_approvals(
    approvals: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flatten approvals and their decisions into one CSV-friendly row stream.

    Each approval emits at least one row (the approval row itself with
    decision columns blank). Each decision then emits one row that references
    the approval_id and carries its decided_by / decided_at / note.
    """
    out: list[dict[str, Any]] = []
    for a in approvals:
        out.append(
            {
                "row_kind": "approval",
                **a,
                "decision_id": "",
                "decision": "",
                "decided_at_decision": "",
                "note": "",
            }
        )
    for d in decisions:
        out.append(
            {
                "row_kind": "decision",
                "approval_id": d["approval_id"],
                "workspace": "",
                "directive": "",
                "skill": "",
                "source_run_id": "",
                "status": "",
                "summary": "",
                "approvers_required": "",
                "created_at": "",
                "decided_at": "",
                "decided_by": d["decided_by"],
                "decision_note": "",
                "decision_id": d["decision_id"],
                "decision": d["decision"],
                "decided_at_decision": d["decided_at"],
                "note": d.get("note") or "",
            }
        )
    return out


def _collect_trace_payloads(
    name: str, run_ids: list[str]
) -> tuple[dict[str, dict[str, Any]], set[int]]:
    """Load every trace JSON in `run_ids`, validate its schema, return the
    payloads keyed by their archive-relative path and the set of schema
    versions seen."""
    out: dict[str, dict[str, Any]] = {}
    versions: set[int] = set()
    traces_root = workspace_traces_dir(name)
    if not traces_root.exists():
        return out, versions

    by_id: dict[str, Path] = {}
    for p in traces_root.rglob("*.json"):
        by_id[p.stem] = p

    for rid in run_ids:
        p = by_id.get(rid)
        if p is None:
            # Index row exists but the JSON is missing; the audit story would
            # rather refuse to ship a half-complete bundle than silently elide.
            raise AuditExportError(
                f"Run {rid!r} listed in run table but trace JSON not found under {traces_root}"
            )
        payload = json.loads(p.read_text())
        v = assert_supported(payload, context=f"audit export of run {rid}")
        versions.add(v)
        # Reproduce the on-disk layout inside the tarball.
        rel = "traces/" + str(p.relative_to(traces_root)).replace("\\", "/")
        out[rel] = payload
    return out, versions


# ───────────── formatting ──────────────────────────────────────


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    # Stable column order: take the union of keys across rows in first-seen
    # order so an empty optional doesn't shuffle the header.
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r[k]) for k in cols})
    return buf.getvalue()


def _render_readme(m: ExportManifest) -> str:
    return (
        "# Orc audit export\n"
        f"\nThis bundle is the audit artifact for workspace `{m.workspace}`,\n"
        f"produced by `orc audit export` at `{m.exported_at}`.\n"
        "\n"
        "## Bundle metadata\n"
        f"- orc version: `{m.orc_version}`\n"
        f"- python: `{m.python_version}` ({m.platform})\n"
        f"- export manifest version: `{m.export_manifest_version}`\n"
        f"- trace schema versions supported by this build: "
        f"`{m.trace_schema_versions_supported}`\n"
        f"- trace schema versions seen in this bundle: "
        f"`{m.trace_schema_versions_seen}`\n"
        f"- date range (started_at): "
        f"`{m.range_from or '—'}` … `{m.range_to or '—'}`\n"
        "\n"
        "## Counts\n"
        + "".join(f"- {k}: `{v}`\n" for k, v in sorted(m.counts.items()))
        + "\n"
        "## File layout\n"
        "- `manifest.json` — this bundle's metadata + sha256 of every file below.\n"
        "- `workspace.json` — workspace row at export time.\n"
        "- `runs.csv` — one row per Run included in this bundle.\n"
        "- `evidence.csv` — evidence manifest with sha256 of each source.\n"
        "- `approvals.csv` — approval queue + per-approver decisions.\n"
        + (
            "- `workspace/traces/<YYYY>/<MM>/<run_id>.json` — full trace JSON for "
            "every included Run. (Self-contained bundles put traces under "
            "`workspace/` so `orc replay` finds them after extraction.)\n"
            "- `workspace/orc.db` — workspace SQLite database (corpus state, runs, "
            "approvals). Captured via SQLite Online Backup so WAL state is included.\n"
            "- `workspace/evidence/...` — every ingested evidence file, mounted under "
            "the same relative path as in the original workspace.\n"
            if m.self_contained
            else "- `traces/<YYYY>/<MM>/<run_id>.json` — full trace JSON for every "
            "included Run.\n"
        )
        + (
            f"\n_Missing evidence files (recorded in `manifest.missing_evidence_files`):_ "
            f"{len(m.missing_evidence_files)}\n"
            if m.missing_evidence_files
            else ""
        )
        + "\n"
        "## Reproducing a Run\n"
        + (
            "This bundle is **self-contained**.\n"
            "\n"
            "Extract and replay:\n"
            "```\n"
            f"tar xzf <this-bundle>.tar.gz workspace/\n"
            f"WS={m.workspace}-replay\n"
            f"mkdir -p ~/.orc/workspaces/$WS\n"
            "cp -R workspace/* ~/.orc/workspaces/$WS/\n"
            "ORC_DEFAULT_WORKSPACE=$WS orc replay <run_id>\n"
            "```\n"
            "\n"
            "`effective_kwargs` (v2 traces) pin the manifest defaults in force at\n"
            "the time of the original run, so a later manifest change does not\n"
            "silently shift behavior on replay.\n"
            if m.self_contained
            else "This bundle is an *inspectable* handoff: every Run's full trace JSON,\n"
            "retrieval set, LLM-call usage, and verdict is present and hashed.\n"
            "Bit-exact `orc replay` requires the original workspace database and\n"
            "evidence files — the bundle does not include those, so re-execution\n"
            "happens against the workspace that produced the trace. (Re-export with\n"
            "`--include-evidence` if the auditor needs offline replay.)\n"
            "`effective_kwargs` (v2 traces) pin the manifest defaults in force at\n"
            "the time of the original run, so a later manifest change does not\n"
            "silently shift behavior on replay.\n"
        )
        + "\n"
        "## Integrity\n"
        "Every file in this tarball except `manifest.json` is hashed in\n"
        "`manifest.json` (manifest.json cannot hash itself). Verify with:\n"
        "    sha256sum -c <(jq -r '.files | to_entries[] | "
        "\"\\(.value)  \\(.key)\"' manifest.json)\n"
    )


def _write_tarball(path: Path, *, entries: list[tuple[str, bytes]]) -> None:
    """Write entries into a tar.gz at `path`. Deterministic-ish: fixed mtime,
    fixed uid/gid/user/group, sorted on read order (caller is responsible for
    passing entries in stable order)."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = _FIXED_MTIME
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(data))
