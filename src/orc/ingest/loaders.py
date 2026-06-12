"""File and URL loaders. Each returns a `LoadedDoc` with raw bytes + extracted text.

Supported formats: the text mimes in SUPPORTED_TEXT_MIMES (markdown, plain text,
HTML, reST, JSON) plus application/pdf, whose text is extracted with pypdf.
Scanned/image-only PDFs are rejected — OCR is not supported.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import mimetypes
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader

from orc import __version__

MAX_URL_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 5

PDF_MIME = "application/pdf"

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
    if mime == PDF_MIME:
        text, pdf_title = _extract_pdf(raw_bytes, source=str(path))
        return LoadedDoc(
            source_uri=str(path.resolve()),
            title=pdf_title or _extract_title(text, fallback=path.stem),
            mime_type=mime,
            text=text,
            raw_bytes=raw_bytes,
        )
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


def _resolve_public_ip(url: str) -> str:
    """Reject anything that isn't a public http(s) endpoint; return the vetted IP.

    Closes SSRF: only http/https, and the host must not resolve to a private,
    loopback, link-local, multicast, reserved, or unspecified address (e.g. cloud
    metadata at 169.254.169.254, localhost, 10.x/192.168.x internal services).
    `not is_global` additionally covers ranges the explicit flags miss, such as
    CGNAT 100.64.0.0/10 — but is_global alone is not enough (IPv4 multicast is
    "global" per IANA), so the explicit flags stay.

    Returns the first vetted address so the caller can connect to it directly.
    Validating here and then letting httpx re-resolve the hostname would leave
    a DNS rebinding / TOCTOU window where a low-TTL record passes validation
    public and re-resolves private at connect time.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Refusing to ingest non-HTTP(S) URL: {url!r}")
    if parsed.username or parsed.password:
        # IP pinning rewrites the netloc, which would silently drop userinfo
        # (and with it the Basic auth header httpx used to derive from it).
        raise ValueError(f"Refusing to ingest URL with embedded credentials: {url!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrinfos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host for URL: {url!r}") from exc
    if not addrinfos:
        raise ValueError(f"Could not resolve host for URL: {url!r}")
    for info in addrinfos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        ):
            raise ValueError(
                f"Refusing to ingest URL resolving to non-public address {ip}: {url!r}"
            )
    return str(ipaddress.ip_address(addrinfos[0][4][0]))


def _get_pinned(http: httpx.Client, url: str, pinned_ip: str) -> httpx.Response:
    """GET `url` but connect to the already-vetted `pinned_ip`, not the hostname.

    httpx performs its own DNS resolution at connect time, so requesting the
    bare hostname would reopen the rebinding window that _resolve_public_ip
    closed. The hostname still travels in the Host header (virtual hosting)
    and, for https, in the sni_hostname extension so TLS SNI and certificate
    verification target the original hostname rather than the IP literal.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    ip_literal = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    netloc = ip_literal if parsed.port is None else f"{ip_literal}:{parsed.port}"
    pinned_url = parsed._replace(netloc=netloc).geturl()
    host_literal = f"[{host}]" if ":" in host else host
    host_header = host_literal if parsed.port is None else f"{host_literal}:{parsed.port}"
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else None
    return http.get(pinned_url, headers={"Host": host_header}, extensions=extensions)


def load_url(
    url: str,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> LoadedDoc:
    # `transport` exists so tests can inject httpx.MockTransport instead of
    # hitting the network; production callers leave it as None.
    #
    # Follow redirects manually so every hop is re-validated against SSRF — a
    # public URL that 302s to 169.254.169.254 must still be blocked.
    current = url
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": f"orc/{__version__}"},
        transport=transport,
    ) as http:
        for _ in range(MAX_REDIRECTS + 1):
            pinned_ip = _resolve_public_ip(current)
            response = _get_pinned(http, current, pinned_ip)
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
    if mime == PDF_MIME:
        text, pdf_title = _extract_pdf(raw_bytes, source=url)
        return LoadedDoc(
            source_uri=url,
            title=pdf_title or _extract_title(text, fallback=url),
            mime_type=mime,
            text=text,
            raw_bytes=raw_bytes,
        )
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


def _extract_pdf(raw_bytes: bytes, *, source: str) -> tuple[str, str | None]:
    """Extract (text, metadata /Title) from a PDF in a single parse.

    Pages are joined with blank lines, skipping empty ones. The metadata title
    is surfaced because PDF corpora (credit memos, contracts) rarely contain
    the markdown-style headings _extract_title scans for.
    """
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        # Owner-password-locked PDFs with an empty user password (common for
        # distributed contracts/memos) open with decrypt(""); only PDFs that
        # truly require a password are refused.
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError(f"Could not extract text from PDF (encrypted): {source}")
        page_texts = (page.extract_text() for page in reader.pages)
        text = "\n\n".join(page_text for page_text in page_texts if page_text.strip())
        meta = reader.metadata
        title = (meta.title or "").strip() if meta is not None else ""
    except ValueError:
        raise
    except Exception as exc:
        # pypdf raises its own hierarchy (PdfReadError, PdfStreamError, ...);
        # callers should see one stable, actionable error type instead.
        raise ValueError(f"Could not extract text from PDF: {source} ({exc})") from exc
    if not text:
        # Silently ingesting an empty corpus would produce confident
        # not_found verdicts downstream, so refuse loudly instead.
        raise ValueError(
            "Could not extract text from PDF (scanned/image-only? "
            f"OCR is not supported): {source}"
        )
    return text, title or None


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
