"""A research document goes when any meeting it quotes goes (spec section 5)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.research_store import save_research_document
from autune_core import Meeting, Team, Utterance


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


def _seed(session: Session) -> dict[str, str]:
    team = Team(name="팀")
    session.add(team)
    session.flush()
    now = Meeting(team_id=team.id, title="이번 회의")
    past = Meeting(team_id=team.id, title="지난주")
    session.add_all([now, past])
    session.flush()
    quoted = Utterance(
        meeting_id=past.id, speaker_label="S", start_sec=0.0, end_sec=1.0, text="배포"
    )
    session.add(quoted)
    session.flush()
    save_research_document(session, team.id, now.id, "본문", [quoted.id])
    session.flush()
    return {"now": now.id, "past": past.id}


def test_deleting_a_quoted_past_meeting_deletes_the_document(db_session: Session) -> None:
    ids = _seed(db_session)

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": ids["past"]})

    assert count(db_session, "agent_research_documents") == 0
    assert count(db_session, "agent_research_sources") == 0


def test_deleting_the_meeting_itself_deletes_the_document(db_session: Session) -> None:
    ids = _seed(db_session)

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": ids["now"]})

    assert count(db_session, "agent_research_documents") == 0
    assert count(db_session, "agent_research_sources") == 0
