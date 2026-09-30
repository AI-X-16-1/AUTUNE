"""Workload from a chat message to the stored row, on module B's real tools.

``test_workload.py`` drives the subgraph with mock tools. This one goes through
``POST /api/agent/chat`` with nothing mocked below the router: the collected
subagents, B's registered ``workload_by_owner`` and ``person_action_items``, and
B's tables. The router is ``FakeRouter`` -- which subagent a sentence reaches is
the model's job and is tested with the model, not here.

What it pins: the shapes B's tools return are the shapes ``plan.py`` reads; the
team comes from the chat request after the membership check, never from the
subagent; the proposals stay L2 and nothing is written to B; and the stored row
keeps ids, not names.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_agent import router as routes
from autune_agent.models import AgentRun
from autune_agent.subagents.workload.graph import ITEMS, LOAD, REASSIGN
from autune_agent.testing import FakeRouter
from autune_core import Base, Meeting, Team, TeamMember, User, Utterance, current_user, get_session
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem

TODAY = date.today()
ASK = "업무 몰린 사람 있어?"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Team, User, TeamMember, Meeting, Utterance)}
    tables = [
        t
        for name, t in Base.metadata.tables.items()
        if name in shared or name.startswith(("ext_", "agent_"))
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def teams(session: Session) -> dict[str, str]:
    """Team A: 박지영 holds 8 open (2 late), 김민경 3, 이승환 none.
    Team B: one person holding 4 -- a one-person team is never "loaded"."""
    team, other = Team(id="team_a", name="A팀"), Team(id="team_b", name="B팀")
    people = {
        "u_park": "박지영",
        "u_kim": "김민경",
        "u_lee": "이승환",
        "u_other": "타팀원",
    }
    session.add_all([team, other])
    for uid, name in people.items():
        session.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
    session.flush()
    for uid in ("u_park", "u_kim", "u_lee"):
        session.add(TeamMember(team_id=team.id, user_id=uid))
    session.add(TeamMember(team_id=other.id, user_id="u_other"))
    session.add(Meeting(id="mtg_a", team_id=team.id, title="주간 회의"))
    session.add(Meeting(id="mtg_b", team_id=other.id, title="다른 팀 회의"))
    session.flush()

    def item(item_id: str, owner: str, meeting: str, *, late: bool = False) -> None:
        due = TODAY - timedelta(days=2) if late else TODAY + timedelta(days=5)
        session.add(
            ExtActionItem(
                id=item_id,
                meeting_id=meeting,
                description=f"{people[owner]}의 할 일",
                assignee_id=owner,
                due_date=due,
                status="todo",
                confidence=0.9,
                origin="user",
            )
        )

    for n in range(8):
        item(f"act_park{n}", "u_park", "mtg_a", late=n < 2)
    for n in range(3):
        item(f"act_kim{n}", "u_kim", "mtg_a")
    for n in range(4):
        item(f"act_other{n}", "u_other", "mtg_b")
    session.commit()
    return {"team": team.id, "other": other.id}


def _client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    app.dependency_overrides[routes.get_chat_router] = lambda: FakeRouter({"업무": "workload"})
    return TestClient(app)


def _assignees(session: Session) -> dict[str, str | None]:
    rows = session.execute(select(ExtActionItem.id, ExtActionItem.assignee_id))
    return {item_id: assignee for item_id, assignee in rows}


def test_a_chat_turn_reaches_workload_and_proposes_the_late_items(
    session: Session, teams: dict[str, str]
) -> None:
    reply = _client(session, "u_kim").post(
        "/api/agent/chat", json={"team_id": teams["team"], "message": ASK}
    )

    assert reply.status_code == 200
    body = reply.json()
    assert body["route"] == "workload"
    assert body["outcome"] == "answered"
    assert body["answer"].startswith("팀원 3명 중 몰림 1명, 여유 1명.")
    assert body["proposed"] == 2
    assert body["executed"] == 0
    # The two late items of the loaded person go to the one person with none.
    assert {f["id"] for f in body["items"]} == {"act_park0", "act_park1"}


def test_the_row_holds_l2_reassignments_by_id_and_nothing_ran(
    session: Session, teams: dict[str, str]
) -> None:
    before = _assignees(session)

    body = (
        _client(session, "u_kim")
        .post("/api/agent/chat", json={"team_id": teams["team"], "message": ASK})
        .json()
    )

    run = session.get(AgentRun, body["run_id"])
    assert run is not None
    assert run.team_id == teams["team"]
    assert [s["tool"] for s in run.steps] == [LOAD, ITEMS]
    assert [(p["tool"], p["level"]) for p in run.proposed] == [(REASSIGN, "L2")] * 2
    assert sorted(e for p in run.proposed for e in p["evidence"]) == ["act_park0", "act_park1"]
    assert run.actions == []
    assert _assignees(session) == before
    stored = json.dumps([run.steps, run.proposed, run.actions], ensure_ascii=False)
    for name in ("박지영", "김민경", "이승환"):
        assert name not in stored


def test_the_team_is_the_one_asked_about_and_nothing_crosses(
    session: Session, teams: dict[str, str]
) -> None:
    body = (
        _client(session, "u_other")
        .post("/api/agent/chat", json={"team_id": teams["other"], "message": ASK})
        .json()
    )

    assert body["route"] == "workload"
    assert body["answer"].startswith("팀원 1명 중 몰림 0명")
    assert body["proposed"] == 0
    assert body["items"] == []
