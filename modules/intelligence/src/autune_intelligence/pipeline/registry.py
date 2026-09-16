"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a classifier class. Call
``get_gap_classifier()``; it is cached, so SetFit trains on the first
aggregation a worker does and not on every meeting.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from autune_intelligence.config import IntelligenceSettings, get_settings

from .base import GapClassifier
from .classifier import FakeGapClassifier, SetFitGapClassifier

_CLASSIFIERS: dict[str, Callable[[IntelligenceSettings], GapClassifier]] = {
    "local": lambda settings: SetFitGapClassifier(settings.gap_classifier_backbone),
    "fake": lambda settings: FakeGapClassifier(),
}
"""Known implementations, keyed by ``AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL``,
and how to build each. The single source of truth for both "what's known" (the
error message below) and "how to build it" (dispatch) — a separate
name-to-description table could list an impl dispatch doesn't recognize, or
vice versa. There is no external-API entry — see ``base``."""


@lru_cache
def get_gap_classifier() -> GapClassifier:
    settings = get_settings()
    impl = settings.gap_classifier_impl

    try:
        factory = _CLASSIFIERS[impl]
    except KeyError:
        raise ValueError(
            f"unknown AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL={impl!r}; "
            f"known: {sorted(_CLASSIFIERS)}"
        ) from None
    return factory(settings)


def reset_cache() -> None:
    """Drop the cached classifier. For tests that switch implementations."""
    get_gap_classifier.cache_clear()
