"""Follow-up from the pipeline's last event to an approved item, on a real PostgreSQL.

``tests/test_followup.py`` runs the subgraph on mock tools. This one runs it the
way the 10/9 demo does (spec section 8, end to end), with everything below the
event real: C detects the gaps of two meetings of one team, the second leaving a
template item open again; ``autune.intelligence.completed`` wakes Follow-up
through ``on_event``; its proposal waits in ``agent_pending_actions`` with ids
only; the team lead approves it through the approvals routes; and B's
``add_followup_item`` puts one unconfirmed "후속 회의 잡기" item on the board.

C's detection and B's write each open their own ``session_scope`` and commit, so
the seed is committed too and the team is deleted at the end, taking its
meetings and ``agent_`` rows with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import autune_extraction.models  # noqa: F401  (ext_ tables)
import autune_gap.models  # noqa: F401  (gap_ tables)
from autune_agent import router as routes
from autune_agent.main.pending import arguments_ok
from autune_agent.main.triggers import on_event
from autune_agent.models import AgentApprover, AgentPendingAction, AgentRun
from autune_agent.subagents.followup import SUBAGENT
from autune_agent.subagents.followup.graph import OPEN_ITEM, WRITE
from autune_contracts import INTELLIGENCE_COMPLETED
from autune_core import (
    Meeting,
    Participant,
    Team,
    TeamMember,
    User,
    current_user,
    get_session,
    session_scope,
)
from autune_core.errors import AutuneError
from autune_extraction import service as extraction_service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem
from autune_gap import service as gap_service
from autune_gap.models import GapGap, GapParticipation, GapTopic

COVERS_TWO = {"핵심 지표": 1.0, "담당자": 0.9}
"""Matches ``general``'s ``success_criteria`` and ``ownership`` only, so a
meeting is left open on ``risk``, ``dependency`` and ``next_step`` -- the same
seed C's own tool tests use."""

T0 = datetime(2026, 9, 1, 10, tzinfo=UTC)
MEMBER_APPROVE = 404
"""A member who approves nothing in the team: the row reads as missing (``pending._load``)."""
SECOND_APPROVE = 409
"""An already decided proposal (``PendingDecidedError``)."""
ONLY_FOLLOWUP = {"followup": SUBAGENT}
"""Research and Report also wake on this event; they are not under test here."""


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extraction_service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session(db_engine: sa.Engine) -> Iterator[Session]:
    with Session(db_engine) as s:
        yield s


@pytest.fixture
def team(db_engine: sa.Engine) -> Iterator[dict[str, str]]:
    """A team with a lead (approver, scope ``followup``) and a member who is not."""
    with session_scope() as s:
        # A suffix per run: a run killed before teardown leaves its users behind,
        # and fixed addresses would collide with the next one.
        suffix = uuid.uuid4().hex[:8]
        row = Team(name="followup-e2e")
        lead = User(email=f"lead-{suffix}@followup-e2e.example", display_name="팀장")
        member = User(email=f"member-{suffix}@followup-e2e.example", display_name="팀원")
        s.add_all([row, lead, member])
        s.flush()
        s.add_all(
            [
                TeamMember(team_id=row.id, user_id=lead.id),
                TeamMember(team_id=row.id, user_id=member.id),
                AgentApprover(team_id=row.id, user_id=lead.id, scope="followup"),
            ]
        )
        ids = {"team": row.id, "lead": lead.id, "member": member.id}
    yield ids
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == ids["team"]))
        s.execute(delete(User).where(User.id.in_([ids["lead"], ids["member"]])))


def analysed_meeting(team_id: str, *, started: datetime) -> str:
    """A meeting whose topic graph C has built and detected. Returns its id."""
    with session_scope() as s:
        row = Meeting(team_id=team_id, title="주간 회의", status="analyzing", started_at=started)
        s.add(row)
        s.flush()
        person = Participant(meeting_id=row.id, speaker_label="화자0", consented=True)
        s.add(person)
        s.flush()
        for label, centrality in COVERS_TWO.items():
            topic = GapTopic(
                meeting_id=row.id,
                label=label,
                extractor_version="fake",
                centrality=centrality,
                betweenness=0.0,
            )
            s.add(topic)
            s.flush()
            s.add(GapParticipation(topic_id=topic.id, participant_id=person.id, spoke=True))
        meeting_id = row.id
    gap_service.detect_gaps(meeting_id)
    return meeting_id


def client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    def _autune_error(request: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app)


def wake(session: Session, meeting_id: str, task_id: str) -> list[AgentRun]:
    rows = on_event(
        INTELLIGENCE_COMPLETED,
        meeting_id,
        session=session,
        subagents=ONLY_FOLLOWUP,
        task_id=task_id,
    )
    session.commit()
    return rows


def pending(session: Session, team_id: str) -> list[AgentPendingAction]:
    session.expire_all()
    return list(
        session.scalars(
            select(AgentPendingAction)
            .where(AgentPendingAction.team_id == team_id)
            .order_by(AgentPendingAction.id)
        )
    )


