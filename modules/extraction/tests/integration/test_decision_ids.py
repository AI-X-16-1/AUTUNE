"""A rebuilt decision keeps its ``dec_`` id, on a real PostgreSQL (#171).

The unit suite runs on SQLite, which enforces no foreign keys and forgives a
primary key reused inside one transaction differently. What is tested here is
the path a reprocessed meeting takes in production: delete the meeting's
decisions and rebuild them in the same transaction, with ids that repeat.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Meeting, Team, Utterance
from autune_extraction import service
from autune_extraction.decisions import ClassifiedUtterance

K = UtteranceKind


@pytest.fixture
def meeting(db_session: Session) -> tuple[str, list[str]]:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="주간 회의")
    db_session.add(meeting)
    db_session.flush()
    said = [
        Utterance(meeting_id=meeting.id, speaker_label="S1", start_sec=s, end_sec=s + 2, text=t)
        for s, t in ((0.0, "A안으로 가죠"), (3.0, "네 그렇게 하기로 했습니다"), (6.0, "다른 얘기"))
    ]
    db_session.add_all(said)
    db_session.flush()
    return meeting.id, [utterance.id for utterance in said]


def labelled(ids: list[str], kinds: list[K | None]) -> list[ClassifiedUtterance]:
    return [
        ClassifiedUtterance(id=uid, kind=kind, confidence=0.9, text="...")
        for uid, kind in zip(ids, kinds, strict=True)
    ]


def decision_rows(session: Session, meeting_id: str) -> list[tuple[str, int]]:
    """Each decision's id with how many source rows point at it."""
    return [
        (row.id, row.sources)
        for row in session.execute(
            sa.text(
                "SELECT d.id, count(s.id) AS sources FROM ext_decisions d "
                "LEFT JOIN ext_decision_sources s ON s.decision_id = d.id "
                "WHERE d.meeting_id = :m GROUP BY d.id ORDER BY d.id"
            ),
            {"m": meeting_id},
        )
    ]


def test_rebuilding_the_same_labels_keeps_the_id_and_one_set_of_sources(
    db_session: Session, meeting: tuple[str, list[str]]
) -> None:
    meeting_id, ids = meeting
    labels = labelled(ids, [K.DECISION, K.DECISION, None])

    service.build_decisions(db_session, meeting_id=meeting_id, utterances=labels)
    first = decision_rows(db_session, meeting_id)
    db_session.expire_all()
    service.build_decisions(db_session, meeting_id=meeting_id, utterances=labels)

    assert decision_rows(db_session, meeting_id) == first
    assert [sources for _id, sources in first] == [2]


def test_a_decision_whose_sources_changed_gets_a_new_id_and_the_old_one_is_gone(
    db_session: Session, meeting: tuple[str, list[str]]
) -> None:
    meeting_id, ids = meeting

    service.build_decisions(
        db_session, meeting_id=meeting_id, utterances=labelled(ids, [K.DECISION, K.DECISION, None])
    )
    ((before, _),) = decision_rows(db_session, meeting_id)
    service.build_decisions(
        db_session, meeting_id=meeting_id, utterances=labelled(ids, [None, K.DECISION, None])
    )
    ((after, sources),) = decision_rows(db_session, meeting_id)

    assert after != before
    assert sources == 1
    orphaned = db_session.execute(
        sa.text("SELECT count(*) FROM ext_decision_sources WHERE decision_id = :d"), {"d": before}
    ).scalar_one()
    assert orphaned == 0
