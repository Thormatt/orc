"""Loader tests. URL tests stub DNS and inject a mock transport — no real network I/O."""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from orc.ingest.loaders import load_file, load_url, sha256_bytes

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "151.101.1.1"


def _addrinfo(ip: str, port: int) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]


def _text_response(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/plain"}, text=body)


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
