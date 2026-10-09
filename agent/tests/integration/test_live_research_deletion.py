"""Live research documents go with their meeting, with any meeting they quote,
and the notice row with its meeting (spec section 4). PostgreSQL only."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.models import AgentLiveResearch, AgentLiveResearchNotice, AgentLiveResearchSource
from autune_core import Meeting, Team


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def _doc(session: Session, team: Team, meeting: Meeting, *, quotes: Meeting | None = None) -> str:
    doc = AgentLiveResearch(
        team_id=team.id,
        meeting_id=meeting.id,
        origin="auto",
        status="done",
        question="배포일이 언제였지?",
        body="배포일\n- 지난 회의에서 금요일로 정했습니다",
    )
    session.add(doc)
    session.flush()
    if quotes is not None:
        session.add(AgentLiveResearchSource(document_id=doc.id, meeting_id=quotes.id))
        session.flush()
    return doc.id


def _team_and_meetings(session: Session) -> tuple[Team, Meeting, Meeting]:
    team = Team(name="팀")
    session.add(team)
    session.flush()
    now = Meeting(team_id=team.id, title="이번 회의")
    past = Meeting(team_id=team.id, title="지난주")
    session.add_all([now, past])
    session.flush()
    return team, now, past


def test_a_document_goes_with_its_meeting(db_session: Session) -> None:
    team, now, _ = _team_and_meetings(db_session)
    _doc(db_session, team, now)
    db_session.add(AgentLiveResearchNotice(meeting_id=now.id))
    db_session.flush()

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": now.id})

    assert count(db_session, "agent_live_research") == 0
    assert count(db_session, "agent_live_research_notices") == 0


def test_a_document_goes_with_a_meeting_it_quotes(db_session: Session) -> None:
    team, now, past = _team_and_meetings(db_session)
    _doc(db_session, team, now, quotes=past)

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": past.id})

    assert count(db_session, "agent_live_research") == 0
    assert count(db_session, "agent_live_research_sources") == 0
