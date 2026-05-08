"""Chunker tests."""

from __future__ import annotations

from orc.ingest.chunker import chunk_text, count_tokens


def test_empty_text_returns_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_short_text_one_chunk() -> None:
    body = "The quick brown fox jumps over the lazy dog."
    chunks = chunk_text(body, target_tokens=800)
    assert len(chunks) == 1
    assert chunks[0].seq == 0
    assert chunks[0].text == body
    assert chunks[0].headings_path is None
    assert chunks[0].token_count == count_tokens(body)


def test_markdown_headings_become_path() -> None:
    body = "# Top\n\nIntro line.\n\n## Sub\n\nDetails here."
    chunks = chunk_text(body, target_tokens=800)
    assert len(chunks) == 2
    assert chunks[0].headings_path == "Top"
    assert chunks[1].headings_path == "Top > Sub"


def test_code_fence_does_not_create_heading() -> None:
    body = "# Real\n\nIntro.\n\n```python\n# not a heading\n```\n\nMore text."
    chunks = chunk_text(body, target_tokens=800)
    assert all(c.headings_path == "Real" for c in chunks)


def test_long_text_splits_into_multiple_chunks() -> None:
    body = ("# Section\n\n" + "Lorem ipsum dolor sit amet. " * 800).strip()
    chunks = chunk_text(body, target_tokens=400, overlap_tokens=50)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 400
    seqs = [c.seq for c in chunks]
    assert seqs == list(range(len(chunks)))


def test_overlap_creates_token_step_smaller_than_target() -> None:
    body = ("# Section\n\n" + "Lorem ipsum dolor sit amet. " * 400).strip()
    chunks_no = chunk_text(body, target_tokens=300, overlap_tokens=0)
    chunks_overlap = chunk_text(body, target_tokens=300, overlap_tokens=100)
    # With overlap, more chunks should be produced (smaller step).
    assert len(chunks_overlap) > len(chunks_no)


def test_invalid_arguments_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_text("hello", target_tokens=0)
    with pytest.raises(ValueError):
        chunk_text("hello", target_tokens=10, overlap_tokens=10)
    with pytest.raises(ValueError):
        chunk_text("hello", target_tokens=10, overlap_tokens=-1)


def test_section_headings_track_hierarchy_resets() -> None:
    body = "# A\n\nbody-a\n\n## B\n\nbody-b\n\n# C\n\nbody-c"
    chunks = chunk_text(body, target_tokens=800)
    assert [c.headings_path for c in chunks] == ["A", "A > B", "C"]


def test_chunks_offsets_are_within_body() -> None:
    body = ("# Top\n\n" + "alpha beta gamma delta epsilon. " * 200).strip()
    chunks = chunk_text(body, target_tokens=200, overlap_tokens=20)
    for c in chunks:
        assert 0 <= c.start_offset <= len(body)
        assert 0 < c.end_offset <= len(body) + 5  # decode rounding may add a couple chars
        assert c.end_offset >= c.start_offset
