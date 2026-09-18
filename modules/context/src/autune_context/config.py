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

    # --- topic segmentation (TextTiling) ---
    topic_window: int = 3
    """Utterances per block on each side of a candidate boundary."""
    topic_min_segment: int = 3
    """Shortest topic segment, in utterances."""
    topic_depth_threshold: float = 0.1
    """Minimum TextTiling depth score for a similarity dip to become a boundary."""

    # --- retrieval / linking knobs ---
    retrieve_top_k: int = 50
    rerank_top_k: int = 10
    rrf_k: int = 60
    link_confidence_threshold: float = 0.6
    """Above: assert the link. Below: store it as ``pending`` and ask the user.
    Placeholder value; tuned against the evaluation set once it exists (the
    eval harness is still owed — see docs/modules/context.md, "Metric").

    A production auto-tuning version of this (issue #256) was tried and
    reverted: confirm/reject only ever labels a ``pending`` link, which by
    definition scores *below* the current threshold, so the training sample
    can never show "the threshold is too low" evidence and a tuner fit to it
    only ever ratchets the value down. Left for #240's offline eval harness,
    which isn't subject to that bias, or a redesign that isn't."""

    # --- decision lineage ---
    lineage_match_threshold: float = 0.6
    """Cosine similarity between B's decision statement and a thread's latest
    statement, above which the decision is threaded into that existing lineage
    rather than opening a new one. Placeholder; tuned against the evaluation set
    once it exists. Also sensitive to how B's ``Decision.statement`` is built —
    a last-utterance quote (no reference resolution yet, extraction issue #11)
    scores closing remarks from unrelated decisions higher than this default
    tolerates, so don't tune this threshold to today's statements."""

    # --- publishing ---
    publish_timeout_s: int = 600
    """How long topic linking waits for B before publishing ``ContextLinks`` with
    ``missing_sources=["extraction"]``. Matches E's own aggregation timeout."""

    # --- notifications ---
    max_topic_link_notices: int = 3
    """Individual topic-link Slack messages posted per meeting before the rest
    collapse into one rollup notice. A meeting with many linked topics would
    otherwise post one message per topic and flood the channel."""

    # --- worker bootstrap ---
    warm_models_on_worker_init: bool = False
    """Set only on workers that actually consume the ``cpu_heavy`` queue.

    ``apps/worker`` imports every module's ``tasks.py`` into one Celery app
    (see docs/architecture/async-pipeline.md, "Queues"), so a warm-up hook
    registered unconditionally on ``worker_process_init`` would run in every
    worker process regardless of ``-Q`` — including ``gpu`` and ``default``
    workers that never run a context task and cannot reach the context model
    endpoints."""


@lru_cache
def get_settings() -> ContextSettings:
    return ContextSettings()
