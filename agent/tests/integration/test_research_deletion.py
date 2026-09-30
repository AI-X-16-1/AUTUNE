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


def test_an_utterance_deleted_before_the_save_refuses_it(db_session: Session) -> None:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    now = Meeting(team_id=team.id, title="이번 회의")
    past = Meeting(team_id=team.id, title="지난주")
    db_session.add_all([now, past])
    db_session.flush()
    quoted = Utterance(
        meeting_id=past.id, speaker_label="S", start_sec=0.0, end_sec=1.0, text="배포"
    )
    db_session.add(quoted)
    db_session.flush()
    quoted_id = quoted.id
    db_session.execute(sa.text("DELETE FROM utterances WHERE id = :id"), {"id": quoted_id})
    db_session.expunge_all()

    result = save_research_document(db_session, team.id, now.id, "본문", [quoted_id])

    assert result["ok"] is False
    assert result["reason"] == "source changed"
    assert count(db_session, "agent_research_documents") == 0
    assert count(db_session, "agent_research_sources") == 0


def test_an_overwrite_adds_sources_and_keeps_the_document(db_session: Session) -> None:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    now = Meeting(team_id=team.id, title="이번 회의")
    p1 = Meeting(team_id=team.id, title="첫째 회의")
    p2 = Meeting(team_id=team.id, title="둘째 회의")
    db_session.add_all([now, p1, p2])
    db_session.flush()
    u1 = Utterance(meeting_id=p1.id, speaker_label="S", start_sec=0.0, end_sec=1.0, text="배포")
    u2 = Utterance(meeting_id=p2.id, speaker_label="S", start_sec=0.0, end_sec=1.0, text="금요일")
    db_session.add_all([u1, u2])
    db_session.flush()

    first = save_research_document(db_session, team.id, now.id, "첫 판", [u1.id])
    second = save_research_document(db_session, team.id, now.id, "둘째 판", [u2.id])
    db_session.flush()

    assert first["ok"] is True and second["ok"] is True
    assert first["evidence"] == second["evidence"]
    doc_id = first["evidence"][0]
    sources = set(
        db_session.execute(
            sa.text("SELECT meeting_id FROM agent_research_sources WHERE document_id = :id"),
            {"id": doc_id},
        ).scalars()
    )
    assert sources == {now.id, p1.id, p2.id}
    body = db_session.execute(
        sa.text("SELECT body FROM agent_research_documents WHERE id = :id"), {"id": doc_id}
    ).scalar_one()
    assert body == "둘째 판"
