"""Misalignment-risk predictors.

``HeuristicMisalignmentPredictor`` is the P1 baseline: a logistic function over
hand-set weights, not fit to anything. It exists so the whole path — features,
storage, the #27 display gate, the dashboard — runs end to end before there is
labeled history to fit a model on, and so a fitted model later has a baseline
to beat on the same calibration measure (docs/engineering/testing.md: E's
metric is prediction calibration).

XGBoost replaces it once reversal labels accumulate; the protocol in
``base.MisalignmentPredictor`` is what the service calls, so that swap touches
only the registry.
"""

from __future__ import annotations

import math
from typing import Final

from autune_intelligence.prediction import MeetingFeatures

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