def followup_items(session: Session, team_id: str) -> list[ExtActionItem]:
    session.expire_all()
    return list(
        session.scalars(
            select(ExtActionItem)
            .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
            .where(Meeting.team_id == team_id, ExtActionItem.origin == "followup")
        )
    )


def calls(run: AgentRun) -> list[tuple[str, bool]]:
    """The run's tool calls in order, and whether each answered.

    A failed read also ends Follow-up with nothing proposed, so "proposes
    nothing" means something only next to the reads that decided it."""
    return [(step["tool"], step["ok"]) for step in run.steps]


def assert_decided_on_reads(run: AgentRun) -> None:
    """Every read answered and the rule did not fire: ``OPEN_ITEM`` is read only
    when it does."""
    assert calls(run), "the run made no call"
    assert all(ok for _, ok in calls(run)), calls(run)
    assert OPEN_ITEM not in [tool for tool, _ in calls(run)], calls(run)


def gap_ids(session: Session, meeting_id: str) -> set[str]:
    return set(session.scalars(select(GapGap.id).where(GapGap.meeting_id == meeting_id)))


def test_a_first_meeting_proposes_nothing(session: Session, team: dict[str, str]) -> None:
    only = analysed_meeting(team["team"], started=T0)

    (run,) = wake(session, only, "task-1")

    # Three open gaps, none high enough twice over and nothing carried: no rule fires.
    assert run.outcome == "answered", run.outcome
    assert run.proposed == []
    assert_decided_on_reads(run)
    assert pending(session, team["team"]) == []


def test_the_pipeline_event_to_an_approved_item(session: Session, team: dict[str, str]) -> None:
    first = analysed_meeting(team["team"], started=T0)
    second = analysed_meeting(team["team"], started=T0 + timedelta(days=7))

    (run,) = wake(session, second, "task-2")

    # The proposal waits for the lead, with ids only, citing gaps of both meetings.
    (row,) = pending(session, team["team"])
    assert (row.tool, row.scope, row.status) == (WRITE, "followup", "pending")
    assert arguments_ok(row.arguments)
    assert row.meeting_id == second
    cited = set(row.evidence)
    assert cited & gap_ids(session, second)
    assert cited <= gap_ids(session, first) | gap_ids(session, second)
    assert (OPEN_ITEM, True) in calls(run), "the rule fired and no item was open"
    assert followup_items(session, team["team"]) == [], "nothing runs before approval"

    # Only the lead sees it and can approve it.
    lead = client(session, team["lead"])
    assert [p["id"] for p in lead.get("/api/agent/pending").json()] == [row.id]
    member = client(session, team["member"])
    assert member.get("/api/agent/pending").json() == []
    assert member.post(f"/api/agent/pending/{row.id}/approve").status_code == MEMBER_APPROVE
    assert followup_items(session, team["team"]) == []

    reply = lead.post(f"/api/agent/pending/{row.id}/approve")

    assert reply.status_code == 200, reply.text
    assert (reply.json()["status"], reply.json()["result_ok"]) == ("approved", True)
    (item,) = followup_items(session, team["team"])
    assert item.meeting_id == second
    assert item.description == "후속 회의 잡기"
    assert item.status == "needs_confirmation", "it reaches nobody until confirmed"
    # No assignee: the lead picks one. The date is the one the card suggested.
    assert item.assignee_id is None
    assert item.due_date is not None
    assert item.due_date.isoformat() == row.arguments["due_date"]

    # A second approval writes nothing more.
    assert lead.post(f"/api/agent/pending/{row.id}/approve").status_code == SECOND_APPROVE
    assert len(followup_items(session, team["team"])) == 1


def test_a_republished_event_proposes_nothing_while_the_item_is_open(
    session: Session, team: dict[str, str]
) -> None:
    analysed_meeting(team["team"], started=T0)
    second = analysed_meeting(team["team"], started=T0 + timedelta(days=7))
    wake(session, second, "task-3")
    (row,) = pending(session, team["team"])
    assert client(session, team["lead"]).post(f"/api/agent/pending/{row.id}/approve").is_success

    # E republishes (a new task): Follow-up runs again and finds its item open.
    (again,) = wake(session, second, "task-4")

    assert again.proposed == []
    # It got as far as B's read and stopped on the open item, not on a failed read.
    assert calls(again)[-1] == (OPEN_ITEM, True), calls(again)
    assert all(ok for _, ok in calls(again)), calls(again)
    assert [p.status for p in pending(session, team["team"])] == ["approved"]
    assert len(followup_items(session, team["team"])) == 1


def test_a_gap_dismissed_before_the_event_is_not_carried(
    session: Session, team: dict[str, str]
) -> None:
    first = analysed_meeting(team["team"], started=T0)
    with session_scope() as s:
        for gap in s.scalars(select(GapGap).where(GapGap.meeting_id == first)):
            gap.dismissed_at = datetime.now(UTC)
    second = analysed_meeting(team["team"], started=T0 + timedelta(days=7))

    (run,) = wake(session, second, "task-5")

    assert run.proposed == []
    assert_decided_on_reads(run)
    assert pending(session, team["team"]) == []
