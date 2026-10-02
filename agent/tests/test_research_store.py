"""Saving a research document: one proposed per meeting, sources computed."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_agent.models import AgentResearchDocument, AgentResearchSource
from autune_agent.research_store import save_research_document, share_research_document
from autune_core import Meeting, Team, Utterance
from autune_core.errors import PrivacyViolationError


def _utterance(session: Session, meeting_id: str, text: str = "말") -> str:
    row = Utterance(
        meeting_id=meeting_id, speaker_label="SPEAKER_00", start_sec=0.0, end_sec=1.0, text=text
    )
    session.add(row)
    session.flush()
    return row.id


def _sources(session: Session, document_id: str) -> set[str]:
    return set(
        session.scalars(
            select(AgentResearchSource.meeting_id).where(
                AgentResearchSource.document_id == document_id
            )
        )
    )


def test_sources_come_from_the_utterances_and_always_include_the_meeting(
    session: Session, team: dict[str, str]
) -> None:
    past = Meeting(team_id=team["team"], title="지난주")
    session.add(past)
    session.flush()
    quoted = _utterance(session, past.id)

    result = save_research_document(
        session, team["team"], team["meeting"], "## 제기된 질문\n...", [quoted]
    )

    assert result["ok"] is True
    doc_id = result["evidence"][0]
    assert doc_id.startswith("rdoc_")
    assert _sources(session, doc_id) == {team["meeting"], past.id}


def test_an_utterance_of_another_team_never_becomes_a_source(
    session: Session, team: dict[str, str]
) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    theirs = Meeting(team_id=other.id, title="남의 회의")
    session.add(theirs)
    session.flush()
    foreign = _utterance(session, theirs.id)

    result = save_research_document(session, team["team"], team["meeting"], "본문", [foreign])

    assert result["ok"] is False
    assert result["reason"] == "source changed"
    assert session.scalars(select(AgentResearchDocument)).all() == []
    assert session.scalars(select(AgentResearchSource)).all() == []


def test_an_utterance_that_no_longer_exists_refuses_the_save(
    session: Session, team: dict[str, str]
) -> None:
    kept = _utterance(session, team["meeting"])

    result = save_research_document(
        session, team["team"], team["meeting"], "본문", [kept, "utt_gone"]
    )

    assert result["ok"] is False
    assert result["reason"] == "source changed"
    assert session.scalars(select(AgentResearchDocument)).all() == []


def test_a_repeated_utterance_id_counts_once(session: Session, team: dict[str, str]) -> None:
    kept = _utterance(session, team["meeting"])

    result = save_research_document(session, team["team"], team["meeting"], "본문", [kept, kept])

    assert result["ok"] is True


def test_a_second_save_before_a_decision_overwrites_the_proposal(
    session: Session, team: dict[str, str]
) -> None:
    first = save_research_document(session, team["team"], team["meeting"], "첫 판", [])
    second = save_research_document(session, team["team"], team["meeting"], "둘째 판", [])

    assert first["evidence"] == second["evidence"]
    docs = session.scalars(select(AgentResearchDocument)).all()
    assert [d.body for d in docs] == ["둘째 판"]


def test_an_approved_document_is_never_overwritten(session: Session, team: dict[str, str]) -> None:
    first = save_research_document(session, team["team"], team["meeting"], "승인된 판", [])
    share_research_document(session, team["team"], first["evidence"][0])

    second = save_research_document(session, team["team"], team["meeting"], "새 판", [])

    assert second["evidence"] != first["evidence"]
    bodies = {d.status: d.body for d in session.scalars(select(AgentResearchDocument))}
    assert bodies == {"approved": "승인된 판", "proposed": "새 판"}


def test_sharing_another_teams_document_reads_as_missing(
    session: Session, team: dict[str, str]
) -> None:
    doc_id = save_research_document(session, team["team"], team["meeting"], "본문", [])["evidence"][
        0
    ]

    result = share_research_document(session, "team_other", doc_id)

    assert result["ok"] is False
    assert result["reason"] == "document not found"


def test_an_empty_body_is_refused(session: Session, team: dict[str, str]) -> None:
    result = save_research_document(session, team["team"], team["meeting"], "   ", [])

    assert result["ok"] is False


def test_an_unmasked_body_is_never_stored(session: Session, team: dict[str, str]) -> None:
    # The writer's output is a model's text: it can carry a value the masker
    # missed in the quotes, or one the model made up (#525 review).
    with pytest.raises(PrivacyViolationError) as raised:
        save_research_document(
            session, team["team"], team["meeting"], "담당자 연락처 010-1234-5678", []
        )

    assert "010" not in str(raised.value)
    assert session.scalars(select(AgentResearchDocument)).all() == []


def test_the_database_holds_one_proposed_document_per_meeting(
    session: Session, team: dict[str, str]
) -> None:
    # The save overwrites in code; the index keeps two racing saves from both inserting.
    session.add(AgentResearchDocument(team_id=team["team"], meeting_id=team["meeting"], body="1"))
    session.flush()
    session.add(AgentResearchDocument(team_id=team["team"], meeting_id=team["meeting"], body="2"))

    with pytest.raises(IntegrityError):
        session.flush()
