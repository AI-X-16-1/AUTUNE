"""Public surface for module E's model pipeline, plus a Celery
``worker_process_init`` hook that, when ``IntelligenceSettings.
warm_models_on_worker_init`` is set, trains the gap classifier eagerly so the
cost lands at worker startup instead of inside the first meeting's aggregation
transaction (``service.aggregate_meeting`` holds an ``intel_completion`` row
lock for that call — see docs/modules/intelligence.md). Opt-in for the same
reason module D's equivalent hook is: ``apps/worker`` imports every module's
``tasks.py`` into one Celery app, so an unconditional hook would run in every
worker process regardless of the queue it consumes.
"""

from __future__ import annotations

from celery.signals import worker_process_init

from autune_core import get_logger
from autune_intelligence.config import get_settings

from .registry import get_gap_classifier, reset_cache

__all__ = ["get_gap_classifier", "reset_cache"]

log = get_logger(__name__)


@worker_process_init.connect
def _warm_models(**_: object) -> None:
    if not get_settings().warm_models_on_worker_init:
        return
    classifier = get_gap_classifier()
    # GapClassifier has no separate "load" step in its protocol — classify() is
    # the only call that trains SetFit's head (FakeGapClassifier no-ops here).
    # The placeholder text and its result are both discarded; only the training
    # side effect is wanted.
    classifier.classify(["warmup"])
    log.info("model_ready", getter="get_gap_classifier", version=classifier.model_version)
