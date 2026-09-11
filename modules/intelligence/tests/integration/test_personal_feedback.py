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


def _three_speakers(session: Session, meeting_id: str) -> list[str]:
    """alice/bob/carol, all consenting, each with an utterance. Returns user ids."""
    uids = []
    for i, name in enumerate(("alice", "bob", "carol")):
        uid = _user(session, name)
        uids.append(uid)
        pid = _participant(session, meeting_id, user_id=uid, label=name.title())
        _utter(session, meeting_id, pid, i * 20.0, i * 20.0 + 20.0)
    return uids


def test_dms_each_identified_participant_their_own_ratio(db_session: Session, meeting: str) -> None:
    alice, bob, carol = _three_speakers(db_session, meeting)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 3
    assert {m.channel for m in slack.sent} == {alice, bob, carol}
    assert all(m.is_dm for m in slack.sent)


def test_a_speaker_with_no_user_account_is_skipped(db_session: Session, meeting: str) -> None:
    alice, bob, _carol = _three_speakers(db_session, meeting)
    p_ghost = _participant(db_session, meeting, user_id=None, label="Speaker 4")
    _utter(db_session, meeting, p_ghost, 100.0, 140.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 3  # alice, bob, carol — the ghost has no account to DM
    assert alice in {m.channel for m in slack.sent}
    assert bob in {m.channel for m in slack.sent}


def test_no_dm_goes_out_when_only_two_people_spoke(db_session: Session, meeting: str) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Bob")
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, p_bob, 30.0, 40.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0
    assert slack.sent == []


def test_no_dm_when_a_third_consenting_participant_only_listened(
    db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    carol = _user(db_session, "carol")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Alice")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Bob")
    _participant(db_session, meeting, user_id=carol, label="Carol")  # consented, silent
    _utter(db_session, meeting, p_alice, 0.0, 30.0)
    _utter(db_session, meeting, p_bob, 30.0, 40.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0  # only two people's speech — alice would fix bob's
    assert slack.sent == []


def test_no_dm_when_a_speaker_is_split_across_two_participant_rows(
    db_session: Session, meeting: str
) -> None:
    """Two real speakers, one split by diarization into two participant rows,
    must still be withheld as a two-person meeting — not sent as three."""
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice_1 = _participant(db_session, meeting, user_id=alice, label="Speaker 0")
    p_alice_2 = _participant(db_session, meeting, user_id=alice, label="Speaker 2")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    _utter(db_session, meeting, p_alice_1, 0.0, 20.0)
    _utter(db_session, meeting, p_alice_2, 20.0, 40.0)
    _utter(db_session, meeting, p_bob, 40.0, 60.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0
    assert slack.sent == []


def test_a_speaker_split_across_two_rows_gets_exactly_one_dm(
    db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    carol = _user(db_session, "carol")
    p_alice_1 = _participant(db_session, meeting, user_id=alice, label="Speaker 0")
    p_alice_2 = _participant(db_session, meeting, user_id=alice, label="Speaker 2")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    p_carol = _participant(db_session, meeting, user_id=carol, label="Speaker 3")
    _utter(db_session, meeting, p_alice_1, 0.0, 15.0)
    _utter(db_session, meeting, p_alice_2, 15.0, 30.0)
    _utter(db_session, meeting, p_bob, 30.0, 60.0)
    _utter(db_session, meeting, p_carol, 60.0, 90.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 3
    assert len([m for m in slack.sent if m.channel == alice]) == 1


def test_no_dm_when_the_other_speaker_is_unidentified_and_split(
    db_session: Session, meeting: str
) -> None:
    bob = _user(db_session, "bob")
    p_x1 = _participant(db_session, meeting, user_id=None, label="Speaker 0")
    p_x2 = _participant(db_session, meeting, user_id=None, label="Speaker 2")
    p_bob = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    _utter(db_session, meeting, p_x1, 0.0, 20.0)
    _utter(db_session, meeting, p_x2, 20.0, 40.0)
    _utter(db_session, meeting, p_bob, 40.0, 60.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0
    assert slack.sent == []


def test_no_dm_when_only_one_of_a_split_speakers_labels_is_identified(
    db_session: Session, meeting: str
) -> None:
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    p_alice = _participant(db_session, meeting, user_id=alice, label="Speaker 0")
    p_bob_identified = _participant(db_session, meeting, user_id=bob, label="Speaker 1")
    p_bob_unidentified = _participant(db_session, meeting, user_id=None, label="Speaker 2")
    _utter(db_session, meeting, p_alice, 0.0, 40.0)
    _utter(db_session, meeting, p_bob_identified, 40.0, 60.0)
    _utter(db_session, meeting, p_bob_unidentified, 60.0, 80.0)
    db_session.flush()
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0
    assert slack.sent == []


def test_nothing_is_persisted(db_session: Session, meeting: str) -> None:
    _three_speakers(db_session, meeting)
    db_session.flush()
    before = _count_all_intel_rows(db_session)

    sent = service.send_personal_feedback(db_session, FakeSlack(), meeting)
    db_session.flush()

    assert sent == 3
    assert _count_all_intel_rows(db_session) == before


def test_a_meeting_with_no_speech_sends_nothing(db_session: Session, meeting: str) -> None:
    slack = FakeSlack()

    sent = service.send_personal_feedback(db_session, slack, meeting)

    assert sent == 0
    assert slack.sent == []
