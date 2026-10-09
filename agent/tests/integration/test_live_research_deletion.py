"""Live research documents go with their meeting, with any meeting they quote,
and a notice row with its meeting or its person (spec section 4). PostgreSQL only."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.live.deletion import forget_live_research
from autune_agent.models import AgentLiveResearch, AgentLiveResearchNotice, AgentLiveResearchSource
from autune_core import Meeting, Team, User, Utterance


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


def _user(session: Session, email: str = "live-notice@example.com") -> User:
    user = User(email=email, display_name="팀원")
    session.add(user)
    session.flush()
    return user


def test_a_document_goes_with_its_meeting(db_session: Session) -> None:
    team, now, _ = _team_and_meetings(db_session)
    _doc(db_session, team, now)
    db_session.add(AgentLiveResearchNotice(meeting_id=now.id, user_id=_user(db_session).id))
    db_session.flush()

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": now.id})

    assert count(db_session, "agent_live_research") == 0
    assert count(db_session, "agent_live_research_notices") == 0


def test_a_notice_row_goes_with_its_person(db_session: Session) -> None:
    _, now, _ = _team_and_meetings(db_session)
    user = _user(db_session)
    db_session.add(AgentLiveResearchNotice(meeting_id=now.id, user_id=user.id))
    db_session.flush()

    db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user.id})

    assert count(db_session, "agent_live_research_notices") == 0


def test_a_document_goes_with_a_meeting_it_quotes(db_session: Session) -> None:
    team, now, past = _team_and_meetings(db_session)
    _doc(db_session, team, now, quotes=past)

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": past.id})

    assert count(db_session, "agent_live_research") == 0
    assert count(db_session, "agent_live_research_sources") == 0


def test_deleting_a_persons_speech_takes_the_live_documents_of_their_meetings(
    db_session: Session,
) -> None:
    team, now, past = _team_and_meetings(db_session)
    in_now = _doc(db_session, team, now)
    quoting_past = _doc(db_session, team, now, quotes=past)
    other = Meeting(team_id=team.id, title="다른 회의")
    db_session.add(other)
    db_session.flush()
    kept = _doc(db_session, team, other)
    spoken = Utterance(meeting_id=past.id, speaker_label="S", start_sec=0.0, end_sec=1.0, text="말")
    db_session.add(spoken)
    db_session.flush()

    gone = forget_live_research(db_session, [spoken.id])

    assert gone == 1
    remaining = set(db_session.execute(sa.text("SELECT id FROM agent_live_research")).scalars())
    assert remaining == {in_now, kept}
    assert quoting_past not in remaining

    db_session.add(
        Utterance(meeting_id=now.id, speaker_label="S", start_sec=0.0, end_sec=1.0, text="말")
    )
    db_session.flush()
    now_utt = db_session.execute(
        sa.text("SELECT id FROM utterances WHERE meeting_id = :m"), {"m": now.id}
    ).scalar_one()
    assert forget_live_research(db_session, [now_utt]) == 1
    assert forget_live_research(db_session, [now_utt]) == 0
