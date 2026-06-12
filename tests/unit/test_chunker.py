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


def _cjk_emoji_body() -> str:
    """Multi-byte heavy document: every char is 3-4 UTF-8 bytes, no whitespace.

    Small token windows routinely land mid-character in byte-level BPE,
    which is exactly the condition the chunker must survive.
    """
    return "".join(f"日本語のテキスト第{i}文。絵文字🎉も含まれます。" for i in range(200))


def test_cjk_emoji_chunks_contain_no_replacement_characters() -> None:
    body = _cjk_emoji_body()
    chunks = chunk_text(body, target_tokens=50, overlap_tokens=10)
    assert len(chunks) >= 2
    for c in chunks:
        assert "�" not in c.text


def test_cjk_emoji_chunk_offsets_slice_body_exactly() -> None:
    body = _cjk_emoji_body()
    chunks = chunk_text(body, target_tokens=50, overlap_tokens=10)
    assert len(chunks) >= 2
    for c in chunks:
        assert body[c.start_offset : c.end_offset] == c.text


def test_cjk_chunks_tile_without_gaps_when_no_overlap() -> None:
    body = _cjk_emoji_body()
    chunks = chunk_text(body, target_tokens=50, overlap_tokens=0)
    assert len(chunks) >= 2
    assert chunks[0].start_offset == 0
    assert chunks[-1].end_offset == len(body)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.end_offset == nxt.start_offset


def test_ascii_windowed_chunk_offsets_slice_body_exactly() -> None:
    body = ("# Section\n\n" + "Lorem ipsum dolor sit amet. " * 400).strip()
    chunks = chunk_text(body, target_tokens=100, overlap_tokens=20)
    assert len(chunks) >= 2
    for c in chunks:
        assert body[c.start_offset : c.end_offset] == c.text


def test_single_chunk_section_offsets_exclude_stripped_whitespace() -> None:
    body = "# Top\n\nIntro text.\n\n## Sub\n\nDetails here.\n"
    chunks = chunk_text(body, target_tokens=800)
    assert len(chunks) == 2
    for c in chunks:
        assert body[c.start_offset : c.end_offset] == c.text
