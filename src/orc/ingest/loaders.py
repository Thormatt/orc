"""File and URL loaders. Each returns a `LoadedDoc` with raw bytes + decoded text."""

from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

MAX_URL_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 5

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


def _assert_public_http_url(url: str) -> None:
    """Reject anything that isn't a public http(s) endpoint.

    Closes SSRF: only http/https, and the host must not resolve to a private,
    loopback, link-local, multicast, reserved, or unspecified address (e.g. cloud
    metadata at 169.254.169.254, localhost, 10.x/192.168.x internal services).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Refusing to ingest non-HTTP(S) URL: {url!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrinfos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host for URL: {url!r}") from exc
    for info in addrinfos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Refusing to ingest URL resolving to non-public address {ip}: {url!r}"
            )


def load_url(url: str, *, timeout: float = 30.0) -> LoadedDoc:
    # Follow redirects manually so every hop is re-validated against SSRF — a
    # public URL that 302s to 169.254.169.254 must still be blocked.
    current = url
    with httpx.Client(
        timeout=timeout, follow_redirects=False, headers={"User-Agent": "orc/0.1.0"}
    ) as http:
        for _ in range(MAX_REDIRECTS + 1):
            _assert_public_http_url(current)
            response = http.get(current)
            location = response.headers.get("location")
            if response.is_redirect and location:
                current = urljoin(current, location)
                continue
            break
        else:
            raise ValueError(f"Too many redirects for URL: {url!r}")
    response.raise_for_status()
    raw_bytes = response.content
    if len(raw_bytes) > MAX_URL_BYTES:
        raise ValueError(f"URL response exceeds {MAX_URL_BYTES} byte limit: {url!r}")
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
