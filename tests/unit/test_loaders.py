"""Loader tests. URL tests stub DNS and inject a mock transport — no real network I/O."""

from __future__ import annotations

import io
import socket
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from orc.ingest.loaders import load_file, load_url, sha256_bytes

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "151.101.1.1"


def _addrinfo(ip: str, port: int) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]


def _text_response(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/plain"}, text=body)


def _pdf_bytes(*page_texts: str | None, title: str | None = None) -> bytes:
    """Hand-rolled minimal one-object-per-page PDF so tests need no binary fixture.

    Each entry in `page_texts` becomes one page; None produces a page with an
    empty content stream, mimicking a scanned/image-only page that pypdf
    extracts as "". Streams are uncompressed so the whole file stays tiny.
    """
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1"))
        return len(objects)

    catalog_num = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_num = add("PLACEHOLDER")  # patched below once page object numbers exist
    font_num = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    kid_nums = []
    for text in page_texts:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text is not None else ""
        content_num = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        kid_nums.append(
            add(
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
                f"/Contents {content_num} 0 R >>"
            )
        )

    kids = " ".join(f"{n} 0 R" for n in kid_nums)
    objects[pages_num - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(kid_nums)} >>".encode("latin-1")
    )
    info_num = add(f"<< /Title ({title}) >>") if title is not None else None

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{num} 0 obj\n".encode("latin-1") + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    trailer = f"<< /Size {len(objects) + 1} /Root {catalog_num} 0 R"
    if info_num is not None:
        trailer += f" /Info {info_num} 0 R"
    trailer += " >>"
    out.write(b"trailer\n" + trailer.encode("latin-1"))
    out.write(f"\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1"))
    return out.getvalue()


def test_load_pdf_joins_pages_with_blank_line_and_skips_empty_pages(
    tmp_path: Path,
) -> None:
    p = tmp_path / "multi.pdf"
    p.write_bytes(_pdf_bytes("Page one", None, "Page three"))
    doc = load_file(p)
    assert doc.text == "Page one\n\nPage three"


def test_load_pdf_with_no_extractable_text_raises_mentioning_ocr(
    tmp_path: Path,
) -> None:
    # A scanned/image-only PDF extracts as empty text. Ingesting it silently
    # would yield an empty corpus and confident not_found verdicts downstream.
    p = tmp_path / "scanned.pdf"
    p.write_bytes(_pdf_bytes(None, None))
    with pytest.raises(ValueError, match="OCR"):
        load_file(p)


def test_load_pdf_unparseable_raises_value_error(tmp_path: Path) -> None:
    # pypdf internals (PdfStreamError etc.) must not leak to callers.
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(ValueError, match="Could not extract text from PDF"):
        load_file(p)


def test_load_pdf_encrypted_raises_value_error(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    p = tmp_path / "locked.pdf"
    p.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match="Could not extract text from PDF"):
        load_file(p)


def test_load_pdf_owner_locked_with_empty_user_password_ingests(tmp_path: Path) -> None:
    # A large share of real-world contracts/credit memos are owner-password-
    # locked but openable with an empty user password — pypdf decrypts them
    # with decrypt(""). Rejecting those would fail ingest on exactly the
    # document class PDF support targets.
    writer = PdfWriter(clone_from=io.BytesIO(_pdf_bytes("Owner locked body")))
    writer.encrypt(user_password="", owner_password="owner-secret")
    buf = io.BytesIO()
    writer.write(buf)
    p = tmp_path / "owner-locked.pdf"
    p.write_bytes(buf.getvalue())
    doc = load_file(p)
    assert "Owner locked body" in doc.text


def test_load_pdf_prefers_metadata_title_over_fallback(tmp_path: Path) -> None:
    p = tmp_path / "scan-target.pdf"
    p.write_bytes(_pdf_bytes("Some body text", title="Q3 Credit Memo"))
    doc = load_file(p)
    assert doc.title == "Q3 Credit Memo"


def test_load_pdf_extracts_text_and_falls_back_to_stem_title(tmp_path: Path) -> None:
    raw = _pdf_bytes("Hello orc PDF")
    p = tmp_path / "credit-memo.pdf"
    p.write_bytes(raw)
    doc = load_file(p)
    assert doc.mime_type == "application/pdf"
    assert "Hello orc PDF" in doc.text
    assert doc.title == "credit-memo"
    assert doc.raw_bytes == raw


def test_load_markdown_extracts_h1_title(tmp_path: Path) -> None:
    p = tmp_path / "doc.md"
    p.write_text("# My Title\n\nSome body content.\n")
    doc = load_file(p)
    assert doc.title == "My Title"
    assert doc.mime_type == "text/markdown"
    assert "Some body content" in doc.text


