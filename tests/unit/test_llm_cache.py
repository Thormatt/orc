"""Prompt-cache assembly tests."""

from __future__ import annotations

from orc.llm.cache import build_verify_messages, format_corpus
from orc.retrieval import RetrievedChunk


def _chunk(chunk_id: str, text: str = "x", title: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        evidence_id="ev1",
        seq=0,
        text=text,
        headings_path=None,
        token_count=len(text.split()),
        rank=0,
        bm25_score=-1.0,
        evidence_title=title,
        evidence_source_path="/tmp/x",
    )


def test_format_corpus_sorts_by_chunk_id() -> None:
    chunks = [_chunk("zzz"), _chunk("aaa"), _chunk("mmm")]
    corpus = format_corpus(chunks)
    aaa_idx = corpus.find('id="aaa"')
    mmm_idx = corpus.find('id="mmm"')
    zzz_idx = corpus.find('id="zzz"')
    assert 0 < aaa_idx < mmm_idx < zzz_idx


def test_format_corpus_is_byte_stable_for_same_chunks() -> None:
    a = format_corpus([_chunk("a"), _chunk("b"), _chunk("c")])
    b = format_corpus([_chunk("c"), _chunk("a"), _chunk("b")])
    assert a == b


def test_format_corpus_escapes_quotes_in_metadata() -> None:
    chunk = _chunk("a", title='He said "hi"')
    corpus = format_corpus([chunk])
    assert '"hi"' not in corpus  # raw quotes were escaped
    assert "&quot;" in corpus


def test_build_verify_messages_places_breakpoint_before_claim() -> None:
    payload = build_verify_messages(
        system_prompt="rules", corpus_block="<corpus></corpus>", claim="x is y"
    )
    assert payload["system"][0] == {"type": "text", "text": "rules"}
    assert payload["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert payload["system"][1]["text"] == "<corpus></corpus>"
    assert payload["messages"][0]["role"] == "user"
    assert "x is y" in payload["messages"][0]["content"]
