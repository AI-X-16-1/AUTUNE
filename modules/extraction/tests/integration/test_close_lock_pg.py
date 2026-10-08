"""Two closes of one item at once leave one close, on PostgreSQL (review of #979).

``tools.close_action_item`` reads the item, decides it is open, and writes the
close. Without a lock on the row a second close reads the item while the first
is still uncommitted, finds it open too, and writes a second ``closed`` event
when the first lets go. Here the first is held after its write until the
second is waiting for the row; the item must end with one ``closed`` event and
the second must be told it is already closed.

The team is committed, because both transactions have to see it, and removed at
the end (its meeting and items cascade).

The board's route (``POST /action-items/{id}/close``) makes the same read and
the same write, and is held to the same answer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, Meeting, Team, TeamMember, User, current_user
from autune_extraction import service, tools
from autune_extraction.models import ExtActionItem, ExtEditEvent
from autune_extraction.router import router


@pytest.fixture
def open_item(db_engine: sa.Engine) -> Iterator[tuple[str, str]]:
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        meeting = Meeting(team_id=team.id, title="회의")
        session.add(meeting)
        session.flush()
        item = ExtActionItem(
            meeting_id=meeting.id,
            description="접을 일",
            status="todo",
            confidence=0.9,
            origin="model",
        )
        session.add(item)
        session.commit()
        ids = (team.id, item.id)
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids[0]))
            session.commit()


def someone_waits_for_a_row(engine: sa.Engine) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND query LIKE '%ext_action_items%'"
                )
            ).scalar()
        )


def test_two_closes_at_once_leave_one_close(
    db_engine: sa.Engine, open_item: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    team_id, item_id = open_item
    first_wrote = threading.Event()
    second_decided = threading.Event()
    release_first = threading.Event()
    real_close = service.close_without_finishing

    def close(session: Session, item: ExtActionItem) -> bool:
        if threading.current_thread().name == "second":
            # Reached only when the second read the item as open.
            second_decided.set()
            return real_close(session, item)
        closed = real_close(session, item)
        session.flush()
        first_wrote.set()
        assert release_first.wait(timeout=10)
        return closed

    monkeypatch.setattr(tools.service, "close_without_finishing", close)
    monkeypatch.setattr(tools.tasks, "sync_after_confirmation", lambda _item_id: None)
    results: dict[str, Any] = {}

    def run() -> None:
        results[threading.current_thread().name] = tools.close_action_item(team_id, item_id)

    first = threading.Thread(target=run, name="first")
    first.start()
    assert first_wrote.wait(timeout=10), "the first close never wrote"
    second = threading.Thread(target=run, name="second")
    second.start()
    deadline = time.monotonic() + 10
    while not (someone_waits_for_a_row(db_engine) or second_decided.is_set()):
        assert time.monotonic() < deadline, "the second close neither waited nor ran"
        time.sleep(0.05)
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    with Session(db_engine) as session:
        kinds = list(
            session.scalars(
                sa.select(ExtEditEvent.kind).where(ExtEditEvent.action_item_id == item_id)
            )
        )
    assert kinds == ["closed"], "one close, not two"
    assert results["first"]["ok"] is True
    assert (results["second"]["ok"], results["second"]["summary"]) == (
        False,
        "이미 닫힌 액션아이템입니다.",
    )


@pytest.fixture
def closer(db_engine: sa.Engine, open_item: tuple[str, str]) -> Iterator[User]:
    """A member of the item's team, committed for the same reason the team is."""
    with Session(db_engine) as session:
        user = User(email="closer@example.com", display_name="닫는 사람")
        session.add(user)
        session.flush()
        session.add(TeamMember(team_id=open_item[0], user_id=user.id))
        session.commit()
        user_id = user.id
    try:
        yield User(id=user_id, email="closer@example.com", display_name="닫는 사람")
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(TeamMember).where(TeamMember.user_id == user_id))
            session.execute(sa.delete(User).where(User.id == user_id))
            session.commit()


def test_two_closes_from_the_board_at_once_leave_one_close(
    db_engine: sa.Engine,
    open_item: tuple[str, str],
    closer: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route, on its real session: the second request waits for the row and
    is then told the item is already closed."""
    _, item_id = open_item
    first_wrote = threading.Event()
    second_decided = threading.Event()
    release_first = threading.Event()
    real_close = service.close_without_finishing

    calls: list[int] = []

    def close(session: Session, item: ExtActionItem) -> bool:
        # By order, not by thread name: a route function runs in a worker
        # thread of the server's, not in the one that sent the request.
        calls.append(1)
        if len(calls) > 1:
            # Reached only when the second read the item as open.
            second_decided.set()
            return real_close(session, item)
        closed = real_close(session, item)
        session.flush()
        first_wrote.set()
        assert release_first.wait(timeout=10)
        return closed

    monkeypatch.setattr(service, "close_without_finishing", close)
    monkeypatch.setattr(tools.tasks, "sync_after_confirmation", lambda _item_id: None)

    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/extraction")
    app.dependency_overrides[current_user] = lambda: closer
    results: dict[str, Any] = {}

    def run() -> None:
        with TestClient(app) as client:
            results[threading.current_thread().name] = client.post(
                f"/api/extraction/action-items/{item_id}/close"
            )

    first = threading.Thread(target=run, name="first")
    first.start()
    assert first_wrote.wait(timeout=10), "the first close never wrote"
    second = threading.Thread(target=run, name="second")
    second.start()
    deadline = time.monotonic() + 10
    while not (someone_waits_for_a_row(db_engine) or second_decided.is_set()):
        assert time.monotonic() < deadline, "the second close neither waited nor ran"
        time.sleep(0.05)
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    with Session(db_engine) as session:
        kinds = list(
            session.scalars(
                sa.select(ExtEditEvent.kind).where(ExtEditEvent.action_item_id == item_id)
            )
        )
    assert kinds == ["closed"], "one close, not two"
    assert results["first"].status_code == 200, results["first"].text
    assert results["second"].status_code == 409, results["second"].text
    assert results["second"].json()["error"]["message"] == "this item is already closed"
