"""The models sit behind interfaces, chosen by config, with a dimension guard."""

from __future__ import annotations

import pytest

from autune_context import pipeline
from autune_context.config import ContextSettings, get_settings
from autune_context.constants import EMBEDDING_DIM
from autune_context.pipeline import (
    Embedder,
    NliModel,
    Reranker,
    registry,
    reset_cache,
)
from autune_context.pipeline.embedding import FakeEmbedder, KureHttpEmbedder
from autune_context.pipeline.nli import FakeNli
from autune_context.pipeline.reranking import FakeReranker


@pytest.fixture(autouse=True)
def _clean_caches():
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


@pytest.mark.parametrize(
    ("impl", "protocol"),
    [
        (FakeEmbedder, Embedder),
        (FakeReranker, Reranker),
        (FakeNli, NliModel),
    ],
)
def test_fake_implementation_satisfies_its_protocol(impl, protocol):
    assert isinstance(impl(), protocol)


def test_registry_selects_the_configured_implementation(monkeypatch):
    for knob in ("EMBEDDER", "RERANKER", "NLI"):
        monkeypatch.setenv(f"AUTUNE_CONTEXT_{knob}_IMPL", "fake")
    get_settings.cache_clear()

    assert registry.get_embedder().model_version == "fake-embedder-v1"
    assert registry.get_reranker().model_version == "fake-reranker-v1"
    assert registry.get_nli().model_version == "fake-nli-v1"


def test_unknown_implementation_string_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTUNE_CONTEXT_EMBEDDER_IMPL", "does_not_exist")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="EMBEDDER_IMPL"):
        registry.get_embedder()


def test_dimension_guard_rejects_a_mismatched_embedder(monkeypatch):
    class WrongDimEmbedder:
        def __init__(self, _settings):
            pass

        dim = EMBEDDING_DIM + 1
        model_version = "wrong-dim"

        def embed(self, texts):
            return []

    monkeypatch.setitem(registry._EMBEDDERS, "wrong_dim", WrongDimEmbedder)
    monkeypatch.setenv("AUTUNE_CONTEXT_EMBEDDER_IMPL", "wrong_dim")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="new migration"):
        registry.get_embedder()


def test_fake_embedder_is_deterministic_and_correctly_sized():
    a = FakeEmbedder().embed(["같은 문장"])
    b = FakeEmbedder().embed(["같은 문장"])
    assert a == b
    assert len(a[0]) == EMBEDDING_DIM


def test_fake_nli_flags_a_negation_as_contradiction():
    (result,) = FakeNli().classify([("정렬은 인기순으로 진행", "정렬은 인기순으로 안 함")])
    assert result.label == "contradiction"


@pytest.mark.parametrize("missing_key", ["dim", "model_version"])
def test_kure_http_embedder_raises_rather_than_defaulting_a_missing_info_key(
    monkeypatch, missing_key
):
    info = {"dim": EMBEDDING_DIM, "model_version": "kure-v1"}
    del info[missing_key]
    monkeypatch.setattr("autune_context.pipeline.embedding.probe", lambda client, service: info)

    with pytest.raises(RuntimeError, match=missing_key):
        KureHttpEmbedder(get_settings())


def test_warm_models_hook_is_a_noop_unless_opted_in(monkeypatch):
    class _Model:
        model_version = "fake"

    calls: list[str] = []

    def _tracked(name):
        def _get():
            calls.append(name)
            return _Model()

        return _get

    monkeypatch.setattr(pipeline, "get_embedder", _tracked("embedder"))
    monkeypatch.setattr(pipeline, "get_reranker", _tracked("reranker"))
    monkeypatch.setattr(pipeline, "get_nli", _tracked("nli"))

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: ContextSettings(warm_models_on_worker_init=False)
    )
    pipeline._warm_models()
    assert calls == []

    monkeypatch.setattr(
        pipeline, "get_settings", lambda: ContextSettings(warm_models_on_worker_init=True)
    )
    pipeline._warm_models()
    assert set(calls) == {"embedder", "reranker", "nli"}
