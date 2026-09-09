"""Config-string -> implementation, with the startup dimension guard.

Nothing outside this package instantiates a model class directly. Call
``get_embedder()`` / ``get_reranker()`` / ``get_nli()`` / ``get_llm()``; each is
cached, so the model loads once per worker process.
"""

from __future__ import annotations

from functools import lru_cache

from autune_context.config import get_settings
from autune_context.constants import EMBEDDING_DIM
from autune_context.pipeline import embedding, llm, nli, reranking
from autune_context.pipeline.base import Embedder, LlmClient, NliModel, Reranker

_EMBEDDERS: dict[str, type] = {
    "kure_v1_http": embedding.KureHttpEmbedder,
    "kure_v1_local": embedding.KureLocalEmbedder,
    "fake": embedding.FakeEmbedder,
}
_RERANKERS: dict[str, type] = {
    "bge_reranker_v2_m3_ko_http": reranking.BgeRerankerKoHttp,
    "bge_reranker_v2_m3_ko_local": reranking.BgeRerankerKoLocal,
    "fake": reranking.FakeReranker,
}
_NLI: dict[str, type] = {
    "klue_kornli_http": nli.KlueKorNliHttp,
    "klue_kornli_local": nli.KlueKorNliLocal,
    "fake": nli.FakeNli,
}
_LLM: dict[str, type] = {
    "external": llm.ExternalLlm,
    "self_hosted_http": llm.SelfHostedLlm,
    "fake": llm.FakeLlm,
}


def _pick(kind: str, table: dict[str, type], key: str) -> type:
    try:
        return table[key]
    except KeyError:
        raise ValueError(
            f"unknown AUTUNE_CONTEXT_{kind}_IMPL={key!r}; known: {sorted(table)}"
        ) from None


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    impl = _pick("EMBEDDER", _EMBEDDERS, settings.embedder_impl)(settings)
    if impl.dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"embedder {settings.embedder_impl!r} has dim {impl.dim}, but "
            f"ctx_embeddings.embedding is vector({EMBEDDING_DIM}). A re-dimensioned "
            "model is a new migration, not a config change."
        )
    return impl


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    return _pick("RERANKER", _RERANKERS, settings.reranker_impl)(settings)


@lru_cache
def get_nli() -> NliModel:
    settings = get_settings()
    return _pick("NLI", _NLI, settings.nli_impl)(settings)


@lru_cache
def get_llm() -> LlmClient:
    settings = get_settings()
    return _pick("LLM", _LLM, settings.llm_impl)(settings)


def reset_cache() -> None:
    """Drop every cached model. For tests that switch implementations."""
    for getter in (get_embedder, get_reranker, get_nli, get_llm):
        getter.cache_clear()
