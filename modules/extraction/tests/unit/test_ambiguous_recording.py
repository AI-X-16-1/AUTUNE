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
from sqlalchemy import Result, create_engine, event, insert, update
from sqlalchemy.orm import ORMExecuteState, Session

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


def test_a_rerun_keeps_a_row_asked_about_after_the_session_last_read_it(
    session: Session,
) -> None:
    """The delete goes by the database's ``sent_at``, not the session's copy.

    Raised on #153: a sender can fill ``sent_at`` in another transaction while
    this session still holds the row as unasked. Deleting on that copy would
    remove a question the speaker has, and their answer would land on nothing.
    """
    record(session, K.AMBIGUOUS)
    # Held, because the identity map is weak: a copy nobody references is
    # dropped, and the next read builds a fresh one from the database -- which
    # would hide exactly the staleness under test.
    held = rows(session)["utt_0"]
    assert held.sent_at is None  # the session's copy: unasked
    # Straight on the connection, so the session never hears of it -- the way a
    # sender's own transaction would change the row.
    table = ExtConfirmation.__table__
    session.connection().execute(
        update(table).where(table.c.utterance_id == "utt_0").values(sent_at=datetime.now(UTC))
    )

    record(session, K.COMMITMENT)

    assert held.sent_at is None  # still stale: the delete must not have trusted it
    session.expire_all()
    assert set(rows(session)) == {"utt_0"}


def test_a_row_another_run_wrote_in_the_meantime_is_left_alone(session: Session) -> None:
    """A redelivered task records the same meeting at the same time.

    Raised on #153: the other run's row lands after this run's first statement
    and before its insert. The insert has to skip it, not fail on the primary
    key and take the whole meeting's transaction down with it.
    """
    other_run_wrote = False

    @event.listens_for(session, "do_orm_execute")
    def the_other_run(state: ORMExecuteState) -> Result[object] | None:
        nonlocal other_run_wrote
        if other_run_wrote:
            return None
        other_run_wrote = True
        result = state.invoke_statement()
        session.connection().execute(
            insert(ExtConfirmation.__table__).values(
                utterance_id="utt_0", meeting_id=MEETING, reason="weak_assent", sent_at=None
            )
        )
        return result

    assert record(session, K.AMBIGUOUS) == 1

    assert other_run_wrote
    assert set(rows(session)) == {"utt_0"}


# --- when the question is finally asked ------------------------------------------


def test_asking_later_starts_the_clock_then(session: Session) -> None:
    """The deadline counts from the DM, not from when the ambiguity was found,
    or a question sent days later would arrive already expired."""
    record(session, K.AMBIGUOUS)
    before = datetime.now(UTC)

    row = service.open_confirmation(session, meeting_id=MEETING, utterance_id="utt_0")

    assert row is not None
    assert row.sent_at is not None
    assert row.sent_at.replace(tzinfo=UTC) >= before - timedelta(seconds=1)
    assert row.outcome_at() == PENDING
    assert row.confirmation_sent is True


def test_only_one_sender_claims_a_recorded_question(session: Session) -> None:
    """The row the pipeline wrote is the one two senders race on (#198).

    Both see ``sent_at`` empty. Before the conditional update each wrote its own
    timestamp and each sent a DM: one question, two messages, and the deadline
    measured from the later one.
    """
    record(session, K.AMBIGUOUS)

    first = service.open_confirmation(session, meeting_id=MEETING, utterance_id="utt_0")
    second = service.open_confirmation(session, meeting_id=MEETING, utterance_id="utt_0")

    assert first is not None
    assert second is None
    assert rows(session)["utt_0"].sent_at == first.sent_at


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
