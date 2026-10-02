"""B's action-item counts per meeting, kept for E's real completion rate (#605).

E keeps the latest ``TeamActionProgress`` per team: a header with ``as_of`` and
one row per meeting. The dashboard reads it in a later change; these tests
cover the store and the task.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts import TeamActionProgress
from autune_intelligence import service, tasks
from autune_intelligence.models import IntelActionProgress, IntelActionProgressMeeting

AS_OF = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _meeting(db_session: Session, team: str) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team, title="회의", started_at=AS_OF - timedelta(days=1))
    db_session.add(row)
    db_session.flush()
    return row.id


def _snapshot(
    team: str, meetings: list[dict[str, Any]], as_of: datetime = AS_OF
) -> TeamActionProgress:
    return TeamActionProgress(team_id=team, as_of=as_of, meetings=meetings)


def _rows(db_session: Session, team: str) -> dict[str, tuple[int, int, int]]:
    rows = db_session.scalars(
        sa.select(IntelActionProgressMeeting).where(IntelActionProgressMeeting.team_id == team)
    )
    return {r.meeting_id: (r.confirmed, r.done, r.overdue) for r in rows}


def test_a_snapshot_is_kept_with_its_time(db_session: Session, team: str) -> None:
    first, second = _meeting(db_session, team), _meeting(db_session, team)

    kept = service.store_action_progress(
        db_session,
        _snapshot(
            team,
            [
                {"meeting_id": first, "confirmed": 5, "done": 2, "overdue": 1},
                {"meeting_id": second, "confirmed": 3, "done": 3, "overdue": 0},
            ],
        ),
    )

    assert kept is True
    header = db_session.get(IntelActionProgress, team)
    assert header is not None and header.as_of == AS_OF
    assert _rows(db_session, team) == {first: (5, 2, 1), second: (3, 3, 0)}


def test_a_newer_snapshot_replaces_every_row(db_session: Session, team: str) -> None:
    """A meeting missing from the next snapshot is gone, not left at its old counts."""
    first, second = _meeting(db_session, team), _meeting(db_session, team)
    service.store_action_progress(
        db_session,
        _snapshot(
            team,
            [
                {"meeting_id": first, "confirmed": 5, "done": 2, "overdue": 1},
                {"meeting_id": second, "confirmed": 3, "done": 0, "overdue": 0},
            ],
        ),
    )

    service.store_action_progress(
        db_session,
        _snapshot(
            team,
            [{"meeting_id": first, "confirmed": 5, "done": 4, "overdue": 0}],
            as_of=AS_OF + timedelta(minutes=10),
        ),
    )

    assert _rows(db_session, team) == {first: (5, 4, 0)}
    header = db_session.get(IntelActionProgress, team)
    assert header is not None and header.as_of == AS_OF + timedelta(minutes=10)


def test_an_older_snapshot_arriving_late_changes_nothing(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team)
    service.store_action_progress(
        db_session,
        _snapshot(team, [{"meeting_id": meeting, "confirmed": 2, "done": 2, "overdue": 0}]),
    )

    kept = service.store_action_progress(
        db_session,
        _snapshot(
            team,
            [{"meeting_id": meeting, "confirmed": 2, "done": 0, "overdue": 2}],
            as_of=AS_OF - timedelta(minutes=10),
        ),
    )

    assert kept is False
    assert _rows(db_session, team) == {meeting: (2, 2, 0)}


def test_a_fresh_empty_snapshot_is_kept_as_a_fact(db_session: Session, team: str) -> None:
    """Nothing confirmed in the window: the header moves on and no row is left."""
    meeting = _meeting(db_session, team)
    service.store_action_progress(
        db_session,
        _snapshot(team, [{"meeting_id": meeting, "confirmed": 1, "done": 0, "overdue": 0}]),
    )

    service.store_action_progress(
        db_session, _snapshot(team, [], as_of=AS_OF + timedelta(minutes=10))
    )

    header = db_session.get(IntelActionProgress, team)
    assert header is not None and header.as_of == AS_OF + timedelta(minutes=10)
    assert _rows(db_session, team) == {}


def test_a_meeting_of_another_team_or_none_is_not_kept(db_session: Session, team: str) -> None:
    """B names meetings by id; E keeps only the team's own, so no row points elsewhere."""
    from autune_core import Team

    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    mine, theirs = _meeting(db_session, team), _meeting(db_session, other.id)

    service.store_action_progress(
        db_session,
        _snapshot(
            team,
            [
                {"meeting_id": mine, "confirmed": 1, "done": 1, "overdue": 0},
                {"meeting_id": theirs, "confirmed": 1, "done": 0, "overdue": 0},
                {"meeting_id": "mtg_gone", "confirmed": 1, "done": 0, "overdue": 0},
            ],
        ),
    )

    assert _rows(db_session, team) == {mine: (1, 1, 0)}


def test_a_team_that_no_longer_exists_is_not_kept(db_session: Session) -> None:
    assert service.store_action_progress(db_session, _snapshot("team_gone", [])) is False


def test_deleting_a_meeting_deletes_its_counts(db_session: Session, team: str) -> None:
    """Every intel_ row has a deletion path (#86): a meeting's counts go with it."""
    from autune_core import Meeting

    meeting = _meeting(db_session, team)
    service.store_action_progress(
        db_session,
        _snapshot(team, [{"meeting_id": meeting, "confirmed": 1, "done": 0, "overdue": 0}]),
    )

    db_session.execute(sa.delete(Meeting).where(Meeting.id == meeting))
    db_session.expire_all()

    assert _rows(db_session, team) == {}


@pytest.fixture
def use_test_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def _scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)


@pytest.mark.usefixtures("use_test_session")
def test_the_task_validates_and_keeps_the_payload(db_session: Session, team: str) -> None:
    meeting = _meeting(db_session, team)
    payload = _snapshot(
        team, [{"meeting_id": meeting, "confirmed": 4, "done": 1, "overdue": 1}]
    ).model_dump(mode="json")

    tasks.on_extraction_action_progress(payload)

    assert _rows(db_session, team) == {meeting: (4, 1, 1)}


@pytest.mark.usefixtures("use_test_session")
def test_the_task_refuses_an_invalid_payload_and_keeps_nothing(
    db_session: Session, team: str
) -> None:
    """Loud like E's other consumers; the next snapshot, ten minutes on, replaces it."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        tasks.on_extraction_action_progress({"team_id": team, "as_of": "not a time"})

    assert db_session.get(IntelActionProgress, team) is None
