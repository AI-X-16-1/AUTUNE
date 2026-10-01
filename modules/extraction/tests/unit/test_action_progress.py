"""A team's action items as counts per meeting, published for E's completion
rate (#605). SQLite in memory; ``publish`` recorded instead of sent.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_contracts import (
    ACTION_PROGRESS_PUBLISH_EVERY,
    ACTION_PROGRESS_WINDOW,
    EXTRACTION_ACTION_PROGRESS,
    TeamActionProgress,
)
from autune_core import Base, Meeting
from autune_extraction import service, tasks
from autune_extraction.models import ExtActionItem

NOW = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
TODAY = date(2026, 10, 2)
TABLES = [Meeting.__table__, ExtActionItem.__table__]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        for mid, team, age in (
            ("mtg_1", "team_1", 3),
            ("mtg_2", "team_1", 10),
            ("mtg_old", "team_1", 120),
            ("mtg_x", "team_other", 1),
            ("mtg_gone", "team_stale", 200),
        ):
            session.add(
                Meeting(id=mid, team_id=team, title="회의", created_at=NOW - timedelta(days=age))
            )
        session.flush()
        yield session


def item(
    session: Session,
    item_id: str,
    meeting_id: str,
    status: str,
    *,
    due: date | None = None,
    assignee: str | None = "user_kim",
) -> None:
    session.add(
        ExtActionItem(
            id=item_id,
            meeting_id=meeting_id,
            description=f"{item_id} 할 일",
            status=status,
            due_date=due,
            assignee_id=assignee,
            confidence=0.8,
            origin="model",
        )
    )
    session.flush()


def test_each_meeting_counts_confirmed_done_and_overdue(session: Session) -> None:
    item(session, "a1", "mtg_1", "todo", due=TODAY - timedelta(days=1))  # overdue
    item(session, "a2", "mtg_1", "in_progress", due=TODAY)  # due today: not overdue
    item(session, "a3", "mtg_1", "done", due=TODAY - timedelta(days=5))  # done is never late
    item(session, "a4", "mtg_1", "needs_confirmation", due=TODAY - timedelta(days=9))
    item(session, "b1", "mtg_2", "done")

    progress = service.team_action_progress(session, "team_1", now=NOW, today=TODAY)

    assert progress.as_of == NOW
    assert [(m.meeting_id, m.confirmed, m.done, m.overdue) for m in progress.meetings] == [
        ("mtg_1", 3, 1, 1),
        ("mtg_2", 1, 1, 0),
    ]


def test_a_meeting_with_nothing_confirmed_or_outside_the_window_is_left_out(
    session: Session,
) -> None:
    item(session, "a1", "mtg_1", "needs_confirmation")
    item(session, "o1", "mtg_old", "done")

    progress = service.team_action_progress(session, "team_1", now=NOW, today=TODAY)

    assert progress.meetings == []


def test_counts_and_meeting_ids_only_leave(session: Session) -> None:
    """No assignee, title or item id: a per-person completion record cannot be
    built from it (privacy.md section 3)."""
    item(session, "a1", "mtg_1", "todo")

    payload = service.team_action_progress(session, "team_1", now=NOW, today=TODAY).model_dump(
        mode="json"
    )
    sent = json.dumps(payload, ensure_ascii=False)

    assert "user_kim" not in sent
    assert "a1" not in sent
    assert "할 일" not in sent
    assert set(payload["meetings"][0]) == {"meeting_id", "confirmed", "done", "overdue"}


def test_teams_with_a_meeting_in_the_window_are_published(session: Session) -> None:
    assert service.teams_with_recent_meetings(session, now=NOW) == ["team_1", "team_other"]


def test_the_task_publishes_one_snapshot_per_team_empty_ones_included(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item(session, "a1", "mtg_1", "done")
    sent: list[tuple[str, dict[str, Any]]] = []

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "publish", lambda event, payload: sent.append((event, payload)))

    tasks.publish_action_progress()

    assert [event for event, _ in sent] == [EXTRACTION_ACTION_PROGRESS] * 2
    snapshots = {s.team_id: s for s in (TeamActionProgress.model_validate(p) for _, p in sent)}
    assert [m.meeting_id for m in snapshots["team_1"].meetings] == ["mtg_1"]
    assert snapshots["team_other"].meetings == [], "a fresh empty snapshot is a fact"


def test_it_runs_on_the_contracts_cadence_over_its_window() -> None:
    assert timedelta(minutes=10) == ACTION_PROGRESS_PUBLISH_EVERY
    assert timedelta(days=91) == ACTION_PROGRESS_WINDOW
