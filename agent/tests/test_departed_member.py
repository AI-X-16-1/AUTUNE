"""Somebody who left a team keeps nothing of its agent layer (#937, #552).

Leaving a team removes the membership and nothing else: the person's
participant rows keep their ``user_id``, so that deleting their own speech
still reaches every line of theirs (privacy.md section 4). Their name is
therefore still on the team's meetings, and whatever the layer left about them
-- an approver row, a run they asked for -- may still be there too. None of it
is a way in: every door of ``/api/agent`` asks ``team_members``.

The person here is that worst case: a participant of the team's meeting, an
``any`` approver of the team, the asker of an earlier run -- and no longer a
member. Tests only; no behaviour is changed by this file.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_agent import router as routes
from autune_agent.models import AgentApprover, AgentPendingAction, AgentResearchDocument, AgentRun
from autune_agent.testing import FakeRouter
from autune_core import (
    AutuneError,
    Participant,
    TeamMember,
    User,
    current_user,
    get_session,
)


def _client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    async def _error(_: object, exc: AutuneError) -> object:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    app.dependency_overrides[routes.get_chat_router] = lambda: FakeRouter()
    app.dependency_overrides[routes.get_chat_tool_model] = lambda: None
    return TestClient(app)


@pytest.fixture
def departed(session: Session, team: dict[str, str]) -> str:
    """A person who was on the team and left it: the membership row is gone,
    and everything else that names them is still there."""
    Participant.__table__.create(session.get_bind())
    person = User(email="departed@example.com", display_name="나간 사람")
    session.add(person)
    session.flush()
    session.add(TeamMember(team_id=team["team"], user_id=person.id))
    session.add(
        Participant(
            meeting_id=team["meeting"], user_id=person.id, speaker_label="화자 1", consented=True
        )
    )
    session.add(AgentApprover(team_id=team["team"], user_id=person.id, scope="any"))
    session.add(
        AgentRun(
            team_id=team["team"],
            meeting_id=team["meeting"],
            requested_by=person.id,
            trigger={"kind": "chat"},
            outcome="unrouted",
        )
    )
    session.commit()

    session.execute(
        delete(TeamMember).where(
            TeamMember.team_id == team["team"], TeamMember.user_id == person.id
        )
    )
    session.commit()
    return person.id


@pytest.fixture
def waiting(session: Session, team: dict[str, str]) -> AgentPendingAction:
    """A proposal waiting for an approver, as ``test_routes._queue`` makes one."""
    doc = AgentResearchDocument(team_id=team["team"], meeting_id=team["meeting"], body="본문")
    session.add(doc)
    session.flush()
    row = AgentPendingAction(
        team_id=team["team"],
        meeting_id=team["meeting"],
        subagent="research",
        tool="agent.share_research_document",
        kind="research_share",
        arguments={"document_id": doc.id},
        evidence=[doc.id],
        scope="research",
    )
    session.add(row)
    session.commit()
    return row


def test_the_person_is_what_the_docstring_says(
    session: Session, team: dict[str, str], departed: str
) -> None:
    """The fixture, checked: without this the tests below could pass for a
    person the layer has simply never heard of."""
    assert session.scalar(
        select(Participant.id).where(
            Participant.meeting_id == team["meeting"], Participant.user_id == departed
        )
    )
    assert (
        session.scalar(
            select(AgentApprover.scope).where(
                AgentApprover.team_id == team["team"], AgentApprover.user_id == departed
            )
        )
        == "any"
    )
    assert session.query(AgentRun).filter(AgentRun.requested_by == departed).count() == 1
    assert (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == team["team"], TeamMember.user_id == departed
            )
        )
        is None
    )


def test_chat_about_the_team_is_refused_and_no_run_is_recorded(
    session: Session, team: dict[str, str], departed: str
) -> None:
    client = _client(session, departed)

    reply = client.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 403
    assert session.query(AgentRun).count() == 1, "only the run from before they left"


def test_chat_from_a_meeting_they_spoke_in_reads_as_missing(
    session: Session, team: dict[str, str], departed: str
) -> None:
    """A meeting alone names its team (S34). Their participant row on that
    meeting does not: the meeting reads as missing, as to any outsider, and
    with the team named the answer is the member check's."""
    client = _client(session, departed)

    alone = client.post(
        "/api/agent/chat", json={"meeting_id": team["meeting"], "message": "이 회의"}
    )
    named = client.post(
        "/api/agent/chat",
        json={"team_id": team["team"], "meeting_id": team["meeting"], "message": "이 회의"},
    )

    assert alone.status_code == 404 and team["meeting"] not in alone.text
    assert named.status_code == 403
    assert session.query(AgentRun).count() == 1


