"""A source row keeps no offsets of a deleted utterance, on a real PostgreSQL (#400).

An item or a decision made from part of an utterance records where that part
is: two character offsets into the utterance's text. The row outlives the
utterance so a reader can be told a source existed, and until this the two
numbers outlived it too -- a trace of a line its speaker had deleted.

Only the database clears them (a trigger on each source table), so every test
deletes with plain SQL in the shape one of module A's paths uses, never
through the ORM, and never by calling this module:

- a rerun of the meeting   ``DELETE FROM utterances WHERE meeting_id = ...``
- a person's own speech    ``DELETE FROM utterances WHERE participant_id IN ...``
- an account deletion      the same, then ``DELETE FROM users``
- retention expiry         ``DELETE FROM meetings``

The rows are written by ``build_action_items`` and ``build_decisions``, the
writers a transcript goes through, from a classified line that names its part.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import UtteranceKind
from autune_core import Meeting, Participant, Team, User, Utterance
from autune_extraction import service
from autune_extraction.decisions import ClassifiedUtterance

REASON = "지난번에 고객사에서 일정이 너무 빠듯하다는 말이 있었으니까"
PROMISE = "수정본은 제가 금요일까지 보내겠습니다"
PROMISED = f"{REASON} {PROMISE}"
SETTLED = "출시는 다음 달로 미루기로 했습니다"
DECIDED = f"여러 의견이 있었지만 정리하면 {SETTLED}"

SOURCE_TABLES = ("ext_action_item_sources", "ext_decision_sources")


@pytest.fixture
def meeting(db_session: Session) -> dict[str, str]:
    """Two people, one line each: a promise by one, a decision said by the other.
    Each row is made from part of its line, so each holds two offsets."""
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="주간 회의")
    promiser = User(email="promiser-400@example.com", display_name="약속한 사람")
    decider = User(email="decider-400@example.com", display_name="정리한 사람")
    db_session.add_all([meeting, promiser, decider])
    db_session.flush()
    seats = [
        Participant(meeting_id=meeting.id, user_id=who.id, speaker_label=label, consented=True)
        for who, label in ((promiser, "S1"), (decider, "S2"))
    ]
    db_session.add_all(seats)
    db_session.flush()
    promised, decided = (
        Utterance(
            meeting_id=meeting.id,
            participant_id=seat.id,
            speaker_label=seat.speaker_label,
            start_sec=at,
            end_sec=at + 5,
            text=text,
        )
        for seat, at, text in ((seats[0], 0.0, PROMISED), (seats[1], 6.0, DECIDED))
    )
    db_session.add_all([promised, decided])
    db_session.flush()

    classified = [
        ClassifiedUtterance(
            id=promised.id,
            kind=UtteranceKind.COMMITMENT,
            confidence=0.9,
            text=PROMISED,
            speaker="S1",
            part=PROMISE,
        ),
        ClassifiedUtterance(
            id=decided.id,
            kind=UtteranceKind.DECISION,
            confidence=0.9,
            text=DECIDED,
            speaker="S2",
            part=SETTLED,
        ),
    ]
    service.build_action_items(
        db_session,
        meeting_id=meeting.id,
        utterances=[
            service.TranscriptUtterance(
                id=promised.id,
                speaker="S1",
                speaker_id=promiser.id,
                start=0.0,
                end=5.0,
                text=PROMISED,
                confidence=0.9,
            )
        ],
        classified=classified,
    )
    service.build_decisions(db_session, meeting_id=meeting.id, utterances=classified)
    db_session.flush()
    return {
        "meeting": meeting.id,
        "promiser": promiser.id,
        "decider": decider.id,
        "promiser_seat": seats[0].id,
        "decider_seat": seats[1].id,
        "promised": promised.id,
        "decided": decided.id,
    }


def kept(session: Session, table: str) -> list[tuple[str | None, int | None, int | None]]:
    """Every source row of the table: the utterance it points at and its offsets."""
    session.expire_all()
    rows = session.execute(
        sa.text(f"SELECT utterance_id, excerpt_start, excerpt_end FROM {table}")  # noqa: S608
    )
    return [tuple(row) for row in rows]


def test_the_writers_record_where_the_part_is(db_session: Session, meeting: dict[str, str]) -> None:
    """What the other tests start from: a row that has something to forget."""
    ((item_line, start, end),) = kept(db_session, "ext_action_item_sources")
    assert item_line == meeting["promised"] and PROMISED[start:end] == PROMISE

    ((decision_line, start, end),) = kept(db_session, "ext_decision_sources")
    assert decision_line == meeting["decided"] and DECIDED[start:end] == SETTLED


def test_a_rerun_of_the_meeting_leaves_no_offsets(
    db_session: Session, meeting: dict[str, str]
) -> None:
    db_session.execute(
        sa.text("DELETE FROM utterances WHERE meeting_id = :id"), {"id": meeting["meeting"]}
    )

    for table in SOURCE_TABLES:
        assert kept(db_session, table) == [(None, None, None)], table


def test_a_person_deleting_their_speech_clears_their_line_and_no_one_elses(
    db_session: Session, meeting: dict[str, str]
) -> None:
    db_session.execute(
        sa.text("DELETE FROM utterances WHERE participant_id IN (:seat)"),
        {"seat": meeting["decider_seat"]},
    )

    assert kept(db_session, "ext_decision_sources") == [(None, None, None)]
    ((line, start, end),) = kept(db_session, "ext_action_item_sources")
    assert line == meeting["promised"]
    assert PROMISED[start:end] == PROMISE, "another person's line still says where its part is"


def test_an_account_deletion_leaves_no_offsets_of_that_persons_line(
    db_session: Session, meeting: dict[str, str]
) -> None:
    db_session.execute(
        sa.text("DELETE FROM utterances WHERE participant_id IN (:seat)"),
        {"seat": meeting["promiser_seat"]},
    )
    db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": meeting["promiser"]})

    assert kept(db_session, "ext_action_item_sources") == [(None, None, None)]
    ((line, start, end),) = kept(db_session, "ext_decision_sources")
    assert line == meeting["decided"] and DECIDED[start:end] == SETTLED


def test_retention_expiry_takes_the_rows_themselves(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """The meeting goes, and with it the item, the decision and their source
    rows: the trigger runs inside that cascade and must not stand in its way."""
    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": meeting["meeting"]})

    for table in SOURCE_TABLES:
        assert kept(db_session, table) == [], table


def test_readers_still_say_a_source_was_deleted(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """Clearing the offsets takes nothing from the mark: it counts rows."""
    db_session.execute(
        sa.text("DELETE FROM utterances WHERE meeting_id = :id"), {"id": meeting["meeting"]}
    )
    db_session.expire_all()

    review = service.review_for_meeting(db_session, meeting["meeting"])
    (decision,) = review.decisions
    assert (decision.source_utterance_ids, decision.deleted_source_count) == ([], 1)
    (item,) = review.action_items
    assert (item.source_utterance_ids, item.deleted_source_count) == ([], 1)


def test_a_row_cannot_be_written_with_offsets_and_no_utterance(
    db_session: Session, meeting: dict[str, str]
) -> None:
    """The rule is the row's, not the delete's: whatever writes a source row
    without an utterance -- an import, a repair by hand -- gets no offsets in."""
    for table in SOURCE_TABLES:
        db_session.execute(
            sa.text(
                f"UPDATE {table} SET utterance_id = NULL, excerpt_start = 3, excerpt_end = 9"  # noqa: S608
            )
        )
        assert kept(db_session, table) == [(None, None, None)], table


def test_both_tables_carry_the_trigger(db_session: Session) -> None:
    """A table rebuilt without its trigger would keep the offsets silently:
    nothing fails when a trigger is missing."""
    rows = db_session.execute(
        sa.text(
            "SELECT c.relname, t.tgname, p.proname FROM pg_trigger t"
            " JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid"
            " WHERE NOT t.tgisinternal AND c.relname IN :tables ORDER BY c.relname"
        ).bindparams(sa.bindparam("tables", expanding=True)),
        {"tables": list(SOURCE_TABLES)},
    )
    assert [tuple(row) for row in rows] == [
        (
            "ext_action_item_sources",
            "ext_action_item_sources_forget_excerpt",
            "ext_forget_excerpt_of_deleted_source",
        ),
        (
            "ext_decision_sources",
            "ext_decision_sources_forget_excerpt",
            "ext_forget_excerpt_of_deleted_source",
        ),
    ]
