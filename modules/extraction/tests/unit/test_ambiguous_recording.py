"""Ambiguous agreement recorded before its DM can be sent (#12).

SQLite in memory. The pipeline writes a row for every utterance it calls
ambiguous; the DM that would fill ``sent_at`` is blocked on #70 and #30. What is
under test is the state in between: what it reports, what a rerun does to it,
and what happens when a question is finally asked.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Base
from autune_extraction import service
from autune_extraction.confirmations import ConfirmationResponse
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.models import NOT_ASKED, PENDING, ExtConfirmation

K = UtteranceKind
MEETING = "mtg_1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[ExtConfirmation.__table__])
    with Session(engine) as session:
        yield session


def classified(*kinds: K | None) -> list[ClassifiedUtterance]:
    return [
        ClassifiedUtterance(id=f"utt_{i}", kind=kind, confidence=0.8, text="...")
        for i, kind in enumerate(kinds)
    ]


def record(session: Session, *kinds: K | None) -> int:
    return service.record_ambiguous_agreements(
        session, meeting_id=MEETING, classified=classified(*kinds)
    )


def rows(session: Session) -> dict[str, ExtConfirmation]:
    return {row.utterance_id: row for row in session.query(ExtConfirmation)}


# --- recorded, not asked --------------------------------------------------------


def test_every_ambiguous_utterance_is_recorded_and_nothing_else(session: Session) -> None:
    count = record(session, K.AMBIGUOUS, K.COMMITMENT, None, K.AMBIGUOUS)

    assert count == 2
    assert set(rows(session)) == {"utt_0", "utt_3"}


def test_a_recorded_agreement_is_not_asked_and_says_so(session: Session) -> None:
    """``confirmation_sent`` false is the state the contract kept the flag for."""
    record(session, K.AMBIGUOUS)

    (agreement,) = service.ambiguous_agreements_for_meeting(session, MEETING)

    assert agreement.utterance_id == "utt_0"
    assert agreement.confirmation_sent is False
    assert rows(session)["utt_0"].outcome_at() == NOT_ASKED


def test_a_question_never_asked_never_times_out(session: Session) -> None:
    """``undecided`` would report the speaker as silent on something nobody put
    to them."""
    record(session, K.AMBIGUOUS)

    far = datetime.now(UTC) + timedelta(days=365)

    assert rows(session)["utt_0"].outcome_at(far) == NOT_ASKED


def test_the_unasked_ones_are_what_a_sender_would_walk(session: Session) -> None:
    record(session, K.AMBIGUOUS, K.AMBIGUOUS)
    service.open_confirmation(session, meeting_id=MEETING, utterance_id="utt_0")

    assert [row.utterance_id for row in service.unasked_confirmations(session, MEETING)] == [
        "utt_1"
    ]


# --- a rerun ---------------------------------------------------------------------


def test_recording_twice_leaves_one_row_each(session: Session) -> None:
    record(session, K.AMBIGUOUS, K.AMBIGUOUS)
    record(session, K.AMBIGUOUS, K.AMBIGUOUS)

    assert len(rows(session)) == 2


def test_a_rerun_removes_an_unasked_row_the_model_no_longer_calls_ambiguous(
    session: Session,
) -> None:
    """Never asked, so it is derived data -- replaced like the classifications."""
    record(session, K.AMBIGUOUS, K.AMBIGUOUS)
    record(session, K.AMBIGUOUS, K.COMMITMENT)

    assert set(rows(session)) == {"utt_0"}


def test_a_rerun_keeps_a_row_the_speaker_was_asked_about(session: Session) -> None:
    """The question is in front of them, and they may have answered it."""
    record(session, K.AMBIGUOUS)
    service.open_confirmation(session, meeting_id=MEETING, utterance_id="utt_0")

    record(session, K.COMMITMENT)

    assert set(rows(session)) == {"utt_0"}


# --- when the question is finally asked ------------------------------------------


def test_asking_later_starts_the_clock_then(session: Session) -> None:
    """The deadline counts from the DM, not from when the ambiguity was found,
    or a question sent days later would arrive already expired."""
    record(session, K.AMBIGUOUS)
    before = datetime.now(UTC)

    row = service.open_confirmation(session, meeting_id=MEETING, utterance_id="utt_0")

    assert row.sent_at is not None
    assert row.sent_at.replace(tzinfo=UTC) >= before - timedelta(seconds=1)
    assert row.outcome_at() == PENDING
    assert row.confirmation_sent is True


def test_an_answer_to_a_question_never_asked_is_ignored(session: Session) -> None:
    """A click needs a DM. Recording one here would be an answer nobody asked for,
    and the table refuses it anyway (``answer_needs_a_question``)."""
    record(session, K.AMBIGUOUS)

    result = service.resolve_confirmation(
        session,
        ConfirmationResponse(
            utterance_id="utt_0", resolved_kind=K.COMMITMENT, responder_id="U_SPEAKER"
        ),
    )

    assert result is None
    assert rows(session)["utt_0"].resolved_kind is None


def test_the_table_refuses_an_answer_without_a_question() -> None:
    names = {constraint.name for constraint in ExtConfirmation.__table__.constraints}

    assert "ck_ext_confirmations_answer_needs_a_question" in names
