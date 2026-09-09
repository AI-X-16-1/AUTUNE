"""AI pipeline for module D.

Public surface: the four model getters plus a Celery ``worker_process_init`` hook
that warms and logs each model so the first task does not pay the load cost and a
misconfigured endpoint fails at startup, not mid-meeting.

Model loading and inference only — no database writes, no HTTP handlers. Pin
model versions explicitly (config) and load once per worker process (the getters
are cached). See docs/modules/context.md, "Model abstraction layer".
"""

from __future__ import annotations

from celery.signals import worker_process_init

from autune_context.pipeline.base import Embedder, LlmClient, NliModel, NliScores, Reranker
from autune_context.pipeline.registry import (
    get_embedder,
    get_llm,
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
    "get_embedder",
    "get_reranker",
    "get_nli",
    "get_llm",
    "reset_cache",
]

log = get_logger(__name__)


@worker_process_init.connect
def _warm_models(**_: object) -> None:
    for getter in (get_embedder, get_reranker, get_nli, get_llm):
        model = getter()
        log.info("model_ready", getter=getter.__name__, version=model.model_version)
