"""A person's edit to a meeting report, from the dashboard to an approved post (#674).

The model's draft waits in ``agent_pending_actions`` for a ``report`` approver.
A team member who is not one edits the draft on E's card. The edit is announced
(``autune.intelligence.meeting_report_changed``), the Report subagent wakes on
it through ``on_event`` with E's real tools, and proposes the post of the edited
draft. The earlier proposal leaves the queue, the member cannot approve the new
one, and the approver's approval posts exactly the edited draft -- the path the
#642 review asked for, with no post from the card.

E's actions open their own ``session_scope`` and commit, so the seed is
committed too and the team is deleted at the end, taking its meetings and
``agent_`` and ``intel_`` rows with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import autune_intelligence.models  # noqa: F401  (intel_ tables)
from autune_agent import router as routes
from autune_agent.main.triggers import on_event
from autune_agent.models import AgentApprover, AgentPendingAction
from autune_agent.subagents.report import SUBAGENT
from autune_agent.subagents.report.graph import CORRECTION_ACTION, PUBLISH_ACTION
from autune_contracts import INTELLIGENCE_COMPLETED, INTELLIGENCE_MEETING_REPORT_CHANGED
from autune_core import (
    Meeting,
    Team,
    TeamMember,
    User,
    current_user,
    get_session,
    session_scope,
)
from autune_core.errors import AutuneError
from autune_intelligence import service as intelligence_service
from autune_intelligence import tasks as intelligence_tasks
from autune_intelligence.models import IntelMeetingReport

ONLY_REPORT = {"report": SUBAGENT}
MEMBER_APPROVE = 404
"""A member who approves nothing in the team: the row reads as missing (``pending._load``)."""


@pytest.fixture
def session(db_engine: sa.Engine) -> Iterator[Session]:
    with Session(db_engine) as s:
        yield s


@pytest.fixture
def team(db_engine: sa.Engine) -> Iterator[dict[str, str]]:
    """A team with a lead (approver, scope ``report``), a member who is not, and
    a meeting whose model draft is waiting for approval."""
    with session_scope() as s:
        suffix = uuid.uuid4().hex[:8]
        row = Team(name="report-edit-e2e")
        lead = User(email=f"lead-{suffix}@report-edit-e2e.example", display_name="팀장")
        member = User(email=f"member-{suffix}@report-edit-e2e.example", display_name="팀원")
        s.add_all([row, lead, member])
        s.flush()
        meeting = Meeting(
            team_id=row.id, title="결제 회의", started_at=datetime(2026, 10, 2, 5, tzinfo=UTC)
        )
        s.add(meeting)
        s.add_all(
            [
                TeamMember(team_id=row.id, user_id=lead.id),
                TeamMember(team_id=row.id, user_id=member.id),
                AgentApprover(team_id=row.id, user_id=lead.id, scope="report"),
            ]
        )
        s.flush()
        document = intelligence_service.meeting_report_document(meeting, "✅ 모델이 쓴 본문")
        intelligence_service.save_meeting_report(s, meeting.id, document, draft_id="rdr_model")
        # The model run's post proposal, as plan mode queued it.
        s.add(
            AgentPendingAction(
                team_id=row.id,
                meeting_id=meeting.id,
                subagent="report",
                tool=PUBLISH_ACTION,
                kind="meeting_report_post",
                arguments={"draft_id": "rdr_model"},
                evidence=[],
                scope="report",
            )
        )
        ids = {"team": row.id, "lead": lead.id, "member": member.id, "meeting": meeting.id}
    yield ids
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == ids["team"]))
        s.execute(delete(User).where(User.id.in_([ids["lead"], ids["member"]])))


def client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    def _autune_error(request: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app)


def pending(session: Session, team_id: str) -> list[AgentPendingAction]:
    session.expire_all()
    return list(
        session.scalars(
            select(AgentPendingAction)
            .where(AgentPendingAction.team_id == team_id)
            .order_by(AgentPendingAction.created_at, AgentPendingAction.id)
        )
    )


def test_an_edit_reaches_the_channel_only_through_a_report_approver(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, ...]] = []
    monkeypatch.setattr(intelligence_tasks.deliver_meeting_report, "apply_async", posted.append)

    # A member who approves nothing edits the draft on the card.
    with session_scope() as s:
        intelligence_service.edit_meeting_report(
            s, team["meeting"], "✅ 팀원이 고친 본문", user_id=team["member"]
        )
    edited = session.get(IntelMeetingReport, team["meeting"])
    assert edited is not None and edited.draft_id not in (None, "rdr_model")

    # The announcement wakes the Report subagent on E's real tools.
    on_event(
        INTELLIGENCE_MEETING_REPORT_CHANGED,
        team["meeting"],
        session=session,
        subagents=ONLY_REPORT,
        task_id="task-edit-1",
    )
    session.commit()

    old, new = pending(session, team["team"])
    assert (old.arguments["draft_id"], old.status) == ("rdr_model", "superseded")
    assert (new.arguments, new.status, new.scope) == (
        {"draft_id": edited.draft_id},
        "pending",
        "report",
    )

    # The editor cannot approve it; the approver's approval posts the edited draft.
    member = client(session, team["member"])
    assert member.post(f"/api/agent/pending/{new.id}/approve").status_code == MEMBER_APPROVE
    reply = client(session, team["lead"]).post(f"/api/agent/pending/{new.id}/approve")

    assert reply.is_success and reply.json()["result_ok"] is True
    assert posted == [(team["meeting"], edited.draft_id)]


def test_a_correction_reaches_the_thread_only_through_a_report_approver(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the post a person's change is a correction; it takes the same road (#658)."""
    queued: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        intelligence_tasks.deliver_meeting_report_correction, "apply_async", queued.append
    )
    with session_scope() as s:
        # The model's draft was approved and posted; its proposal is done with.
        s.execute(
            sa.update(AgentPendingAction)
            .where(AgentPendingAction.team_id == team["team"])
            .values(status="approved")
        )
        intelligence_service.claim_meeting_report(s, team["meeting"], draft_id="rdr_model")
        intelligence_service.record_meeting_report_post(s, team["meeting"], "C123", "1.000100")
    with session_scope() as s:
        intelligence_service.correct_meeting_report(
            s, team["meeting"], "✅ 기한을 10/3으로 바로잡습니다", user_id=team["member"]
        )
    row = session.get(IntelMeetingReport, team["meeting"])
    assert row is not None and row.correction_id is not None

    on_event(
        INTELLIGENCE_MEETING_REPORT_CHANGED,
        team["meeting"],
        session=session,
        subagents=ONLY_REPORT,
        task_id="task-correction-1",
    )
    session.commit()

    [new] = [p for p in pending(session, team["team"]) if p.status == "pending"]
    assert (new.tool, new.arguments, new.scope) == (
        CORRECTION_ACTION,
        {"correction_id": row.correction_id},
        "report",
    )
    member = client(session, team["member"])
    assert member.post(f"/api/agent/pending/{new.id}/approve").status_code == MEMBER_APPROVE
    reply = client(session, team["lead"]).post(f"/api/agent/pending/{new.id}/approve")

    assert reply.is_success and reply.json()["result_ok"] is True
    assert queued == [(team["meeting"], row.correction_id)]


