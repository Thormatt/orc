"""Fake embedder for tests. Deterministic keyword -> one-hot mapping.

Scripting semantic hits: pass a vocabulary mapping keyword -> dimension index.
Any text containing a vocabulary keyword embeds with 1.0 at that index, so a
query sharing the keyword lands at L2 distance 0 from the chunk. Texts with no
vocabulary hit fall back to 0.5 at a CRC32 bucket (stable across processes,
unlike Python's randomized str hash); the 0.5 magnitude guarantees a fallback
vector never equals a scripted one-hot even when the buckets collide.
"""

from __future__ import annotations

import re
import zlib


class FakeEmbedder:
    def __init__(
        self,
        dim: int = 8,
        *,
        model_id: str = "fake-embedder",
        vocabulary: dict[str, int] | None = None,
    ) -> None:
        self.model_id = model_id
        self.dim = dim
        self.vocabulary = dict(vocabulary or {})
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._embed(t) for t in texts]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        words = set(re.findall(r"\w+", text.lower()))
        hit = False
        for keyword, index in sorted(self.vocabulary.items()):
            if keyword in words:
                vec[index % self.dim] = 1.0
                hit = True
        if not hit:
            vec[zlib.crc32(text.encode("utf-8")) % self.dim] = 0.5
        return vec
