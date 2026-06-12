"""Token-bounded chunker with markdown heading awareness.

Strategy:
1. Walk the body line-by-line, building a heading index (offset -> heading path).
   Code-fenced regions are skipped, so `# example` inside ``` ``` is not a heading.
2. Split the body into sections at heading boundaries.
3. For each section, if the body fits in `target_tokens`, emit one chunk; otherwise
   slide a token window across with `step = target_tokens - overlap_tokens`.

Window boundaries are computed at the byte level and snapped forward to UTF-8
character starts. cl100k_base is byte-level BPE, so a token boundary can fall
inside a multi-byte character (routine for CJK/emoji); decoding each window
independently would inject U+FFFD (tiktoken decodes with errors='replace') and
make char offsets drift. Byte slices snapped to char starts decode strictly and
keep `body[start_offset:end_offset] == chunk.text` exact.

Tokenization uses tiktoken cl100k_base. The Anthropic tokenizer is similar within ~5%
for sizing purposes; for billing-accurate counts at LLM-call time we use the API's
token usage instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import accumulate

import tiktoken

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CODE_FENCE_RE = re.compile(r"^(```|~~~)")
_TOKENIZER_NAME = "cl100k_base"

_encoder: tiktoken.Encoding | None = None


def _enc() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding(_TOKENIZER_NAME)
    return _encoder


def count_tokens(text: str) -> int:
    return len(_enc().encode(text))


@dataclass(frozen=True)
class Chunk:
    seq: int
    text: str
    token_count: int
    headings_path: str | None
    start_offset: int
    end_offset: int


def chunk_text(
    body: str,
    *,
    target_tokens: int = 800,
    overlap_tokens: int = 100,
) -> list[Chunk]:
    """Chunk text or markdown into token-bounded chunks."""
    if target_tokens <= 0:
        raise ValueError("target_tokens must be > 0")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be in [0, target_tokens)")
    if not body or not body.strip():
        return []

    out: list[Chunk] = []
    for s_start, _s_end, path, s_body in _iter_sections(body):
        out.extend(
            _chunk_section(
                s_body,
                base_offset=s_start,
                headings_path=path,
                target_tokens=target_tokens,
                overlap_tokens=overlap_tokens,
            )
        )
    return [
        Chunk(
            seq=i,
            text=c.text,
            token_count=c.token_count,
            headings_path=c.headings_path,
            start_offset=c.start_offset,
            end_offset=c.end_offset,
        )
        for i, c in enumerate(out)
    ]


def _iter_sections(body: str) -> Iterator[tuple[int, int, str | None, str]]:
    index = _build_heading_index(body)

    if not index:
        yield (0, len(body), None, body)
        return

    first_heading_offset = index[0][0]
    if first_heading_offset > 0:
        pre_body = body[:first_heading_offset]
        if pre_body.strip():
            yield (0, first_heading_offset, None, pre_body)

    for i, (offset, path) in enumerate(index):
        next_offset = index[i + 1][0] if i + 1 < len(index) else len(body)
        section_body = body[offset:next_offset]
        if not section_body.strip():
            continue
        path_str = " > ".join(p for p in path if p)
        yield (offset, next_offset, path_str or None, section_body)


def _build_heading_index(body: str) -> list[tuple[int, list[str]]]:
    """Return [(offset_of_heading_line, headings_path_after_this_heading)]."""
    out: list[tuple[int, list[str]]] = []
    stack: list[str] = []
    in_fence = False
    offset = 0

    for raw_line in body.splitlines(keepends=True):
        line = raw_line.rstrip("\n").rstrip("\r")
        stripped = line.strip()

        if _CODE_FENCE_RE.match(stripped):
            in_fence = not in_fence
            offset += len(raw_line)
            continue

        if not in_fence:
            m = _HEADING_RE.match(line)
            if m:
                depth = len(m.group(1))
                title = m.group(2).strip()
                stack = stack[: depth - 1]
                while len(stack) < depth - 1:
                    stack.append("")
                stack.append(title)
                out.append((offset, list(stack)))

        offset += len(raw_line)

    return out


def _snap_to_char_start(data: bytes, pos: int) -> int:
    """Advance pos past UTF-8 continuation bytes (0b10xxxxxx).

    Token boundaries can land mid-character; snapping forward to the next
    character start lets adjacent windows tile without splitting any character.
    """
    while pos < len(data) and (data[pos] & 0xC0) == 0x80:
        pos += 1
    return pos


def _stripped_span(text: str) -> tuple[int, int]:
    """Return (start, end) of text.strip() within text, so offsets match the
    stripped chunk text exactly instead of the raw decoded window."""
    leading = len(text) - len(text.lstrip())
    return leading, leading + len(text.strip())


def _chunk_section(
    section_body: str,
    *,
    base_offset: int,
    headings_path: str | None,
    target_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    enc = _enc()
    tokens = enc.encode(section_body)
    if not tokens:
        return []

    if len(tokens) <= target_tokens:
        text_start, text_end = _stripped_span(section_body)
        return [
            Chunk(
                seq=0,
                text=section_body.strip(),
                token_count=len(tokens),
                headings_path=headings_path,
                start_offset=base_offset + text_start,
                end_offset=base_offset + text_end,
            )
        ]

    section_bytes = section_body.encode("utf-8")
    # cl100k_base byte-level BPE round-trips bytes exactly, so cumulative
    # per-token byte lengths give each window's byte span without re-decoding
    # full prefixes (which would be O(n^2) and inject U+FFFD at split chars).
    byte_starts = list(
        accumulate((len(enc.decode_single_token_bytes(t)) for t in tokens), initial=0)
    )

    out: list[Chunk] = []
    step = max(1, target_tokens - overlap_tokens)
    prev_byte = 0
    prev_char = 0
    i = 0
    while i < len(tokens):
        window_end = min(i + target_tokens, len(tokens))
        start_byte = _snap_to_char_start(section_bytes, byte_starts[i])
        end_byte = _snap_to_char_start(section_bytes, byte_starts[window_end])
        # Boundaries sit on character starts, so a strict decode failing here
        # would be a bug in the snapping logic — let it raise.
        window_text = section_bytes[start_byte:end_byte].decode("utf-8")
        chunk_text_value = window_text.strip()
        if not chunk_text_value:
            i += step
            continue
        start_char = prev_char + len(section_bytes[prev_byte:start_byte].decode("utf-8"))
        prev_byte, prev_char = start_byte, start_char
        text_start, text_end = _stripped_span(window_text)
        out.append(
            Chunk(
                seq=len(out),
                text=chunk_text_value,
                token_count=window_end - i,
                headings_path=headings_path,
                start_offset=base_offset + start_char + text_start,
                end_offset=base_offset + start_char + text_end,
            )
        )
        if i + target_tokens >= len(tokens):
            break
        i += step
    return out
