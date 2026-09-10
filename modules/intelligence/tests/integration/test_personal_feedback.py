"""service.send_personal_feedback: DM each identified participant their own ratio.

Delivery only — the ratio is computed, sent, and dropped. This test asserts
that nothing lands in an intel_ table and that the delivery goes through
``assert_personal_delivery`` (FakeSlack enforces it).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_integrations.fakes import FakeSlack
from autune_intelligence import service


def _user(session: Session, name: str) -> str:
    from autune_core import User

    row = User(email=f"{name}@example.com", display_name=name)
    session.add(row)
    session.flush()
    return row.id


def _participant(session: Session, meeting_id: str, *, user_id: str | None, label: str) -> str:
    from autune_core import Participant

    row = Participant(meeting_id=meeting_id, user_id=user_id, speaker_label=label, consented=True)
    session.add(row)
    session.flush()
    return row.id


def _utter(session: Session, meeting_id: str, participant_id: str | None, start, end) -> None:
    from autune_core import Utterance

    session.add(
        Utterance(
            meeting_id=meeting_id,
            participant_id=participant_id,
            speaker_label="s",
            start_sec=start,
            end_sec=end,
            text="[말씀]",
        )
    )


def _count_all_intel_rows(session: Session) -> int:
    from autune_intelligence.models import (
        IntelAlignment,
        IntelCompletion,
        IntelGapPattern,
        IntelPrediction,
        IntelReport,
        IntelScore,
    )

    return sum(
        session.scalar(sa.select(sa.func.count()).select_from(model)) or 0
        for model in (
            IntelCompletion,
            IntelScore,
            IntelGapPattern,
            IntelAlignment,
            IntelPrediction,
            IntelReport,
        )
    )


def test_dms_each_identified_participant_their_own_ratio(db_session: Session, meeting: str) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Bob")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, p_bob, 30.0, 40.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 2
    recipients = {m.channel for m in slack.sent}
    assert recipients == {alice, bob}
    assert all(m.is_dm for m in slack.sent)


def test_a_speaker_with_no_user_account_is_skipped(db_session: Session, meeting: str) -> None:
    alice = _user(db_session, "alice")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    p_ghost = _participant(db_session, meeting, user_id=None, label="Speaker 2")
    _utter(db_session, meeting, p_alice, 0.0, 20.0)
    _utter(db_session, meeting, p_ghost, 20.0, 40.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 1
    assert {m.channel for m in slack.sent} == {alice}


def test_nothing_is_persisted(db_session: Session, meeting: str) -> None:
    alice = _user(db_session, "alice")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    db_session.flush()
    before = _count_all_intel_rows(db_session)

    service.send_personal_feedback(db_session, FakeSlack(), meeting)
    db_session.flush()

    assert _count_all_intel_rows(db_session) == before


def test_a_meeting_with_no_speech_sends_nothing(db_session: Session, meeting: str) -> None:
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0
    assert slack.sent == []
