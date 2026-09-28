"""Role-pair alignment arithmetic: pure functions over ``stance_by_role``, no I/O."""

from __future__ import annotations

import pytest

from autune_contracts import Decision, RoleStance
from autune_intelligence.alignment import (
    decision_agreement,
    meeting_alignment,
    role_position,
)


def _stance(role: str, identified: int, supporting: int, concerns: int) -> RoleStance:
    return RoleStance(role=role, identified=identified, supporting=supporting, concerns=concerns)


def _decision(i: int, *stances: RoleStance) -> Decision:
    return Decision(id=f"dec_{i}", statement="s", confidence=0.9, stance_by_role=list(stances))


def test_position_counts_silent_people_as_neutral() -> None:
    assert role_position(_stance("PM", 5, 0, 1)) == pytest.approx(-0.2)
    assert role_position(_stance("PM", 4, 3, 0)) == pytest.approx(0.75)


def test_roles_leaning_the_same_way_by_the_same_amount_fully_agree() -> None:
    assert decision_agreement(_stance("Dev", 4, 2, 0), _stance("PM", 4, 2, 0)) == 1.0


def test_opposite_leans_pull_agreement_down_symmetrically() -> None:
    dev, pm = _stance("Dev", 4, 3, 0), _stance("PM", 4, 0, 3)

    assert decision_agreement(dev, pm) == pytest.approx(0.25)
    assert decision_agreement(pm, dev) == decision_agreement(dev, pm)


def test_two_silent_roles_are_not_scored_as_consensus() -> None:
    assert decision_agreement(_stance("Dev", 3, 0, 0), _stance("PM", 3, 0, 0)) is None


def test_one_silent_role_against_one_that_spoke_is_scored() -> None:
    assert decision_agreement(_stance("Dev", 3, 0, 0), _stance("PM", 4, 2, 0)) == pytest.approx(
        0.75
    )


def test_meeting_alignment_averages_each_pair_over_the_decisions_it_appears_in() -> None:
    pairs = meeting_alignment(
        [
            _decision(1, _stance("PM", 4, 2, 0), _stance("Dev", 4, 2, 0)),
            _decision(2, _stance("PM", 4, 2, 0), _stance("Dev", 4, 0, 2)),
        ]
    )

    assert len(pairs) == 1
    (pair,) = pairs
    assert (pair.role_a, pair.role_b) == ("Dev", "PM")  # ordered, whatever the input order
    assert pair.score == pytest.approx((1.0 + 0.5) / 2)
    assert pair.decision_count == 2


def test_every_pair_among_three_roles_is_scored_once() -> None:
    pairs = meeting_alignment(
        [_decision(1, _stance("PM", 3, 1, 0), _stance("Dev", 3, 1, 0), _stance("Data", 3, 0, 1))]
    )

    assert [(p.role_a, p.role_b) for p in pairs] == [
        ("Data", "Dev"),
        ("Data", "PM"),
        ("Dev", "PM"),
    ]


def test_a_role_alone_on_a_decision_forms_no_pair() -> None:
    assert meeting_alignment([_decision(1, _stance("PM", 3, 1, 0))]) == []


def test_decisions_without_stance_produce_nothing() -> None:
    """The state until B's producer ships: ``stance_by_role`` is always empty."""
    assert meeting_alignment([_decision(1), _decision(2)]) == []


def test_pair_converts_to_the_contract_shape() -> None:
    (pair,) = meeting_alignment([_decision(1, _stance("PM", 3, 1, 0), _stance("Dev", 3, 1, 0))])

    contract = pair.to_contract()
    assert (contract.role_a, contract.role_b, contract.score) == ("Dev", "PM", 1.0)
