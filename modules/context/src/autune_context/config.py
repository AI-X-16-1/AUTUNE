"""Module D settings. Environment prefix ``AUTUNE_CONTEXT_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.

Implementation selection lives here: ``*_impl`` picks which class backs each
model, and swapping it changes no code outside ``autune_context.pipeline``.
See docs/modules/context.md, "Model abstraction layer".
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(env_prefix="AUTUNE_CONTEXT_", env_file=".env", extra="ignore")

    # --- implementation selection (swap here, nothing else changes) ---
    embedder_impl: str = "kure_v1_http"
    reranker_impl: str = "bge_reranker_v2_m3_ko_http"
    nli_impl: str = "klue_kornli_http"
    # LLM (agenda / briefs) is Phase 2 and has no impl yet — see base.LlmClient.

    # --- embedding (KURE-v1) ---
    embedding_dim: int = 1024
    """Fixed at migration time as ``ctx_embeddings.embedding vector(N)``.
    ``get_embedder()`` refuses to start if the chosen model's dimension differs."""
    embedder_endpoint: str = "http://autune-embed.internal:8080"
    embedder_timeout_s: float = 10.0
    embedder_local_model: str = "nlpai-lab/KURE-v1"
    """Only used by the ``kure_v1_local`` implementation (extra: local-models)."""

    # --- reranker (dragonkue/bge-reranker-v2-m3-ko) ---
    reranker_endpoint: str = "http://autune-rerank.internal:8080"
    reranker_timeout_s: float = 10.0
    reranker_local_model: str = "dragonkue/bge-reranker-v2-m3-ko"

    # --- nli (klue/roberta fine-tuned on KorNLI, in-house) ---
    nli_endpoint: str = "http://autune-nli.internal:8080"
    nli_timeout_s: float = 10.0
    nli_local_model: str = ""
    """Path or hub id of the in-house checkpoint. Set for ``klue_kornli_local``."""

    # --- retrieval / linking knobs ---
    retrieve_top_k: int = 50
    rerank_top_k: int = 10
    rrf_k: int = 60
    link_confidence_threshold: float = 0.6
    """Above: assert the link. Below: store it as ``pending`` and ask the user.
    Placeholder value; tuned against the evaluation set in Phase 2."""

    # --- publishing ---
    publish_timeout_s: int = 600
    """How long topic linking waits for B before publishing ``ContextLinks`` with
    ``missing_sources=["extraction"]``. Matches E's own aggregation timeout."""


@lru_cache
def get_settings() -> ContextSettings:
    return ContextSettings()
