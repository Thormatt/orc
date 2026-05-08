"""Top-level ingest functions. Coordinate loading, chunking, and storage."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from orc.core.clock import now_iso
from orc.core.ids import new_chunk_id, new_evidence_id
from orc.errors import IngestError
from orc.ingest.chunker import chunk_text
from orc.ingest.loaders import LoadedDoc, load_file, load_url, sha256_bytes
from orc.paths import workspace_db_path, workspace_evidence_dir
from orc.storage.db import open_connection, transaction
from orc.storage.workspace import Workspace

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
_MIME_EXT = {
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/x-rst": ".rst",
    "application/json": ".json",
}


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def ingest(
    workspace: Workspace,
    source: str,
    *,
    recursive: bool = True,
) -> list[str]:
    """Ingest a path or URL. Returns evidence_ids of newly added items.

    Idempotent on sha256: re-ingesting unchanged content is a no-op.
    """
    if is_url(source):
        return _ingest_one(workspace, load_url(source))

    path = Path(source).expanduser()
    if not path.exists():
        raise IngestError(f"Path not found: {source}")
    path = path.resolve()

    out: list[str] = []
    if path.is_file():
        out.extend(_ingest_one(workspace, load_file(path)))
    elif path.is_dir():
        for child in _iter_files(path, recursive=recursive):
            try:
                doc = load_file(child)
            except (ValueError, OSError):
                continue
            out.extend(_ingest_one(workspace, doc))
    return out


def _ingest_one(workspace: Workspace, doc: LoadedDoc) -> list[str]:
    db_path = workspace_db_path(workspace.name)
    sha = sha256_bytes(doc.raw_bytes)

    with open_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT evidence_id FROM evidence WHERE sha256 = ?", (sha,)
        ).fetchone()
        if existing is not None:
            return []

        evidence_id = new_evidence_id()
        ext = _MIME_EXT.get(doc.mime_type, ".bin")
        stored_path = workspace_evidence_dir(workspace.name) / f"{evidence_id}{ext}"
        stored_path.write_bytes(doc.raw_bytes)

        chunks = chunk_text(doc.text)

        with transaction(conn):
            conn.execute(
                "UPDATE workspace SET corpus_version = corpus_version + 1 WHERE name = ?",
                (workspace.name,),
            )
            new_corpus_version = conn.execute(
                "SELECT corpus_version FROM workspace WHERE name = ?", (workspace.name,)
            ).fetchone()["corpus_version"]

            conn.execute(
                "INSERT INTO evidence(evidence_id, source_path, stored_path, sha256, mime_type, "
                "title, ingested_at, corpus_version) VALUES (?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    doc.source_uri,
                    str(stored_path),
                    sha,
                    doc.mime_type,
                    doc.title,
                    now_iso(),
                    new_corpus_version,
                ),
            )
            for c in chunks:
                conn.execute(
                    "INSERT INTO chunk(chunk_id, evidence_id, seq, text, token_count, "
                    "headings_path, start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        new_chunk_id(),
                        evidence_id,
                        c.seq,
                        c.text,
                        c.token_count,
                        c.headings_path,
                        c.start_offset,
                        c.end_offset,
                    ),
                )

        return [evidence_id]


def _iter_files(root: Path, *, recursive: bool) -> Iterator[Path]:
    if recursive:
        for child in sorted(root.rglob("*")):
            if child.is_file() and not _should_skip(child, root):
                yield child
    else:
        for child in sorted(root.iterdir()):
            if child.is_file() and not _should_skip(child, root):
                yield child


def _should_skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts)
