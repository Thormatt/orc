"""Embedder protocol tests: registry, factory hook, availability, errors."""

from __future__ import annotations

import pytest

from orc.errors import EmbeddingsUnavailableError, OrcError
from orc.retrieval import embedder as embedder_module
from orc.retrieval.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    embedder_available,
    get_embedder,
    model_dim,
    set_embedder_factory,
)
from tests._fake_embedder import FakeEmbedder


def test_registry_knows_default_model_dim_without_loading() -> None:
    assert model_dim(DEFAULT_EMBEDDING_MODEL) == 384


def test_registry_returns_none_for_unknown_model() -> None:
    assert model_dim("not/a-model") is None


def test_get_embedder_uses_factory_and_caches() -> None:
    fake = FakeEmbedder(dim=8)
    set_embedder_factory(lambda model_id: fake)
    try:
        first = get_embedder("any-model")
        second = get_embedder("any-model")
        assert first is fake
        assert second is fake
    finally:
        set_embedder_factory(None)


def test_get_embedder_raises_with_install_hint_when_deps_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_embedder_factory(None)
    monkeypatch.setattr(embedder_module, "find_spec", lambda name: None)
    with pytest.raises(EmbeddingsUnavailableError, match=r'pip install "orc-ai\[embeddings\]"'):
        get_embedder(DEFAULT_EMBEDDING_MODEL)


def test_embeddings_unavailable_error_is_orc_error() -> None:
    assert issubclass(EmbeddingsUnavailableError, OrcError)


def test_embedder_available_false_when_find_spec_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedder_module, "find_spec", lambda name: None)
    assert embedder_available() is False


def test_fake_embedder_is_deterministic_and_scriptable() -> None:
    fake = FakeEmbedder(dim=8, vocabulary={"skills": 2})
    [a] = fake.embed_texts(["the skills api"])
    [b] = fake.embed_texts(["SKILLS everywhere"])
    assert a == b
    assert a[2] == 1.0
    [unrelated] = fake.embed_texts(["kubernetes pods"])
    assert unrelated != a
