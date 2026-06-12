"""Embedding model access for hybrid retrieval.

The model is pinned per workspace (workspace.embedding_model column) — there is
deliberately NO env var override at retrieval time, because the workspace column
is the replay-pinned truth: a frozen replay must embed with the same model the
original run used.

sentence-transformers (and its torch dependency) is heavyweight, so the import
is lazy and everything that only needs the dimension consults the module-level
registry instead of loading the model.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.util import find_spec
from typing import Protocol

from orc.errors import EmbeddingsUnavailableError

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Known model dims, so callers can size chunk_vec without loading torch.
_MODEL_DIMS: dict[str, int] = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
}

_INSTALL_HINT = 'pip install "orc-ai[embeddings]"'


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Real embedder. Lazy-imports sentence_transformers so `orc` stays light
    for users who never opt into embeddings."""

    def __init__(self, model_id: str = DEFAULT_EMBEDDING_MODEL) -> None:
        try:
            import sentence_transformers
        except ImportError as exc:
            raise EmbeddingsUnavailableError(
                f"sentence-transformers is not installed; run: {_INSTALL_HINT}"
            ) from exc
        self.model_id = model_id
        self._model = sentence_transformers.SentenceTransformer(model_id)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # Normalized embeddings make L2 distance rank-equivalent to cosine.
        return self._model.encode(texts, normalize_embeddings=True).tolist()


_factory: Callable[[str], Embedder] | None = None
_cache: dict[str, Embedder] = {}


def model_dim(model_id: str) -> int | None:
    """Dimension for a known model id, without loading the model."""
    return _MODEL_DIMS.get(model_id)


def embedder_available() -> bool:
    return find_spec("sentence_transformers") is not None


def get_embedder(model_id: str) -> Embedder:
    """Return a (cached) embedder for the model id.

    Raises EmbeddingsUnavailableError with an install hint when the optional
    dependency is missing, so callers can decide between fail-loud (ingest)
    and warn-and-fall-back (retrieval).
    """
    if model_id in _cache:
        return _cache[model_id]
    if _factory is not None:
        embedder = _factory(model_id)
    elif not embedder_available():
        raise EmbeddingsUnavailableError(
            f"Embedding model {model_id!r} requested but sentence-transformers "
            f"is not installed; run: {_INSTALL_HINT}"
        )
    else:
        embedder = SentenceTransformerEmbedder(model_id)
    _cache[model_id] = embedder
    return embedder


def set_embedder_factory(factory: Callable[[str], Embedder] | None) -> None:
    """Test hook. Pass None to clear. Clears the cache either way."""
    global _factory
    _factory = factory
    _cache.clear()
