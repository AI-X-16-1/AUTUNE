"""Workload from a chat message to the stored row, on a real PostgreSQL.

``tests/test_workload_chat.py`` runs the same path on SQLite. This one is for
what SQLite does not check: B's tool queries on PostgreSQL, ids from
``new_id`` rather than hand-written ones, the foreign keys of an ``agent_runs``
row, and its JSON columns read back from the database.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_agent import router as routes
from autune_agent.models import AgentRun
from autune_agent.subagents.workload.graph import ITEMS, LOAD, REASSIGN
from autune_agent.testing import FakeRouter
from autune_core import Meeting, Team, TeamMember, User, current_user, get_session
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem

TODAY = date.today()


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


def _seed(session: Session) -> dict[str, str]:
    """박지영 holds 8 open (2 late), 김민경 3, 이승환 none."""
    team = Team(name="A팀")
    names = ("박지영", "김민경", "이승환")
    users = {n: User(email=f"{n}@example.com", display_name=n) for n in names}
    session.add_all([team, *users.values()])
    session.flush()
    session.add_all([TeamMember(team_id=team.id, user_id=u.id) for u in users.values()])
    meeting = Meeting(team_id=team.id, title="주간 회의")
    session.add(meeting)
    session.flush()
    late: list[str] = []
    for owner, count in (("박지영", 8), ("김민경", 3)):
        for n in range(count):
            row = ExtActionItem(
                meeting_id=meeting.id,
                description=f"{owner}의 할 일",
                assignee_id=users[owner].id,
                due_date=TODAY - timedelta(days=2) if owner == "박지영" and n < 2 else None,
                status="todo",
                confidence=0.9,
                origin="user",
            )
            session.add(row)
            session.flush()
            if row.due_date is not None:
                late.append(row.id)
    session.flush()
    return {"team": team.id, "asker": users["김민경"].id, "free": users["이승환"].id} | {
        f"late{i}": item_id for i, item_id in enumerate(late)
    }


def _assignees(session: Session) -> dict[str, str | None]:
    rows = session.execute(select(ExtActionItem.id, ExtActionItem.assignee_id))
    return {item_id: assignee for item_id, assignee in rows}


def test_a_chat_turn_proposes_by_id_and_writes_nothing_to_b(db_session: Session) -> None:
    seed = _seed(db_session)
    before = _assignees(db_session)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_user] = lambda: db_session.get(User, seed["asker"])
    app.dependency_overrides[routes.get_chat_router] = lambda: FakeRouter({"업무": "workload"})

    body = (
        TestClient(app)
        .post("/api/agent/chat", json={"team_id": seed["team"], "message": "업무 몰린 사람 있어?"})
        .json()
    )

    assert body["route"] == "workload"
    assert body["proposed"] == 2
    assert body["executed"] == 0
    assert {f["id"] for f in body["items"]} == {seed["late0"], seed["late1"]}
    db_session.expire_all()
    run = db_session.scalars(select(AgentRun).where(AgentRun.id == body["run_id"])).one()
    assert run.team_id == seed["team"]
    assert run.requested_by == seed["asker"]
    assert [s["tool"] for s in run.steps] == [LOAD, ITEMS]
    assert [(p["tool"], p["level"]) for p in run.proposed] == [(REASSIGN, "L2")] * 2
    assert sorted(e for p in run.proposed for e in p["evidence"]) == sorted(
        [seed["late0"], seed["late1"]]
    )
    stored = json.dumps([run.steps, run.proposed, run.actions], ensure_ascii=False)
    for name in ("박지영", "김민경", "이승환"):
        assert name not in stored
    after = _assignees(db_session)
    assert after == before
