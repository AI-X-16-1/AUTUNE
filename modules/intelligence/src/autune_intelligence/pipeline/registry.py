"""Config string -> implementation, loaded once per process.

Nothing outside this package instantiates a classifier class. Call
``get_gap_classifier()``; it is cached, so SetFit trains on the first
aggregation a worker does and not on every meeting.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from autune_core import get_logger
from autune_intelligence.config import IntelligenceSettings, get_settings

from .base import GapClassifier, MisalignmentPredictor
from .classifier import FakeGapClassifier, SetFitGapClassifier
from .predictor import (
    HeuristicMisalignmentPredictor,
    InsufficientHistoryError,
    XGBoostMisalignmentPredictor,
    require_xgboost,
)

log = get_logger(__name__)

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


_predictor: MisalignmentPredictor | None = None
_predictor_expires_at: datetime | None = None


def _fit_local(now: datetime) -> MisalignmentPredictor:
    """XGBoost on labeled history, or the heuristic when there is not enough yet."""
    from autune_core import session_scope  # noqa: PLC0415
    from autune_intelligence.history import labeled_examples  # noqa: PLC0415

    # Before the database read, so a worker configured for `local` without the
    # extra fails at startup rather than on the day history crosses the floor.
    require_xgboost()
    with session_scope() as session:
        examples = labeled_examples(session, now=now)
    try:
        predictor = XGBoostMisalignmentPredictor.fit(examples, fitted_at=now)
    except InsufficientHistoryError as exc:
        log.info("misalignment_predictor_fallback", reason=str(exc))
        return HeuristicMisalignmentPredictor()
    # The per-fit facts live here, not in model_version -- see MODEL_VERSION.
    log.info(
        "misalignment_predictor_fitted",
        version=predictor.model_version,
        fitted_at=predictor.fitted_at.isoformat(),
        examples=predictor.training_size,
    )
    return predictor


def get_misalignment_predictor(*, now: datetime | None = None) -> MisalignmentPredictor:
    """The configured predictor, refit from history every ``misalignment_refit_hours``.

    ``heuristic`` never touches the database. ``local`` reads labeled history
    in its own short session — read-only, and separate from the caller's
    transaction — and keeps the result (fitted model or fallback) until it
    expires, so a team's first labeled meetings are picked up within a day.
    """
    global _predictor, _predictor_expires_at
    settings = get_settings()
    impl = settings.misalignment_predictor_impl
    if impl not in _PREDICTORS:
        raise ValueError(
            f"unknown AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL={impl!r}; "
            f"known: {sorted(_PREDICTORS)}"
        )
    now = now or datetime.now(UTC)
    if _predictor is None or (_predictor_expires_at is not None and now >= _predictor_expires_at):
        _predictor = _PREDICTORS[impl](now)
        _predictor_expires_at = (
            now + timedelta(hours=settings.misalignment_refit_hours) if impl == "local" else None
        )
    return _predictor


_PREDICTORS: dict[str, Callable[[datetime], MisalignmentPredictor]] = {
    "heuristic": lambda _now: HeuristicMisalignmentPredictor(),
    "local": _fit_local,
}


def reset_cache() -> None:
    """Drop the cached models. For tests that switch implementations."""
    global _predictor, _predictor_expires_at
    get_gap_classifier.cache_clear()
    _predictor = None
    _predictor_expires_at = None
