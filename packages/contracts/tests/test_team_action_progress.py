"""B -> E: a team's action-item counts per meeting, for E's real completion rate (#605)."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from autune_contracts import (
    ACTION_PROGRESS_PUBLISH_EVERY,
    ACTION_PROGRESS_STALE_AFTER,
    ACTION_PROGRESS_TODAY_ZONE,
    ACTION_PROGRESS_WINDOW,
    EVENTS,
    EXTRACTION_ACTION_PROGRESS,
    TERMINAL_EVENTS,
    MeetingActionProgress,
    TeamActionProgress,
    fixtures,
    validate_major_version,
)


def test_the_event_is_declared_and_reaches_a_consumer() -> None:
    assert EXTRACTION_ACTION_PROGRESS in EVENTS
    assert EXTRACTION_ACTION_PROGRESS not in TERMINAL_EVENTS


def test_the_snapshot_goes_stale_after_a_few_missed_publishes() -> None:
    assert ACTION_PROGRESS_STALE_AFTER >= 2 * ACTION_PROGRESS_PUBLISH_EVERY
    assert ACTION_PROGRESS_WINDOW.days == 91  # 13 weeks, the 90-day retention


def test_overdue_is_counted_against_one_named_zone() -> None:
    """B's board and E's dashboard must agree on "today" (#619 review)."""
    from zoneinfo import ZoneInfo

    assert ZoneInfo(ACTION_PROGRESS_TODAY_ZONE).key == "Asia/Seoul"
    assert "Asia/Seoul" in MeetingActionProgress.model_fields["overdue"].description


def test_counts_hold_together() -> None:
    row = MeetingActionProgress(meeting_id="mtg_a1", confirmed=3, done=1, overdue=2)
    assert (row.confirmed, row.done, row.overdue) == (3, 1, 2)


@pytest.mark.parametrize(
    ("confirmed", "done", "overdue"),
    [
        (0, 0, 0),  # a meeting with nothing confirmed is left out, not sent as zero
        (2, 3, 0),  # more done than confirmed
        (3, 1, 3),  # overdue counts only confirmed items that are not done
        (2, -1, 0),
    ],
)
def test_counts_that_cannot_happen_are_refused(confirmed: int, done: int, overdue: int) -> None:
    with pytest.raises(ValidationError):
        MeetingActionProgress(meeting_id="mtg_a1", confirmed=confirmed, done=done, overdue=overdue)


def test_a_meeting_is_named_by_id_only() -> None:
    """Counts and an id: no assignee, no title, no item id (#605 review)."""
    assert set(MeetingActionProgress.model_fields) == {"meeting_id", "confirmed", "done", "overdue"}
    with pytest.raises(ValidationError):
        MeetingActionProgress(meeting_id="act_1", confirmed=1, done=0, overdue=0)


def test_an_empty_snapshot_is_representable() -> None:
    """Fresh and empty is a fact (nothing confirmed), distinct from stale (unknown)."""
    snapshot = TeamActionProgress.model_validate(
        fixtures.load("team_action_progress") | {"meetings": []}
    )
    assert snapshot.meetings == []


def test_as_of_carries_an_offset() -> None:
    """Snapshots are compared by ``as_of``; a naive time against an aware one raises."""
    payload = fixtures.load("team_action_progress") | {"as_of": "2026-10-01T09:00:00"}
    with pytest.raises(ValidationError):
        TeamActionProgress.model_validate(payload)
    snapshot = TeamActionProgress.model_validate(fixtures.load("team_action_progress"))
    assert isinstance(snapshot.as_of, datetime) and snapshot.as_of.tzinfo is not None


def test_a_meeting_appears_once() -> None:
    payload = fixtures.load("team_action_progress")
    payload["meetings"] = [payload["meetings"][0], payload["meetings"][0]]
    with pytest.raises(ValidationError):
        TeamActionProgress.model_validate(payload)


def test_the_snapshot_is_version_checked_like_a_meeting_payload() -> None:
    validate_major_version(TeamActionProgress.model_validate(fixtures.load("team_action_progress")))