def test_a_late_analysis_after_the_post_leaves_the_correction_waiting(
    session: Session, team: dict[str, str]
) -> None:
    """B, C or D finish late after the post: the waiting correction stays in the queue (#658)."""
    with session_scope() as s:
        s.execute(
            sa.update(AgentPendingAction)
            .where(AgentPendingAction.team_id == team["team"])
            .values(status="approved")
        )
        intelligence_service.claim_meeting_report(s, team["meeting"], draft_id="rdr_model")
        intelligence_service.record_meeting_report_post(s, team["meeting"], "C123", "1.000100")
        intelligence_service.correct_meeting_report(
            s, team["meeting"], "✅ 기한을 10/3으로 바로잡습니다", user_id=team["member"]
        )
    on_event(
        INTELLIGENCE_MEETING_REPORT_CHANGED,
        team["meeting"],
        session=session,
        subagents=ONLY_REPORT,
        task_id="task-correction-2",
    )
    session.commit()

    on_event(
        INTELLIGENCE_COMPLETED,
        team["meeting"],
        session=session,
        subagents=ONLY_REPORT,
        task_id="task-late-analysis",
    )
    session.commit()

    [waiting] = [p for p in pending(session, team["team"]) if p.status == "pending"]
    assert waiting.tool == CORRECTION_ACTION
    assert [p.status for p in pending(session, team["team"])].count("superseded") == 0
