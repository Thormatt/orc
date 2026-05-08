"""File and URL loaders. Each returns a `LoadedDoc` with raw bytes + decoded text."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import httpx

SUPPORTED_TEXT_MIMES = {
    "text/markdown",
    "text/x-markdown",
    "text/plain",
    "text/html",
    "text/x-rst",
    "application/json",
}


@dataclass(frozen=True)
class LoadedDoc:
    source_uri: str
    title: str | None
    mime_type: str
    text: str
    raw_bytes: bytes


def load_file(path: Path) -> LoadedDoc:
    raw_bytes = path.read_bytes()
    mime = _guess_mime(path)
    if mime not in SUPPORTED_TEXT_MIMES and not mime.startswith("text/"):
        raise ValueError(f"Unsupported file type for ingest: {mime} ({path})")
    text = raw_bytes.decode("utf-8", errors="replace")
    return LoadedDoc(
        source_uri=str(path.resolve()),
        title=_extract_title(text, fallback=path.stem),
        mime_type=mime,
        text=text,
        raw_bytes=raw_bytes,
    )


def load_url(url: str, *, timeout: float = 30.0) -> LoadedDoc:
    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "orc/0.1.0"},
    )
    response.raise_for_status()
    raw_bytes = response.content
    mime = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    if mime not in SUPPORTED_TEXT_MIMES and not mime.startswith("text/"):
        raise ValueError(f"Unsupported URL content-type for ingest: {mime} ({url})")
    text = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
    return LoadedDoc(
        source_uri=url,
        title=_extract_title(text, fallback=url),
        mime_type=mime,
        text=text,
        raw_bytes=raw_bytes,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime is not None:
        return mime
    ext = path.suffix.lower()
    if ext in (".md", ".markdown", ".mdown"):
        return "text/markdown"
    if ext in (".txt", ".text"):
        return "text/plain"
    if ext in (".rst",):
        return "text/x-rst"
    if ext in (".json",):
        return "application/json"
    return "application/octet-stream"


def _extract_title(text: str, *, fallback: str) -> str | None:
    for line in text.splitlines()[:50]:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return fallback or None
