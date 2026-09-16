"""Opening a confirmation and answering it, against a real session.

SQLite in memory, one table. The rules under test are about what a second call
does — a Slack retry, a re-sent DM, a click after the meeting was deleted — and
none of them are visible without a session that actually remembers the first
call.

This is not the integration suite: no Postgres, no migrations, no fixtures. It
buys the one thing the pure tests cannot have, which is a store.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Base
from autune_extraction.confirmations import WEAK_ASSENT, ConfirmationResponse
from autune_extraction.models import PENDING, RESOLVED, UNDECIDED, ExtConfirmation
from autune_extraction.service import (
    ambiguous_agreements_for_meeting,
    open_confirmation,
    resolve_confirmation,
)

MEETING = "mtg_1"
UTTERANCE = "utt_1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[ExtConfirmation.__table__])
    with Session(engine) as session:
        yield session


def answer(kind: UtteranceKind, utterance_id: str = UTTERANCE) -> ConfirmationResponse:
    return ConfirmationResponse(
        utterance_id=utterance_id, resolved_kind=kind, responder_id="U_SPEAKER"
    )


# --- asking twice -----------------------------------------------------------


def test_asking_twice_leaves_one_question(session: Session) -> None:
    """The primary key is the idempotency key, and this is what it buys."""
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)

    assert session.query(ExtConfirmation).count() == 1


def test_re_sending_does_not_restart_the_deadline(session: Session) -> None:
    """The clock measures how long the speaker has had the question.

    A Celery retry means they had it the whole time. Restarting it would hand the
    pipeline another day of looking undecided for free, every time a send was
    retried.
    """
    first = open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    assert first is not None
    original_sent_at = first.sent_at

    again = open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)

    assert again is None, "the clock was already running, so this call claimed nothing"
    stored = session.get(ExtConfirmation, UTTERANCE)
    assert stored is not None
    assert stored.sent_at == original_sent_at


def test_the_second_call_claims_nothing_so_no_second_dm_goes_out(session: Session) -> None:
    """``None`` is the whole signal ``ask_for_confirmation`` sends on.

    Two runs can reach one utterance -- a redelivered task (``acks_late``), or a
    rerun overlapping a manual send. Both used to read no ``sent_at`` and both
    used to write one, and the speaker got the same question twice (#198).
    """
    claims = [
        open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE) for _ in range(3)
    ]

    assert [claim is not None for claim in claims] == [True, False, False]


def test_re_sending_does_not_erase_an_answer(session: Session) -> None:
    """Someone who already replied keeps their reply, and gets no second DM."""
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    assert open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE) is None

    row = session.get(ExtConfirmation, UTTERANCE)
    assert row is not None
    assert row.resolved_kind == "commitment"
    assert row.outcome_at() == RESOLVED


# --- answering --------------------------------------------------------------


def test_an_answer_resolves_the_question(session: Session) -> None:
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)

    row = resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    assert row is not None
    assert row.resolved_kind == "commitment"
    assert row.responded_at is not None
    assert row.outcome_at() == RESOLVED


def test_the_same_click_twice_is_the_same_state(session: Session) -> None:
    """Slack retries a click it has not heard back from within three seconds."""
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)

    first = resolve_confirmation(session, answer(UtteranceKind.DECISION))
    assert first is not None
    recorded_at = first.responded_at

    second = resolve_confirmation(session, answer(UtteranceKind.DECISION))

    assert second is not None
    assert second.resolved_kind == "decision"
    assert session.query(ExtConfirmation).count() == 1
    assert recorded_at is not None


def test_a_changed_mind_wins(session: Session) -> None:
    """Two different clicks are a person correcting themselves, not a retry.

    Refusing the second would leave the speaker looking at a button that does
    nothing, which is worse than either answer.
    """
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    row = resolve_confirmation(session, answer(UtteranceKind.CONCERN))

    assert row is not None
    assert row.resolved_kind == "concern"


def test_a_click_with_no_question_behind_it_writes_nothing(session: Session) -> None:
    """The meeting was deleted and the DM outlived it.

    Creating a row here would write meeting-scoped data back after the cascade
    that was meant to remove it — a deletion that undoes itself when somebody
    taps an old notification.
    """
    row = resolve_confirmation(session, answer(UtteranceKind.COMMITMENT, "utt_gone"))

    assert row is None
    assert session.query(ExtConfirmation).count() == 0


# --- the deadline, through a store ------------------------------------------


def test_a_stored_question_goes_undecided_on_its_own(session: Session) -> None:
    """No sweep, no beat schedule, no row rewritten.

    A timestamp that survives a round trip is the whole mechanism.
    """
    row = open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    assert row is not None
    session.commit()
    session.expire_all()

    stored = session.get(ExtConfirmation, UTTERANCE)
    assert stored is not None
    assert stored.outcome_at(row.sent_at.replace(tzinfo=UTC) + timedelta(hours=23)) == PENDING
    assert stored.outcome_at(row.sent_at.replace(tzinfo=UTC) + timedelta(hours=25)) == UNDECIDED


def test_a_timestamp_that_lost_its_zone_is_still_read_as_utc(session: Session) -> None:
    """SQLite has no timezone type, and this is the read path that must not fail.

    Every timestamp in ``autune_core`` is declared ``timezone=True`` and the
    worker runs on ``enable_utc``, so a naive value out of a store can only have
    been UTC going in. Raising here would break the one call that decides whether
    a question has gone undecided.
    """
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    session.commit()
    session.expire_all()

    stored = session.get(ExtConfirmation, UTTERANCE)
    assert stored is not None
    assert stored.sent_at.tzinfo is None, "SQLite is expected to drop the zone"
    assert stored.outcome_at(datetime.now(UTC)) == PENDING


# --- what E reads -----------------------------------------------------------


def test_the_contract_carries_the_reason_and_the_flag(session: Session) -> None:
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)

    agreements = ambiguous_agreements_for_meeting(session, MEETING)

    assert len(agreements) == 1
    assert agreements[0].utterance_id == UTTERANCE
    assert agreements[0].reason == WEAK_ASSENT
    assert agreements[0].confirmation_sent is True


def test_another_meetings_questions_do_not_leak_in(session: Session) -> None:
    open_confirmation(session, meeting_id=MEETING, utterance_id=UTTERANCE)
    open_confirmation(session, meeting_id="mtg_other", utterance_id="utt_other")

    assert [a.utterance_id for a in ambiguous_agreements_for_meeting(session, MEETING)] == [
        UTTERANCE
    ]


def test_a_meeting_with_no_ambiguity_reports_none(session: Session) -> None:
    assert ambiguous_agreements_for_meeting(session, MEETING) == []
