"""Loader tests (file only — URL loader is exercised via http mocks if needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.ingest.loaders import load_file, load_url, sha256_bytes


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
