"""Loader tests (file only — URL loader is exercised via http mocks if needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.ingest.loaders import load_file, sha256_bytes


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
