"""Retrieval primitives. Pure functions over a sqlite connection."""

from orc.retrieval.bm25 import RetrievedChunk, bm25_search
from orc.retrieval.hybrid import RetrievalResult, retrieve, rrf_fuse, vector_search

__all__ = [
    "RetrievalResult",
    "RetrievedChunk",
    "bm25_search",
    "retrieve",
    "rrf_fuse",
    "vector_search",
]
