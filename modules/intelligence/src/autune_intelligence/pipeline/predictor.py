"""Misalignment-risk predictors.

``HeuristicMisalignmentPredictor`` is the P1 baseline: a logistic function over
hand-set weights, not fit to anything. It exists so the whole path — features,
storage, the #27 display gate, the dashboard — runs end to end before there is
labeled history to fit a model on, and so a fitted model later has a baseline
to beat on the same calibration measure (docs/engineering/testing.md: E's
metric is prediction calibration).

``XGBoostMisalignmentPredictor`` is fit on labeled history
(``history.labeled_examples``: was a decision from this meeting reversed within
the horizon?). ``registry.get_misalignment_predictor`` decides which one runs:
the fitted model when there is enough history, the heuristic otherwise.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import fields
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from autune_intelligence.prediction import MeetingFeatures

if TYPE_CHECKING:
    from autune_intelligence.history import LabeledExample

_INTERCEPT: Final = -2.2
"""logit(0.1): a meeting with every signal neutral or unmeasured predicts a
~10% chance a decision is reversed within the horizon. A guess, not a base
rate — nobody has measured one."""

_WEIGHTS: Final = {
    "disagreement": 2.0,
    "high_gaps": 0.25,
    "changed_decision_share": 1.2,
    "reversed": 0.4,
    "stakeholder_absent": 0.8,
    "ambiguous": 0.2,
    "unconfirmed_actions": 0.6,
    "low_quality": 1.0,
}
"""Unvalidated P1 heuristic, fixed by hand — the same status ``service.WEIGHTS``
has for the quality score (#26). Signs follow what each signal means (more
disagreement, more unresolved gaps, a team already revising itself → higher
risk); magnitudes are placeholders until the XGBoost fit replaces them.
"""

_CAP: Final = {"high_gaps": 5, "reversed": 3, "ambiguous": 5}
"""Counts are capped so one very long meeting cannot saturate the logit on
volume alone."""


def _terms(f: MeetingFeatures) -> dict[str, float]:
    terms: dict[str, float] = {"low_quality": 1.0 - f.quality_value}
    if f.alignment_min is not None:
        terms["disagreement"] = 1.0 - f.alignment_min
    if f.high_gap_count is not None:
        terms["high_gaps"] = min(f.high_gap_count, _CAP["high_gaps"])
    if f.changed_decision_share is not None:
        terms["changed_decision_share"] = f.changed_decision_share
    if f.reversed_count is not None:
        terms["reversed"] = min(f.reversed_count, _CAP["reversed"])
    if f.change_with_stakeholder_absent is not None:
        terms["stakeholder_absent"] = float(f.change_with_stakeholder_absent)
    if f.ambiguous_agreement_count is not None:
        terms["ambiguous"] = min(f.ambiguous_agreement_count, _CAP["ambiguous"])
    if f.unconfirmed_action_share is not None:
        terms["unconfirmed_actions"] = f.unconfirmed_action_share
    return terms


class HeuristicMisalignmentPredictor:
    """Logistic over hand-set weights. An unmeasured feature contributes nothing."""

    model_version = "heuristic-v1"

    def predict(self, features: list[MeetingFeatures]) -> list[float]:
        return [self._one(f) for f in features]

    @staticmethod
    def _one(f: MeetingFeatures) -> float:
        z = _INTERCEPT + sum(_WEIGHTS[k] * v for k, v in _terms(f).items())
        return 1.0 / (1.0 + math.exp(-z))


FEATURE_NAMES: Final = tuple(f.name for f in fields(MeetingFeatures))
"""Column order for the fitted model — every ``MeetingFeatures`` field, so a
new feature is picked up by the next fit without a second list to update."""


def feature_fingerprint(names: Sequence[str]) -> str:
    """Eight hex characters standing for one feature set."""
    return hashlib.sha256("|".join(names).encode()).hexdigest()[:8]


MODEL_VERSION: Final = f"xgb-f{feature_fingerprint(FEATURE_NAMES)}"
"""What ``intel_predictions.model_version`` records, and what ``eval`` groups by.

**It must not change when the model is refit.** An earlier version carried the
fit timestamp and the training size, which changes every
``misalignment_refit_hours`` and once per prefork child. ``eval`` buckets stored
predictions by this string and refuses to score a bucket below
``MIN_EXAMPLES``, so a per-fit version means no bucket ever fills: 84 days of
daily refits produced 84 versions and zero scored. The stored predictions are
the only out-of-sample numbers there are — the ones users were actually shown —
so losing them loses the honest half of the report.

