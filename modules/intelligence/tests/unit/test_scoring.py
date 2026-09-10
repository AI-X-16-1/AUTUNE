"""Module E quality-score components — pure functions, no database."""

from __future__ import annotations

import pytest

from autune_contracts import ActionStatus
from autune_contracts.extraction import ActionItem
from autune_contracts.gap import Participation
from autune_intelligence import service


def _item(status: ActionStatus) -> ActionItem:
    return ActionItem(id="act_1", description="d", status=status, confidence=0.9)


def test_decision_density_one_per_ten_minutes_is_full() -> None:
    assert service._decision_density(3, 30.0) == pytest.approx(1.0)


def test_decision_density_scales_below_the_cadence() -> None:
    assert service._decision_density(1, 30.0) == pytest.approx(1 / 3)


def test_decision_density_caps_at_one() -> None:
    assert service._decision_density(20, 30.0) == pytest.approx(1.0)


def test_decision_density_tolerates_zero_duration() -> None:
    assert service._decision_density(1, 0.0) == pytest.approx(1.0)


def test_decision_density_is_none_with_no_decisions() -> None:
    assert service._decision_density(0, 30.0) is None
    assert service._decision_density(0, 0.0) is None


def test_gap_burden_is_one_with_no_high_gaps() -> None:
    assert service._gap_burden(0) == pytest.approx(1.0)


def test_gap_burden_hits_zero_at_five_high_gaps() -> None:
    assert service._gap_burden(5) == pytest.approx(0.0)
    assert service._gap_burden(9) == pytest.approx(0.0)


def test_action_item_completion_rate_counts_anything_past_needs_confirmation() -> None:
    items = [
        _item(ActionStatus.NEEDS_CONFIRMATION),
        _item(ActionStatus.TODO),
        _item(ActionStatus.DONE),
    ]
    assert service._action_item_completion_rate(items) == pytest.approx(2 / 3)


def test_action_item_completion_rate_is_none_with_no_items() -> None:
    assert service._action_item_completion_rate([]) is None


def test_participation_balance_averages_topic_coverage() -> None:
    p = [
        Participation(topic_id="t1", spoke=["u1", "u2"], silent=["u3", "u4"]),
        Participation(topic_id="t2", spoke=["u1"], silent=["u2", "u3"]),
    ]
    assert service._participation_balance(p) == pytest.approx((0.5 + 1 / 3) / 2)


def test_participation_balance_is_none_with_no_data() -> None:
    assert service._participation_balance([]) is None


def test_quality_score_weighted_mean_of_present_components() -> None:
    score = service._quality_score(
        {
            "decision_density": 1.0,
            "gap_burden": 1.0,
            "action_item_completion_rate": 0.0,
            "participation_balance": 0.0,
        }
    )
    assert score.value == pytest.approx(0.6)  # .3 + .3 + 0 + 0
    assert score.grade == "D"


def test_quality_score_renormalises_when_a_component_is_missing() -> None:
    score = service._quality_score({"decision_density": 1.0, "action_item_completion_rate": 1.0})
    assert score.value == pytest.approx(1.0)  # weights .3 and .2 renormalise to .6/.4
    assert score.grade == "A"


def test_quality_score_is_neutral_when_everything_is_missing() -> None:
    score = service._quality_score({"decision_density": None, "gap_burden": None})
    assert score.value == pytest.approx(0.5)
    assert score.grade == "E"


@pytest.mark.parametrize(
    ("value", "grade"),
    [(0.95, "A"), (0.9, "A"), (0.85, "B"), (0.75, "C"), (0.65, "D"), (0.55, "E"), (0.4, "F")],
)
def test_grade_cutoffs(value: float, grade: str) -> None:
    assert service._grade_for(value) == grade
