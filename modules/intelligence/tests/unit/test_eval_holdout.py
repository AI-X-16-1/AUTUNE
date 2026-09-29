"""The eval's `current:` row must be out of sample when the predictor is fitted."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from autune_intelligence import eval as eval_module
from autune_intelligence.eval import HOLDOUT, evaluate
from autune_intelligence.history import LabeledExample
from autune_intelligence.prediction import MISALIGNMENT_HORIZON_DAYS, MeetingFeatures

NOW = datetime(2026, 9, 27, tzinfo=UTC)

_BASE = MeetingFeatures(
    quality_value=0.7,
    decision_count=3,
    high_gap_count=1,
    gap_count=4,
    alignment_min=0.5,
    changed_decision_share=None,
    reversed_count=0,
    change_with_stakeholder_absent=False,
    ambiguous_agreement_count=0,
    unconfirmed_action_share=None,
    missing_source_count=0,
)


def _examples(n: int, *, oldest_days: int, newest_days: int) -> list[LabeledExample]:
    rng = random.Random(0)
    span = oldest_days - newest_days
    return [
        LabeledExample(
            meeting_id=f"mtg_{i}",
            team_id="team_1",
            at=NOW - timedelta(days=oldest_days - span * i / max(n - 1, 1)),
            features=replace(_BASE, alignment_min=rng.random()),
            reversed_within_horizon=rng.random() < 0.3,
            stored_predictions={"heuristic-v1": rng.random()},
        )
        for i in range(n)
    ]


class _Stub:
    """Stands in for a predictor.

    `fitted` is what `predictor_fits_from_history` returns below -- that is what
    `eval` asks. `fitted_at` rides along only because a real fitted predictor
    carries it.
    """

    def __init__(self, version: str, *, fitted: bool) -> None:
        self.model_version = version
        if fitted:
            self.fitted_at = NOW

    def predict(self, features: list[MeetingFeatures]) -> list[float]:
        return [0.3] * len(features)


@pytest.fixture
def _stub(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def factory(fitted: bool, version: str):
        def _get(*, now=None):
            seen["now"] = now
            return _Stub(version, fitted=fitted)

        monkeypatch.setattr(eval_module, "get_misalignment_predictor", _get)
        monkeypatch.setattr(eval_module, "predictor_fits_from_history", lambda: fitted)
        monkeypatch.setattr(eval_module, "reset_cache", lambda: None)
        return seen

    return factory


def test_a_fitted_predictor_is_scored_only_after_the_split(_stub) -> None:
    """Fit on what closed before the split, score only what came after it.

    Without this the predictor is scored on the window it was fit on, and an
    in-sample Brier beats `stored:heuristic-v1`'s out-of-sample one almost
    however bad the model is -- so the switch rule it was meant to decide
    measures nothing.
    """
    seen = _stub(True, "xgb-fdeadbeef")
    examples = _examples(60, oldest_days=80, newest_days=15)

    reports = evaluate(examples, now=NOW, holdout=timedelta(weeks=4))

    key = next(k for k in reports if k.startswith("current:"))
    assert "out of sample" in key
    # The predictor was built as of the split, so its own fit saw nothing newer.
    assert seen["now"] == NOW - timedelta(weeks=4)
    scored = [e for e in examples if e.at > NOW - timedelta(weeks=4)]
    assert 0 < len(scored) < len(examples)
    assert reports[key] is None or reports[key].count == len(scored)


def test_an_unfitted_predictor_keeps_every_example(_stub) -> None:
    """The heuristic is not fit on anything, so holding data back buys nothing."""
    _stub(False, "heuristic-v1")
    examples = _examples(60, oldest_days=80, newest_days=15)

    reports = evaluate(examples, now=NOW, holdout=timedelta(weeks=4))

    key = next(k for k in reports if k.startswith("current:"))
    assert "out of sample" not in key
    assert reports[key] is not None and reports[key].count == len(examples)


def test_the_default_holdout_clears_the_label_horizon() -> None:
    """A holdout inside the label horizon would leave nothing scoreable.

    Examples stop at ``now - horizon`` (a meeting is not labeled until its
    horizon passes) and scoring starts at ``now - HOLDOUT``, so the two windows
    only overlap while the holdout is the longer of the two.
    """
    horizon = timedelta(days=MISALIGNMENT_HORIZON_DAYS)
    assert horizon < HOLDOUT
