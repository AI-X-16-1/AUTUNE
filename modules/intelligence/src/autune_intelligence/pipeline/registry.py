"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a classifier class. Call
``get_gap_classifier()``; it is cached, so SetFit trains on the first
aggregation a worker does and not on every meeting.
"""

from __future__ import annotations

from functools import lru_cache

from autune_intelligence.config import get_settings

from .base import GapClassifier
from .classifier import FakeGapClassifier, SetFitGapClassifier

_CLASSIFIERS: dict[str, str] = {
    "local": "SetFit, trained in this process from the seed set",
    "fake": "deterministic, for tests",
}
"""Known implementations and what they are. There is no external-API entry —
see ``base``."""


@lru_cache
def get_gap_classifier() -> GapClassifier:
    settings = get_settings()
    impl = settings.gap_classifier_impl

    if impl == "local":
        return SetFitGapClassifier(settings.gap_classifier_backbone)
    if impl == "fake":
        return FakeGapClassifier()

    raise ValueError(
        f"unknown AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL={impl!r}; known: {sorted(_CLASSIFIERS)}"
    )


def reset_cache() -> None:
    """Drop the cached classifier. For tests that switch implementations."""
    get_gap_classifier.cache_clear()