The fingerprint is over ``FEATURE_NAMES`` because that is what genuinely makes
two predictions incomparable: a model fit on different columns is a different
model. Refit time and example count are per-fit facts and go to the
``misalignment_predictor_fitted`` log line instead."""

MIN_TRAINING_EXAMPLES: Final = 50
MIN_EXAMPLES_PER_CLASS: Final = 5
"""Below either, ``fit`` refuses and the registry keeps the heuristic. Fifty
labeled meetings is a floor, not a recommendation: gradient boosting on fewer
memorizes the few reversals it has seen."""

_XGB_PARAMS: Final[dict[str, Any]] = {
    "n_estimators": 200,
    "max_depth": 3,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "min_child_weight": 2,
    "reg_lambda": 1.0,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "n_jobs": 1,
    "random_state": 0,
}
"""Shallow and heavily shrunk, for small tabular data: depth 3 keeps each tree
to a few feature interactions, and the low learning rate keeps early
probabilities near the base rate rather than at 0 or 1 — which is what the
calibration metric punishes. ``n_jobs=1`` because this fits inside a Celery
worker that already runs one task per process."""


class InsufficientHistoryError(Exception):
    """Too few labeled meetings, or too few of one class, to fit on."""


def feature_vector(f: MeetingFeatures) -> list[float]:
    """``None`` becomes NaN, which XGBoost treats as missing — not as zero."""
    values: list[float] = []
    for name in FEATURE_NAMES:
        value = getattr(f, name)
        values.append(math.nan if value is None else float(value))
    return values


def require_xgboost() -> None:
    """Raise before anything expensive when the optional extra is missing.

    Called at the top of the registry's fit path rather than inside ``fit``: the
    count check used to run first, so a worker without the extra stayed quietly
    on the heuristic until the day history crossed
    ``MIN_TRAINING_EXAMPLES``, and then raised on every aggregation. Checking
    first makes a misconfigured worker fail at startup instead.
    """
    try:
        import numpy  # noqa: F401, PLC0415
        import xgboost  # noqa: F401, PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "the XGBoost misalignment predictor needs the 'local-models' extra: "
            "uv sync --package autune-intelligence --extra local-models"
        ) from exc


class XGBoostMisalignmentPredictor:
    """Gradient-boosted trees fit on the trailing window of labeled meetings."""

    def __init__(self, model: Any, *, fitted_at: datetime, training_size: int) -> None:
        self._model = model
        self.model_version = MODEL_VERSION
        self.fitted_at = fitted_at
        """When this instance was fit. Reported in the
        ``misalignment_predictor_fitted`` log line, which is where the fit time
        went when it came out of ``model_version``. Nothing reads it to decide
        whether a holdout is needed -- ``eval`` asks
        ``registry.predictor_fits_from_history`` about the configured
        implementation instead, so an unfitted predictor needs no such
        attribute."""
        self.training_size = training_size

    @classmethod
    def fit(
        cls, examples: Sequence[LabeledExample], *, fitted_at: datetime
    ) -> XGBoostMisalignmentPredictor:
        positives = sum(1 for e in examples if e.reversed_within_horizon)
        negatives = len(examples) - positives
        if (
            len(examples) < MIN_TRAINING_EXAMPLES
            or min(positives, negatives) < MIN_EXAMPLES_PER_CLASS
        ):
            raise InsufficientHistoryError(
                f"{len(examples)} labeled meetings ({positives} positive); need "
                f"{MIN_TRAINING_EXAMPLES} with {MIN_EXAMPLES_PER_CLASS} of each class"
            )
        require_xgboost()
        import numpy as np  # noqa: PLC0415
        from xgboost import XGBClassifier  # noqa: PLC0415

        x = np.array([feature_vector(e.features) for e in examples], dtype=float)
        y = np.array([int(e.reversed_within_horizon) for e in examples])
        model = XGBClassifier(**_XGB_PARAMS, base_score=positives / len(examples))
        model.fit(x, y)
        return cls(model, fitted_at=fitted_at, training_size=len(examples))

    def predict(self, features: list[MeetingFeatures]) -> list[float]:
        if not features:
            return []
        import numpy as np  # noqa: PLC0415

        x = np.array([feature_vector(f) for f in features], dtype=float)
        return [float(p) for p in self._model.predict_proba(x)[:, 1]]
