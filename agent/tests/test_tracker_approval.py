"""Tracker ("할 일 챙김", #856) from a chat message to a person's approval, on
module B's real tools.

``test_tracker.py`` holds the rules on a mock. This one starts from rows: B's
``stalled_action_items`` finds the items, the proposals wait in
``agent_pending_actions`` with ids only and scope ``any``, the manager reads
each card -- built from B's ``action_item_status`` when it is read -- and an
approval runs B's ``set_action_item_due_date`` on exactly that item, once.
The router is ``FakeRouter``; everything below it is real, with B's actions on
this test's session and its after-commit sync recorded instead of sent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

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
from autune_agent.main.preview import GONE
from autune_agent.models import AgentApprover, AgentPendingAction
from autune_agent.subagents.tracker import graph, plan
from autune_agent.subagents.tracker.graph import SET_DUE_DATE
from autune_agent.testing import FakeRouter
from autune_core import Base, Meeting, Team, TeamMember, User, current_user, get_session
from autune_core.errors import AutuneError
from autune_extraction import service, tools
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem

TODAY = date.today()
ASK = "밀린 할 일 정리해줘"
DRAFT = "아무도 확정하지 않은 초안 문장"


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


def held(session: Session, meeting_id: str, days_ago: int) -> None:
    session.add(
        Meeting(
            id=meeting_id,
            team_id="team_a",
            title=f"{days_ago}일 전 회의",
            status="complete",
            started_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    session.flush()


def item(
    item_id: str, meeting: str, *, due: date | None = None, status: str = "todo"
) -> ExtActionItem:
    return ExtActionItem(
        id=item_id,
        meeting_id=meeting,
        description=DRAFT if status == "needs_confirmation" else f"{item_id} 할 일",
        assignee_id="user_park",
        due_date=due,
        status=status,
        confidence=0.9,
        origin="user",
    )


@pytest.fixture
def team(session: Session) -> str:
    """A meeting a month ago and three held since, so an item of the first has
    been carried through three. 박지영 holds every item; 김민경 is the manager
    (``any``); 이승환 approves Workload's proposals only."""
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
    session.add(AgentApprover(team_id="team_a", user_id="user_kim", scope="any"))
    session.add(AgentApprover(team_id="team_a", user_id="user_lee", scope="workload"))
    held(session, "mtg_then", 30)
    for n, days_ago in enumerate((20, 10, 5)):
        held(session, f"mtg_later{n}", days_ago)
    session.add_all(
        [
            item("act_both", "mtg_then", due=TODAY - timedelta(days=9)),
            item("act_late", "mtg_later2", due=TODAY - timedelta(days=2)),
            item("act_carried", "mtg_then"),
            item("act_moving", "mtg_later2", due=TODAY + timedelta(days=2)),
            item("act_wait", "mtg_later2", status="needs_confirmation"),
        ]
    )
    session.flush()
    waiting = session.get(ExtActionItem, "act_wait")
    assert waiting is not None
    waiting.created_at = datetime.now(UTC) - timedelta(days=5)
    session.commit()
    return "team_a"


