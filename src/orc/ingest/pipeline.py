"""Top-level ingest functions. Coordinate loading, chunking, and storage."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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

        # Chunk before any disk write so a chunker failure leaves nothing behind.
        chunks = chunk_text(doc.text)

        # Embed BEFORE the write transaction: model inference can be slow and
        # must not hold the BEGIN IMMEDIATE write lock. The vectors are then
        # inserted in the same transaction as the chunk rows (atomic).
        embeddings = _embed_chunks_for_ingest(conn, workspace=workspace, chunks=chunks)

        # Stage the evidence bytes to a temp file and only promote it into place
        # once the DB transaction commits. A failure anywhere leaves neither an
        # orphaned file nor a dangling row — the corpus stays consistent.
        tmp_path = stored_path.with_name(f"{stored_path.name}.{os.getpid()}.tmp")
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(doc.raw_bytes)
        try:
            _commit_evidence(
                conn,
                workspace=workspace,
                evidence_id=evidence_id,
                stored_path=stored_path,
                sha=sha,
                doc=doc,
                chunks=chunks,
                embeddings=embeddings,
            )
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        os.replace(tmp_path, stored_path)
        return [evidence_id]


def _embed_chunks_for_ingest(
    conn: Any,
    *,
    workspace: Workspace,
    chunks: list,
) -> list[list[float]] | None:
    """Embed chunk texts when the workspace opts into embeddings.

    Fail-loud by design: a workspace with embedding_model set has promised
    hybrid retrieval, so silently ingesting unembedded chunks would corrupt
    that promise. Missing deps surface as IngestError with an install hint.
    Also prepares chunk_vec (extension + table) before the write transaction.
    """
    if workspace.embedding_model is None or not chunks:
        return None

    from orc.errors import EmbeddingsUnavailableError
    from orc.retrieval.embedder import get_embedder
    from orc.storage.embeddings_store import (
        ensure_chunk_vec,
        load_vec_extension,
        vec_extension_available,
    )

    try:
        if not vec_extension_available():
            raise EmbeddingsUnavailableError(
                "the sqlite-vec extension is unavailable; "
                'run: pip install "orc-ai[embeddings]"'
            )
        embedder = get_embedder(workspace.embedding_model)
    except EmbeddingsUnavailableError as exc:
        raise IngestError(
            f"Workspace {workspace.name!r} requires embeddings "
            f"(embedding_model={workspace.embedding_model!r}) but they are "
            f"unavailable: {exc}"
        ) from exc

    load_vec_extension(conn)
    ensure_chunk_vec(conn, embedder.dim)
    return embedder.embed_texts([c.text for c in chunks])


def _commit_evidence(
    conn: Any,
    *,
    workspace: Workspace,
    evidence_id: str,
    stored_path: Path,
    sha: str,
    doc: LoadedDoc,
    chunks: list,
    embeddings: list[list[float]] | None = None,
) -> None:
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
        chunk_ids = [new_chunk_id() for _ in chunks]
        for chunk_id, c in zip(chunk_ids, chunks, strict=True):
            conn.execute(
                "INSERT INTO chunk(chunk_id, evidence_id, seq, text, token_count, "
                "headings_path, start_offset, end_offset) VALUES (?,?,?,?,?,?,?,?)",
                (
                    chunk_id,
                    evidence_id,
                    c.seq,
                    c.text,
                    c.token_count,
                    c.headings_path,
                    c.start_offset,
                    c.end_offset,
                ),
            )
        if embeddings is not None:
            from orc.storage.embeddings_store import store_chunk_embeddings

            store_chunk_embeddings(
                conn,
                [
                    (chunk_id, new_corpus_version, vector)
                    for chunk_id, vector in zip(chunk_ids, embeddings, strict=True)
                ],
            )


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
