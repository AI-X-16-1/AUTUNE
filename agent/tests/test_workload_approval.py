"""Workload from a chat message to a person's approval, on module B's real tools.

``test_workload_chat.py`` stops at the stored run: two L2 reassignments proposed,
nothing run. This one carries on through plan mode (#556) and the approvals
routes (#557): the proposals wait in ``agent_pending_actions`` with ids only, a
Workload approver sees and approves one, and B's ``reassign_action_item`` moves
exactly that item -- once. The router is ``FakeRouter``; everything below it is
real, with B's actions on this test's session and its after-commit sync
recorded instead of sent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_agent import router as routes
from autune_agent.main.pending import arguments_ok
from autune_agent.models import AgentApprover, AgentPendingAction
from autune_agent.subagents.workload.graph import REASSIGN
from autune_agent.testing import FakeRouter
from autune_core import Base, Meeting, Team, TeamMember, User, current_user, get_session
from autune_core.errors import AutuneError
from autune_extraction import service, tools
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
    shared = {m.__tablename__ for m in (Team, User, TeamMember, Meeting)}
    tables = [
        t
        for name, t in Base.metadata.tables.items()
        if name in shared or name.startswith(("ext_", "agent_", "utterances"))
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def synced(session: Session, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """B's actions on this session; the sync they start afterwards recorded."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    sent: list[str] = []
    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks, "sync_after_confirmation", sent.append)
    return sent


@pytest.fixture
def team(session: Session) -> str:
    """박지영 holds 8 open (2 late), 김민경 3, 이승환 none. 김민경 approves
    Workload's proposals; 박지영 is a member with no approver scope."""
    session.add_all([Team(id="team_a", name="A팀"), Team(id="team_b", name="B팀")])
    people = {
        "user_park": "박지영",
        "user_kim": "김민경",
        "user_lee": "이승환",
        "user_other": "타팀원",
    }
    for uid, name in people.items():
        session.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
    session.flush()
    for uid in ("user_park", "user_kim", "user_lee"):
        session.add(TeamMember(team_id="team_a", user_id=uid))
    session.add(TeamMember(team_id="team_b", user_id="user_other"))
    session.add(AgentApprover(team_id="team_a", user_id="user_kim", scope="workload"))
    session.add(Meeting(id="mtg_a", team_id="team_a", title="주간 회의"))
    session.flush()
    for n in range(8):
        due = TODAY - timedelta(days=2) if n < 2 else TODAY + timedelta(days=5)
        session.add(item(f"act_park{n}", "user_park", due))
    for n in range(3):
        session.add(item(f"act_kim{n}", "user_kim", TODAY + timedelta(days=5)))
    session.commit()
    return "team_a"


def item(item_id: str, owner: str, due: date) -> ExtActionItem:
    return ExtActionItem(
        id=item_id,
        meeting_id="mtg_a",
        description="할 일",
        assignee_id=owner,
        due_date=due,
        status="todo",
        confidence=0.9,
        origin="user",
    )


def client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    def _autune_error(request: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    app.dependency_overrides[routes.get_chat_router] = lambda: FakeRouter({"업무": "workload"})
    return TestClient(app)


def ask(session: Session, team: str) -> list[AgentPendingAction]:
    reply = client(session, "user_kim").post(
        "/api/agent/chat", json={"team_id": team, "message": ASK}
    )
    assert reply.status_code == 200 and reply.json()["proposed"] == 2
    return list(session.scalars(select(AgentPendingAction).order_by(AgentPendingAction.id)))


def owner(session: Session, item_id: str) -> str | None:
    session.expire_all()
    row = session.get(ExtActionItem, item_id)
    return row.assignee_id if row else None


def test_the_proposals_wait_with_ids_only(session: Session, team: str, synced: list[str]) -> None:
    rows = ask(session, team)

    assert [(r.tool, r.scope, r.status) for r in rows] == [(REASSIGN, "workload", "pending")] * 2
    for r in rows:
        assert arguments_ok(r.arguments)
        assert set(r.arguments) == {"action_item_id", "assignee_id"}
    assert sorted(r.arguments["action_item_id"] for r in rows) == ["act_park0", "act_park1"]
    assert {r.arguments["assignee_id"] for r in rows} == {"user_lee"}
    assert owner(session, "act_park0") == "user_park", "nothing ran before approval"
    assert synced == []


def test_only_a_workload_approver_sees_them(session: Session, team: str, synced: list[str]) -> None:
    ask(session, team)

    seen = client(session, "user_kim").get("/api/agent/pending", params={"team_id": team}).json()
    assert len(seen) == 2
    assert client(session, "user_park").get("/api/agent/pending").json() == []


def test_approving_moves_that_one_item_once(session: Session, team: str, synced: list[str]) -> None:
    first, second = ask(session, team)
    moved = first.arguments["action_item_id"]

    reply = client(session, "user_kim").post(f"/api/agent/pending/{first.id}/approve")

    assert reply.status_code == 200, reply.text
    assert (reply.json()["status"], reply.json()["result_ok"]) == ("approved", True)
    assert owner(session, moved) == "user_lee"
    assert owner(session, second.arguments["action_item_id"]) == "user_park"
    assert synced == [moved], "the moved item's Notion page and calendar follow"
    again = client(session, "user_kim").post(f"/api/agent/pending/{first.id}/approve")
    assert again.status_code == 409
    assert synced == [moved], "never run twice"


def test_a_member_without_the_scope_cannot_approve(
    session: Session, team: str, synced: list[str]
) -> None:
    first, _ = ask(session, team)

    reply = client(session, "user_park").post(f"/api/agent/pending/{first.id}/approve")

    assert reply.status_code in (403, 404)
    assert owner(session, first.arguments["action_item_id"]) == "user_park"
    outsider = client(session, "user_other").post(f"/api/agent/pending/{first.id}/approve")
    assert outsider.status_code == 404, "another team's row reads as missing"


def test_rejecting_changes_nothing(session: Session, team: str, synced: list[str]) -> None:
    first, _ = ask(session, team)

    reply = client(session, "user_kim").post(
        f"/api/agent/pending/{first.id}/reject", json={"reason": "not_now"}
    )

    assert reply.status_code == 200 and reply.json()["status"] == "rejected"
    assert owner(session, first.arguments["action_item_id"]) == "user_park"
    assert synced == []


def test_a_taker_who_left_the_team_is_refused_by_b(
    session: Session, team: str, synced: list[str]
) -> None:
    first, _ = ask(session, team)
    session.execute(
        delete(TeamMember).where(TeamMember.team_id == team, TeamMember.user_id == "user_lee")
    )
    session.commit()

    reply = client(session, "user_kim").post(f"/api/agent/pending/{first.id}/approve")

    assert reply.status_code == 200
    assert (reply.json()["status"], reply.json()["result_ok"]) == ("failed", False)
    assert owner(session, first.arguments["action_item_id"]) == "user_park"
    assert synced == []