def client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    def _autune_error(request: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    app.dependency_overrides[routes.get_chat_router] = lambda: FakeRouter({"정리": "tracker"})
    return TestClient(app)


def ask(session: Session, team: str, *, proposed: int = 2) -> dict[str, AgentPendingAction]:
    """Ask once; the waiting rows by the item each is about."""
    reply = client(session, "user_kim").post(
        "/api/agent/chat", json={"team_id": team, "message": ASK}
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["proposed"] == proposed
    rows = session.scalars(select(AgentPendingAction).where(AgentPendingAction.status == "pending"))
    return {r.arguments["action_item_id"]: r for r in rows}


def row_of(session: Session, item_id: str) -> ExtActionItem:
    session.expire_all()
    row = session.get(ExtActionItem, item_id)
    assert row is not None
    return row


def cards(session: Session, user_id: str, team: str) -> dict[str, dict[str, str]]:
    seen = client(session, user_id).get("/api/agent/pending", params={"team_id": team})
    assert seen.status_code == 200, seen.text
    return {card["id"]: card for card in seen.json()}


def test_the_proposals_wait_for_the_manager_with_ids_only(
    session: Session, team: str, synced: list[str]
) -> None:
    rows = ask(session, team)

    assert {i: (r.tool, r.kind, r.scope, r.subagent) for i, r in rows.items()} == {
        "act_both": (SET_DUE_DATE, plan.MOVE, "any", "tracker"),
        "act_late": (SET_DUE_DATE, plan.MOVE, "any", "tracker"),
    }, "not the moving item, not the unconfirmed one, and not the one only carried"
    moved_to = plan.new_due_date(graph._today()).isoformat()
    for r in rows.values():
        assert arguments_ok(r.arguments)
        assert r.arguments == {
            "action_item_id": r.arguments["action_item_id"],
            "due_date": moved_to,
        }
    assert row_of(session, "act_late").due_date == TODAY - timedelta(days=2), "nothing ran yet"
    assert synced == []


def test_the_card_says_whose_item_and_what_changes(
    session: Session, team: str, synced: list[str]
) -> None:
    rows = ask(session, team)

    shown = cards(session, "user_kim", team)

    move = shown[rows["act_late"].id]
    assert move["title"] == "기한 옮기기"
    assert "act_late 할 일" in move["body"] and "박지영" in move["body"]
    assert "기한 지남" in move["body"]
    assert f"→ 새 기한: {plan.spoken(plan.new_due_date(graph._today()))}" in move["body"]
    for card in shown.values():
        assert DRAFT not in card["title"] + card["body"]


def test_approving_a_move_changes_that_items_date_once(
    session: Session, team: str, synced: list[str]
) -> None:
    rows = ask(session, team)

    reply = client(session, "user_kim").post(f"/api/agent/pending/{rows['act_late'].id}/approve")

    assert reply.status_code == 200, reply.text
    assert (reply.json()["status"], reply.json()["result_ok"]) == ("approved", True)
    moved = row_of(session, "act_late")
    assert moved.due_date == plan.new_due_date(graph._today())
    assert moved.status == "todo", "a move never closes an item"
    assert row_of(session, "act_both").due_date == TODAY - timedelta(days=9), "the other waits"
    assert synced == ["act_late"], "the item's Notion page and calendar follow"
    again = client(session, "user_kim").post(f"/api/agent/pending/{rows['act_late'].id}/approve")
    assert again.status_code == 409
    assert synced == ["act_late"], "never run twice"


def test_no_approval_marks_an_item_done(session: Session, team: str, synced: list[str]) -> None:
    # Closing is not proposed until B can mark a close apart from finished
    # work (the user, 2026-10-07): approving everything leaves every status.
    rows = ask(session, team)

    for row in rows.values():
        reply = client(session, "user_kim").post(f"/api/agent/pending/{row.id}/approve")
        assert reply.status_code == 200, reply.text

    session.expire_all()
    statuses = {r.id: r.status for r in session.scalars(select(ExtActionItem))}
    assert statuses == {
        "act_both": "todo",
        "act_late": "todo",
        "act_carried": "todo",
        "act_moving": "todo",
        "act_wait": "needs_confirmation",
    }


def test_only_a_person_holding_any_sees_or_decides_them(
    session: Session, team: str, synced: list[str]
) -> None:
    rows = ask(session, team)
    first = rows["act_late"].id

    assert len(cards(session, "user_kim", team)) == 2
    for user_id in ("user_lee", "user_park"):
        assert cards(session, user_id, team) == {}, "Workload's approver is not the manager"
        reply = client(session, user_id).post(f"/api/agent/pending/{first}/approve")
        assert reply.status_code in (403, 404)
    outsider = client(session, "user_other").post(f"/api/agent/pending/{first}/approve")
    assert outsider.status_code == 404, "another team's row reads as missing"
    assert row_of(session, "act_late").due_date == TODAY - timedelta(days=2)
    assert synced == []


def test_refusing_changes_nothing(session: Session, team: str, synced: list[str]) -> None:
    rows = ask(session, team)

    reply = client(session, "user_kim").post(
        f"/api/agent/pending/{rows['act_both'].id}/reject", json={"reason": "not_now"}
    )

    assert reply.status_code == 200 and reply.json()["status"] == "rejected"
    assert row_of(session, "act_both").due_date == TODAY - timedelta(days=9)
    assert synced == []


def test_a_later_run_replaces_the_cards_still_waiting(
    session: Session, team: str, synced: list[str]
) -> None:
    first = ask(session, team)
    client(session, "user_kim").post(f"/api/agent/pending/{first['act_both'].id}/approve")

    second = ask(session, team, proposed=1)

    assert set(second) == {"act_late"}, "the item whose date was moved is no longer late"
    assert second["act_late"].id != first["act_late"].id
    session.expire_all()
    statuses = {
        r.arguments["action_item_id"]: r.status
        for r in session.scalars(
            select(AgentPendingAction).where(
                AgentPendingAction.id.in_([r.id for r in first.values()])
            )
        )
    }
    assert statuses == {"act_both": "approved", "act_late": "superseded"}
    assert len(cards(session, "user_kim", team)) == 1, "one card an item, never two"


def test_an_item_deleted_since_reads_as_gone_and_its_approval_changes_nothing(
    session: Session, team: str, synced: list[str]
) -> None:
    rows = ask(session, team)
    session.execute(delete(ExtActionItem).where(ExtActionItem.id == "act_late"))
    session.commit()

    assert cards(session, "user_kim", team)[rows["act_late"].id]["body"] == GONE
    reply = client(session, "user_kim").post(f"/api/agent/pending/{rows['act_late'].id}/approve")

    assert reply.status_code == 200
    assert (reply.json()["status"], reply.json()["result_ok"]) == ("failed", False)
    assert synced == []
