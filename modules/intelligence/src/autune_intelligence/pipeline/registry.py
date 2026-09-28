"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a classifier class. Call
``get_gap_classifier()``; it is cached, so SetFit trains on the first
aggregation a worker does and not on every meeting.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from autune_intelligence.config import IntelligenceSettings, get_settings

from .base import GapClassifier, MisalignmentPredictor
from .classifier import FakeGapClassifier, SetFitGapClassifier
from .predictor import HeuristicMisalignmentPredictor

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


@lru_cache
def get_misalignment_predictor() -> MisalignmentPredictor:
    """The heuristic baseline, until there is reversal history to fit XGBoost on.

    No config switch yet: with one implementation a setting would only be a
    way to misspell it. One arrives with the second implementation.
    """
    return HeuristicMisalignmentPredictor()


def reset_cache() -> None:
    """Drop the cached models. For tests that switch implementations."""
    get_gap_classifier.cache_clear()
    get_misalignment_predictor.cache_clear()
