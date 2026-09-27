"""Reversal labels: which meetings are positive, and which are not labeled yet."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autune_intelligence.history import MeetingPoint, Reversal, reversal_labels

NOW = datetime(2026, 9, 27, tzinfo=UTC)


def _m(mid: str, days_ago: float) -> MeetingPoint:
    return MeetingPoint(meeting_id=mid, team_id="team_1", at=NOW - timedelta(days=days_ago))


def _r(earlier: str, days_ago: float) -> Reversal:
    return Reversal(earlier_meeting_id=earlier, at=NOW - timedelta(days=days_ago))


def test_a_reversal_inside_the_horizon_labels_the_earlier_meeting_positive() -> None:
    labels = reversal_labels([_m("mtg_a", 30), _m("mtg_b", 25)], [_r("mtg_a", 25)], now=NOW)

    assert labels == {"mtg_a": True, "mtg_b": False}


def test_a_reversal_after_the_horizon_does_not_count() -> None:
    labels = reversal_labels([_m("mtg_a", 30)], [_r("mtg_a", 15)], now=NOW)  # 15 days later

    assert labels == {"mtg_a": False}


def test_a_meeting_whose_horizon_is_still_open_is_not_labeled() -> None:
    """ "No reversal yet" is not "no reversal"."""
    labels = reversal_labels([_m("mtg_a", 10)], [], now=NOW)

    assert labels == {}


def test_the_horizon_boundary_is_inclusive() -> None:
    labels = reversal_labels([_m("mtg_a", 20)], [_r("mtg_a", 6)], now=NOW)  # exactly 14 days

    assert labels == {"mtg_a": True}