def test_load_plaintext_falls_back_to_filename(tmp_path: Path) -> None:
    p = tmp_path / "notes.txt"
    p.write_text("just some plain text without a heading")
    doc = load_file(p)
    assert doc.title == "notes"
    assert doc.mime_type == "text/plain"


def test_load_unsupported_extension_raises(tmp_path: Path) -> None:
    p = tmp_path / "binary.exe"
    p.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        load_file(p)


def test_sha256_is_stable() -> None:
    assert sha256_bytes(b"hello") == sha256_bytes(b"hello")
    assert sha256_bytes(b"hello") != sha256_bytes(b"world")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://localhost:8080/admin",
        "http://127.0.0.1/secret",
        "http://[::1]/secret",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://0.0.0.0/",
    ],
)
def test_load_url_refuses_ssrf_targets(url: str) -> None:
    # Must reject before any network call: bad scheme, or a host resolving to a
    # private / loopback / link-local / unspecified address.
    with pytest.raises(ValueError):
        load_url(url)


def test_load_url_pdf_content_type_extracts_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: _addrinfo(PUBLIC_IP, port)
    )
    raw = _pdf_bytes("Hello orc PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=raw
        )

    doc = load_url("http://example.com/memo.pdf", transport=httpx.MockTransport(handler))

    assert doc.mime_type == "application/pdf"
    assert "Hello orc PDF" in doc.text
    assert doc.raw_bytes == raw
    assert doc.source_uri == "http://example.com/memo.pdf"


def test_load_url_pins_connection_to_validated_ip_with_original_host_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The validated IP must be what we actually connect to; the hostname only
    # travels in the Host header. Otherwise httpx re-resolves DNS at connect
    # time and a rebinding record can swap in a private address (TOCTOU).
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: _addrinfo(PUBLIC_IP, port)
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _text_response("pinned body")

    doc = load_url("http://example.com/doc.txt", transport=httpx.MockTransport(handler))

    assert len(seen) == 1
    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].headers["host"] == "example.com"
    assert doc.text == "pinned body"
    assert doc.source_uri == "http://example.com/doc.txt"


def test_load_url_https_sets_sni_to_original_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Connecting to the IP literal must not break TLS: SNI and certificate
    # verification still need to target the original hostname.
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: _addrinfo(PUBLIC_IP, port)
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _text_response("ok")

    load_url("https://example.com/doc.txt", transport=httpx.MockTransport(handler))

    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].extensions.get("sni_hostname") == "example.com"


def test_load_url_dns_rebinding_cannot_redirect_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate a rebinding attack: the first resolution is public (passes
    # validation), every later one flips to loopback. The request must go to
    # the vetted IP, and the hostname must never be resolved a second time.
    calls: list[str] = []

    def rebinding_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
        calls.append(host)
        ip = PUBLIC_IP if len(calls) == 1 else "127.0.0.1"
        return _addrinfo(ip, port)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _text_response("ok")

    load_url("http://rebind.attacker.test/", transport=httpx.MockTransport(handler))

    assert calls == ["rebind.attacker.test"]
    assert seen[0].url.host == PUBLIC_IP


def test_load_url_rejects_cgnat_shared_address_space() -> None:
    # 100.64.0.0/10 is not "private" per ipaddress, but it is not globally
    # routable either — is_global=False must be enough to reject it.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent to a CGNAT address")

    with pytest.raises(ValueError, match="non-public"):
        load_url("http://100.64.1.1/", transport=httpx.MockTransport(handler))


def test_load_url_rejects_urls_with_embedded_credentials() -> None:
    # Pinning rewrites the netloc to the vetted IP, which would silently drop
    # userinfo (httpx previously turned it into a Basic auth header). Refuse
    # loudly rather than regress to an unexplained 401.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent for a credentialed URL")

    with pytest.raises(ValueError, match="credentials"):
        load_url("http://user:secret@example.com/", transport=httpx.MockTransport(handler))


def test_load_url_redirect_hops_are_revalidated_and_pinned_per_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every redirect hop must get its own validation + pinning: a fresh DNS
    # check per hostname, and the connection made to that hop's vetted IP.
    ips = {"example.com": PUBLIC_IP, "cdn.example.net": OTHER_PUBLIC_IP}
    resolved: list[str] = []

    def fake_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
        resolved.append(host)
        return _addrinfo(ips[host], port)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == PUBLIC_IP:
            return httpx.Response(302, headers={"location": "http://cdn.example.net/final.txt"})
        return _text_response("final body")

    doc = load_url("http://example.com/start", transport=httpx.MockTransport(handler))

    assert resolved == ["example.com", "cdn.example.net"]
    assert [r.url.host for r in seen] == [PUBLIC_IP, OTHER_PUBLIC_IP]
    assert [r.headers["host"] for r in seen] == ["example.com", "cdn.example.net"]
    assert doc.text == "final body"
