"""The CHECK constraints refuse what they were written to refuse, on PostgreSQL.

The unit suite asserts constraint *names* on the model; SQLite would accept the
rows these reject. Each case is written as raw SQL so nothing in the ORM or the
service can stop it first -- the database is the last line, and this is where
it is tested.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Meeting, Team, Utterance


@pytest.fixture
def ids(db_session: Session) -> dict[str, str]:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="회의")
    db_session.add(meeting)
    db_session.flush()
    utterance = Utterance(
        meeting_id=meeting.id, speaker_label="S1", start_sec=0.0, end_sec=1.0, text="네"
    )
    db_session.add(utterance)
    db_session.flush()
    return {"meeting": meeting.id, "utterance": utterance.id}


def refused(session: Session, sql: str, params: dict[str, object], constraint: str) -> None:
    with pytest.raises(IntegrityError) as caught, session.begin_nested():
        session.execute(sa.text(sql), params)
    assert constraint in str(caught.value.orig)


CLASSIFICATION = (
    "INSERT INTO ext_classifications "
    "(utterance_id, meeting_id, kind, confidence, model_version, nli_verified) "
    "VALUES (:u, :m, :kind, :confidence, 'test', false)"
)


def test_none_is_not_a_stored_kind(db_session: Session, ids: dict[str, str]) -> None:
    """An utterance the model calls none has no row (#149) -- and cannot get one."""
    refused(
        db_session,
        CLASSIFICATION,
        {"u": ids["utterance"], "m": ids["meeting"], "kind": "none", "confidence": 0.9},
        "ck_ext_classifications_kind",
    )


def test_a_classification_confidence_is_a_probability(
    db_session: Session, ids: dict[str, str]
) -> None:
    refused(
        db_session,
        CLASSIFICATION,
        {"u": ids["utterance"], "m": ids["meeting"], "kind": "decision", "confidence": 1.5},
        "ck_ext_classifications_confidence",
    )


def test_one_utterance_has_one_classification(db_session: Session, ids: dict[str, str]) -> None:
    """The primary key is the idempotency key the pipeline relies on."""
    params = {"u": ids["utterance"], "m": ids["meeting"], "kind": "decision", "confidence": 0.5}
    db_session.execute(sa.text(CLASSIFICATION), params)

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(sa.text(CLASSIFICATION), params)


ITEM = (
    "INSERT INTO ext_action_items (id, meeting_id, description, status, confidence, origin) "
    "VALUES ('act_x', :m, '할 일', :status, 0.5, :origin)"
)


@pytest.mark.parametrize(
    ("status", "origin", "constraint"),
    [
        ("finished", "model", "ck_ext_action_items_status"),
        ("todo", "import", "ck_ext_action_items_origin"),
    ],
)
def test_an_item_status_and_origin_are_closed_sets(
    db_session: Session, ids: dict[str, str], status: str, origin: str, constraint: str
) -> None:
    refused(db_session, ITEM, {"m": ids["meeting"], "status": status, "origin": origin}, constraint)


def test_an_answer_is_a_kind_and_a_time_together(db_session: Session, ids: dict[str, str]) -> None:
    """Half an answer cannot be dated or has nothing to date."""
    refused(
        db_session,
        "INSERT INTO ext_confirmations (utterance_id, meeting_id, reason, sent_at, resolved_kind) "
        "VALUES (:u, :m, 'weak_assent', now(), 'commitment')",
        {"u": ids["utterance"], "m": ids["meeting"]},
        "ck_ext_confirmations_answer_is_whole",
    )


def test_an_edit_event_names_what_a_person_did_and_nothing_else(
    db_session: Session, ids: dict[str, str]
) -> None:
    refused(
        db_session,
        "INSERT INTO ext_edit_events (meeting_id, kind) VALUES (:m, 'viewed')",
        {"m": ids["meeting"]},
        "ck_ext_edit_events_kind",
    )
