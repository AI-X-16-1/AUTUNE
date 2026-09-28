"""The fitted misalignment predictor and the registry that chooses it."""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from autune_intelligence import history
from autune_intelligence.config import get_settings
from autune_intelligence.history import LabeledExample
from autune_intelligence.pipeline import get_misalignment_predictor, registry, reset_cache
from autune_intelligence.pipeline.predictor import (
    FEATURE_NAMES,
    MODEL_VERSION,
    HeuristicMisalignmentPredictor,
    InsufficientHistoryError,
    XGBoostMisalignmentPredictor,
    feature_fingerprint,
    feature_vector,
)
from autune_intelligence.prediction import MeetingFeatures

NOW = datetime(2026, 9, 27, tzinfo=UTC)

_BASE = MeetingFeatures(
    quality_value=0.7,
    decision_count=3,
    high_gap_count=1,
    gap_count=4,
    alignment_min=None,
    changed_decision_share=None,
    reversed_count=0,
    change_with_stakeholder_absent=False,
    ambiguous_agreement_count=0,
    unconfirmed_action_share=None,
    missing_source_count=0,
)


def _examples(n: int, seed: int = 0) -> list[LabeledExample]:
    """Reversal follows weak alignment, with noise — something learnable."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        alignment = rng.random()
        reversed_ = rng.random() < (0.8 if alignment < 0.4 else 0.1)
        out.append(
            LabeledExample(
                meeting_id=f"mtg_{i}",
                team_id="team_1",
                at=NOW - timedelta(days=20),
                features=replace(_BASE, alignment_min=alignment),
                reversed_within_horizon=reversed_,
                stored_predictions={},
            )
        )
    return out


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch):
    yield
    get_settings.cache_clear()
    reset_cache()


def _use(monkeypatch: pytest.MonkeyPatch, impl: str) -> None:
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL", impl)
    get_settings.cache_clear()
    reset_cache()


def test_feature_vector_keeps_unmeasured_as_missing_not_zero() -> None:
    vector = feature_vector(_BASE)

    assert len(vector) == len(FEATURE_NAMES)
    assert math.isnan(vector[FEATURE_NAMES.index("alignment_min")])
    assert vector[FEATURE_NAMES.index("change_with_stakeholder_absent")] == 0.0


def test_fit_refuses_too_little_history() -> None:
    with pytest.raises(InsufficientHistoryError):
        XGBoostMisalignmentPredictor.fit(_examples(20), fitted_at=NOW)


def test_fit_refuses_history_without_both_outcomes() -> None:
    all_negative = [replace(e, reversed_within_horizon=False) for e in _examples(200)]

    with pytest.raises(InsufficientHistoryError):
        XGBoostMisalignmentPredictor.fit(all_negative, fitted_at=NOW)


def test_fitted_model_learns_the_signal_and_stays_in_range() -> None:
    pytest.importorskip("xgboost")
    model = XGBoostMisalignmentPredictor.fit(_examples(300), fitted_at=NOW)

    weak, strong, unmeasured = model.predict(
        [
            replace(_BASE, alignment_min=0.1),
            replace(_BASE, alignment_min=0.9),
            _BASE,
        ]
    )

    assert weak > strong
    assert all(0.0 <= p <= 1.0 for p in (weak, strong, unmeasured))
    assert model.model_version == MODEL_VERSION
    assert (model.fitted_at, model.training_size) == (NOW, 300)


def test_model_version_survives_a_refit() -> None:
    """The version groups comparable predictions; it must not change per fit.

    ``eval`` buckets stored predictions by ``model_version`` and refuses to score
    a bucket under ``MIN_EXAMPLES``. A version carrying the fit timestamp and the
    example count makes a new bucket every refit (every 24h, and once per prefork
    child), so no bucket ever fills and the honest out-of-sample numbers -- the
    ones actually shown to users -- can never be scored.
    """
    pytest.importorskip("xgboost")
    first = XGBoostMisalignmentPredictor.fit(_examples(80, seed=1), fitted_at=NOW)
    later = XGBoostMisalignmentPredictor.fit(
        _examples(120, seed=2), fitted_at=NOW + timedelta(days=1)
    )

    assert first.model_version == later.model_version
    assert first.fitted_at != later.fitted_at
    assert first.training_size == 80 and later.training_size == 120


def test_model_version_tracks_the_feature_set() -> None:
    """What does invalidate a comparison is the features changing under it."""
    assert MODEL_VERSION.startswith("xgb-f")
    assert f"xgb-f{feature_fingerprint(FEATURE_NAMES)}" == MODEL_VERSION
    assert feature_fingerprint(FEATURE_NAMES) != feature_fingerprint((*FEATURE_NAMES, "extra"))


def test_default_impl_is_the_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_INTELLIGENCE_MISALIGNMENT_PREDICTOR_IMPL", raising=False)
    get_settings.cache_clear()
    reset_cache()

    assert isinstance(get_misalignment_predictor(), HeuristicMisalignmentPredictor)


def test_unknown_impl_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "hosted")

    with pytest.raises(ValueError, match="known"):
        get_misalignment_predictor()


def test_local_falls_back_to_the_heuristic_without_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the extra installed, thin history is a fallback and not an error."""
    pytest.importorskip("xgboost")
    _use(monkeypatch, "local")
    monkeypatch.setattr(history, "labeled_examples", lambda _session, **_: [])
    monkeypatch.setattr("autune_core.session_scope", _null_scope)

    assert isinstance(get_misalignment_predictor(now=NOW), HeuristicMisalignmentPredictor)


def test_local_without_the_extra_fails_before_reading_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured worker should say so at startup, not weeks later.

    With the count check first, ``impl=local`` without the extra stayed quietly
    on the heuristic until history crossed ``MIN_TRAINING_EXAMPLES`` -- and then
    raised on every aggregation, leaving the cache empty so each one re-read the
    database. Checking the import before the read moves the failure to the
    worker's first call.
    """
    _use(monkeypatch, "local")
    read = False

    def _watched_scope():
        nonlocal read
        read = True
        return _null_scope()

    def _missing() -> None:
        raise RuntimeError("needs the 'local-models' extra")

    monkeypatch.setattr(registry, "require_xgboost", _missing)
    monkeypatch.setattr("autune_core.session_scope", _watched_scope)

    with pytest.raises(RuntimeError, match="local-models"):
        get_misalignment_predictor(now=NOW)
    assert not read


def test_local_refits_once_the_previous_fit_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("xgboost")
    _use(monkeypatch, "local")
    available: list[LabeledExample] = []
    monkeypatch.setattr(history, "labeled_examples", lambda _session, **_: available)
    monkeypatch.setattr("autune_core.session_scope", _null_scope)

    first = get_misalignment_predictor(now=NOW)
    available.extend(_examples(300))
    same_day = get_misalignment_predictor(now=NOW + timedelta(hours=1))
    next_day = get_misalignment_predictor(now=NOW + timedelta(hours=25))

    assert isinstance(first, HeuristicMisalignmentPredictor)
    assert same_day is first
    assert isinstance(next_day, XGBoostMisalignmentPredictor)


class _null_scope:  # noqa: N801 - stands in for a context-manager function
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None