def test_pending_lists_nothing_and_is_refused_for_the_team_by_name(
    session: Session, team: dict[str, str], departed: str, waiting: AgentPendingAction
) -> None:
    """Their approver row is still there and counts for nothing: with no team
    named the list is empty, and naming the team is refused."""
    client = _client(session, departed)

    everywhere = client.get("/api/agent/pending")
    named = client.get("/api/agent/pending", params={"team_id": team["team"]})

    assert everywhere.status_code == 200 and everywhere.json() == []
    assert named.status_code == 403
    assert waiting.id not in everywhere.text and waiting.id not in named.text


def test_approving_is_refused_and_nothing_runs(
    session: Session, team: dict[str, str], departed: str, waiting: AgentPendingAction
) -> None:
    client = _client(session, departed)

    reply = client.post(f"/api/agent/pending/{waiting.id}/approve")

    # Not a member reads as another team's row: missing, with no id echoed.
    assert reply.status_code == 404 and waiting.id not in reply.text
    session.refresh(waiting)
    assert (waiting.status, waiting.decided_by) == ("pending", None)
    doc = session.get(AgentResearchDocument, waiting.arguments["document_id"])
    assert doc is not None
    session.refresh(doc)
    assert doc.status != "approved", "the action behind the proposal did not run"


def test_rejecting_is_refused_and_the_proposal_still_waits(
    session: Session, team: dict[str, str], departed: str, waiting: AgentPendingAction
) -> None:
    client = _client(session, departed)

    reply = client.post(f"/api/agent/pending/{waiting.id}/reject", json={"reason": "not_now"})

    assert reply.status_code == 404 and waiting.id not in reply.text
    session.refresh(waiting)
    assert (waiting.status, waiting.decided_by, waiting.reject_reason) == ("pending", None, None)


DOORS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/api/agent/runs", {"params": {"team_id": "{team}"}}),
    ("GET", "/api/agent/research", {"params": {"team_id": "{team}", "meeting_id": "{meeting}"}}),
    ("GET", "/api/agent/meeting-label", {"params": {"meeting_id": "{meeting}"}}),
    ("GET", "/api/agent/approvers", {"params": {"team_id": "{team}"}}),
    (
        "PUT",
        "/api/agent/approvers/{member}",
        {"params": {"team_id": "{team}"}, "json": {"scopes": ["any"]}},
    ),
]
"""The layer's other doors, beside the four above: each takes a team or a
meeting, and each must ask the same question."""


def _filled(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, dict):
        return {key: _filled(inner, ids) for key, inner in value.items()}
    return value


@pytest.mark.parametrize(("method", "path", "request_"), DOORS, ids=[d[1] for d in DOORS])
def test_no_other_door_of_the_layer_opens(
    session: Session,
    team: dict[str, str],
    departed: str,
    method: str,
    path: str,
    request_: dict[str, Any],
) -> None:
    client = _client(session, departed)
    before = sorted(
        session.execute(select(AgentApprover.user_id, AgentApprover.scope)).tuples().all()
    )

    reply = client.request(method, _filled(path, team), **_filled(request_, team))

    assert reply.status_code in (403, 404), reply.text
    assert "주간 회의" not in reply.text, "not even the meeting's title"
    after = sorted(
        session.execute(select(AgentApprover.user_id, AgentApprover.scope)).tuples().all()
    )
    assert after == before, "and nobody's approver scopes moved"


def test_every_route_of_the_layer_is_one_of_the_doors_tried_here() -> None:
    """A route added to ``/api/agent`` fails here until somebody decides what a
    departed person gets from it and adds it above."""
    tried = {
        ("POST", "/chat"),
        ("GET", "/pending"),
        ("POST", "/pending/{pending_id}/approve"),
        ("POST", "/pending/{pending_id}/reject"),
        ("GET", "/runs"),
        ("GET", "/research"),
        ("GET", "/meeting-label"),
        ("GET", "/approvers"),
        ("PUT", "/approvers/{user_id}"),
    }

    served = {
        (method, route.path)
        for route in routes.router.routes
        for method in getattr(route, "methods", None) or ()
        if method not in ("HEAD", "OPTIONS")
    }

    assert served == tried
