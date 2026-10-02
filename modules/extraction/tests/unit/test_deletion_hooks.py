"""B's deletion hooks: due-date events off people's calendars (#588).

An account deletion (#582) removes that person's events at once, with their own
grant, and never stops the deletion. A meeting's expiry (#581) only queues its
events; ``drain_calendar_cleanup`` removes them later. Notion and Jira copies
are the team's records and are not touched. A fake calendar stands in for
Google.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, Meeting, User
from autune_core.deletion import registered_modules
from autune_extraction import tasks
from autune_extraction.models import ExtActionItem, ExtCalendarCleanup, ExtCalendarEvent
from autune_integrations import PermanentIntegrationError, TransientIntegrationError


class _Calendar:
    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.fail = fail or {}

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        if event_id in self.fail:
            raise self.fail[event_id]
        self.deleted.append((calendar_id, event_id))


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        for uid in ("user_kim", "user_lee"):
            s.add(User(id=uid, email=f"{uid}@example.com", display_name=uid))
        s.add(Meeting(id="mtg_1", team_id="team_1", title="주간 회의"))
        s.add(Meeting(id="mtg_2", team_id="team_1", title="다른 회의"))
        s.flush()
        for item, meeting, user, event in (
            ("act_1", "mtg_1", "user_kim", "ev_1"),
            ("act_2", "mtg_1", "user_lee", "ev_2"),
            ("act_3", "mtg_2", "user_kim", "ev_3"),
        ):
            s.add(
                ExtActionItem(
                    id=item,
                    meeting_id=meeting,
                    description="할 일",
                    status="todo",
                    confidence=0.9,
                    origin="model",
                )
            )
            s.flush()
            s.add(
                ExtCalendarEvent(
                    action_item_id=item,
                    meeting_id=meeting,
                    user_id=user,
                    event_id=event,
                    synced_due_date=date(2026, 10, 9),
                )
            )
        s.commit()
        yield s


@pytest.fixture
def calendars(session: Session, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """This session for the hooks; each person's own calendar, or none."""
    state: dict[str, Any] = {"by_user": {"user_kim": _Calendar(), "user_lee": _Calendar()}}

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    @contextmanager
    def lookup(_: Session) -> Iterator[Any]:
        def calendar_for(user_id: str) -> tuple[_Calendar, str] | None:
            found = state["by_user"].get(user_id)
            if isinstance(found, Exception):
                raise found
            return (found, "primary") if found is not None else None

        yield calendar_for

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "_calendars", lookup)
    monkeypatch.setattr(tasks, "_google_client_configured", lambda: True)
    return state


def events(session: Session) -> list[str]:
    session.expire_all()
    return sorted(e.event_id or "" for e in session.scalars(select(ExtCalendarEvent)))


def queued(session: Session) -> list[tuple[str, str]]:
    session.expire_all()
    return sorted((q.user_id, q.event_id) for q in session.scalars(select(ExtCalendarCleanup)))


def test_both_hooks_are_registered() -> None:
    meeting_hooks, user_hooks = registered_modules()
    assert "extraction" in meeting_hooks and "extraction" in user_hooks


def test_an_account_deletion_removes_that_persons_events_now(
    session: Session, calendars: dict[str, Any]
) -> None:
    tasks.forget_user_calendar_events("user_kim")

    assert sorted(calendars["by_user"]["user_kim"].deleted) == [
        ("primary", "ev_1"),
        ("primary", "ev_3"),
    ]
    assert calendars["by_user"]["user_lee"].deleted == [], "nobody else's calendar"
    assert events(session) == ["ev_2"]


def test_it_also_removes_events_queued_for_them(
    session: Session, calendars: dict[str, Any]
) -> None:
    session.add(ExtCalendarCleanup(user_id="user_kim", event_id="ev_old"))
    session.commit()

    tasks.forget_user_calendar_events("user_kim")

    assert ("primary", "ev_old") in calendars["by_user"]["user_kim"].deleted
    assert queued(session) == []


@pytest.mark.parametrize(
    "trouble",
    [
        PermanentIntegrationError("refused"),
        TransientIntegrationError("down"),
        RuntimeError("anything at all"),
    ],
)
def test_an_account_deletion_never_stops_on_the_calendar(
    session: Session, calendars: dict[str, Any], trouble: Exception
) -> None:
    calendars["by_user"]["user_kim"] = trouble

    tasks.forget_user_calendar_events("user_kim")  # must not raise (#582)


def test_running_it_twice_is_harmless(session: Session, calendars: dict[str, Any]) -> None:
    tasks.forget_user_calendar_events("user_kim")
    tasks.forget_user_calendar_events("user_kim")

    assert len(calendars["by_user"]["user_kim"].deleted) == 2


def test_a_meeting_expiry_only_queues_its_events(
    session: Session, calendars: dict[str, Any]
) -> None:
    tasks.queue_meeting_calendar_events("mtg_1")
    tasks.queue_meeting_calendar_events("mtg_1")  # twice: still one row each

    assert queued(session) == [("user_kim", "ev_1"), ("user_lee", "ev_2")]
    assert calendars["by_user"]["user_kim"].deleted == [], "no Google call in the sweep"


def test_the_drain_removes_queued_events_with_each_owners_grant(
    session: Session, calendars: dict[str, Any]
) -> None:
    tasks.queue_meeting_calendar_events("mtg_1")

    assert tasks.drain_calendar_cleanup() == 2

    assert calendars["by_user"]["user_kim"].deleted == [("primary", "ev_1")]
    assert calendars["by_user"]["user_lee"].deleted == [("primary", "ev_2")]
    assert queued(session) == []


def test_a_transient_failure_is_retried_then_given_up(
    session: Session, calendars: dict[str, Any]
) -> None:
    calendars["by_user"]["user_kim"] = _Calendar({"ev_1": TransientIntegrationError("down")})
    tasks.queue_meeting_calendar_events("mtg_1")

    for _ in range(tasks.CLEANUP_MAX_ATTEMPTS - 1):
        tasks.drain_calendar_cleanup()
    assert queued(session) == [("user_kim", "ev_1")], "kept while it may still work"

    tasks.drain_calendar_cleanup()
    assert queued(session) == []


@pytest.mark.parametrize("owner", [None, PermanentIntegrationError("refused")])
def test_an_event_nothing_can_remove_is_dropped(
    session: Session, calendars: dict[str, Any], owner: Exception | None
) -> None:
    calendars["by_user"]["user_kim"] = owner
    tasks.queue_meeting_calendar_events("mtg_1")

    tasks.drain_calendar_cleanup()

    assert queued(session) == [], "retrying forever would help nobody"


def test_an_unexpected_error_on_one_row_does_not_block_the_queue(
    session: Session, calendars: dict[str, Any]
) -> None:
    calendars["by_user"]["user_kim"] = _Calendar({"ev_1": ValueError("not an IntegrationError")})
    tasks.queue_meeting_calendar_events("mtg_1")

    assert tasks.drain_calendar_cleanup() == 1  # user_lee's still goes

    assert queued(session) == [("user_kim", "ev_1")], "kept and counted, not the batch lost"
    for _ in range(tasks.CLEANUP_MAX_ATTEMPTS - 1):
        tasks.drain_calendar_cleanup()
    assert queued(session) == []


def test_without_a_google_client_the_queue_is_kept_not_dropped(
    session: Session, calendars: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "_google_client_configured", lambda: False)
    tasks.queue_meeting_calendar_events("mtg_1")

    assert tasks.drain_calendar_cleanup() == 0

    assert queued(session) == [("user_kim", "ev_1"), ("user_lee", "ev_2")]
