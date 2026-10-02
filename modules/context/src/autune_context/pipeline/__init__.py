"""AI pipeline for the Meeting Context Engine.

Public surface: the model getters plus a Celery ``worker_process_init`` hook that,
when ``ContextSettings.warm_models_on_worker_init`` is set, warms and logs each
model so the first task does not pay the load cost and a misconfigured or
unreachable endpoint fails at startup, not mid-meeting (the ``*Http`` clients
probe ``/health`` in their constructor). Opt-in because every worker process
imports this module regardless of the queue it consumes — see the setting's
docstring.

Model loading and inference only — no database writes, no HTTP handlers. Pin
model versions explicitly (config) and load once per worker process (the getters
are cached). See docs/modules/context.md, "Model abstraction layer".
"""

from __future__ import annotations

from celery.signals import worker_process_init

from autune_context.config import get_settings
from autune_context.pipeline.base import (
    Embedder,
    LlmClient,
    LlmUsage,
    NliModel,
    NliScores,
    Reranker,
)
from autune_context.pipeline.llm_judge import LlmJudge
from autune_context.pipeline.registry import (
    get_embedder,
    get_llm,
    get_llm_judge,
    get_nli,
    get_reranker,
    reset_cache,
)
from autune_core import get_logger

__all__ = [
    "Embedder",
    "Reranker",
    "NliModel",
    "NliScores",
    "LlmClient",
    "LlmJudge",
    "LlmUsage",
    "get_embedder",
    "get_reranker",
    "get_nli",
    "get_llm",
    "get_llm_judge",
    "reset_cache",
]

log = get_logger(__name__)


@worker_process_init.connect
def _warm_models(**_: object) -> None:
    settings = get_settings()
    if not settings.warm_models_on_worker_init:
        return
    # ``llm`` mode never calls the re-ranker or NLI, and their endpoints may not
    # exist where it runs; the LLM client has nothing to warm (it does not probe).
    getters = (
        (get_embedder,)
        if settings.engine_mode == "llm"
        else (
            get_embedder,
            get_reranker,
            get_nli,
        )
    )
    for getter in getters:
        model = getter()
        log.info("model_ready", getter=getter.__name__, version=model.model_version)
