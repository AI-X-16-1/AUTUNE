"""What happens to an ambiguous agreement between the DM and the deadline.

No database. The outcome rule is arithmetic on two timestamps and the privacy
properties are facts about the table definition, so both are readable without
Postgres — the same split as ``test_action_item_editing``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autune_extraction.confirmations import ACTION_IDS, CONFIRMATION_TIMEOUT, WEAK_ASSENT
from autune_extraction.models import (
    CONFIRMATION_KINDS,
    PENDING,
    RESOLVED,
    UNDECIDED,
    ExtConfirmation,
)

SENT = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def confirmation(**overrides: object) -> ExtConfirmation:
    fields: dict = {
        "utterance_id": "utt_1",
        "meeting_id": "mtg_1",
        "reason": WEAK_ASSENT,
        "sent_at": SENT,
    }
    fields.update(overrides)
    return ExtConfirmation(**fields)


# --- the 24-hour rule -------------------------------------------------------


def test_an_unanswered_question_is_pending_before_the_deadline() -> None:
    row = confirmation()

    assert row.outcome_at(SENT + timedelta(hours=23, minutes=59)) == PENDING


def test_silence_resolves_itself_at_the_deadline() -> None:
    """Nobody has to run anything for this to become true.

    The outcome is read off ``sent_at``, so a worker that is down, a beat
    schedule nobody configured, or a queue that backed up cannot leave a
    confirmation pending past its deadline.
    """
    row = confirmation()

    assert row.outcome_at(SENT + CONFIRMATION_TIMEOUT) == UNDECIDED
    assert row.outcome_at(SENT + timedelta(days=7)) == UNDECIDED


def test_the_deadline_is_twenty_four_hours() -> None:
    """#12 and ui-spec S19. Pinned so changing it is a deliberate edit."""
    assert timedelta(hours=24) == CONFIRMATION_TIMEOUT


def test_an_answer_outlives_the_deadline() -> None:
    """A resolved question does not lapse back into undecided.

    Reading it any other way would mean a meeting's summary changed on its own
    overnight, with nothing in the data to explain why.
    """
    row = confirmation(resolved_kind="commitment", responded_at=SENT + timedelta(minutes=5))

    assert row.outcome_at(SENT + timedelta(days=30)) == RESOLVED


def test_a_late_answer_still_counts() -> None:
    """The speaker telling us what they meant beats the clock.

    Preferring a stale ``undecided`` over an answer that arrived would be
    choosing the timeout over the person it was asking.
    """
    row = confirmation(resolved_kind="decision", responded_at=SENT + timedelta(days=2))

    assert row.outcome_at(SENT + timedelta(days=2)) == RESOLVED


# --- what the row is allowed to hold ----------------------------------------


def test_a_confirmation_records_no_responder() -> None:
    """ADR 0003: no per-person record of one person's conduct in a meeting.

    The DM goes to one person about their own speech and comes back from that
    person, so a responder column would be exactly that. Asserting the whole
    column set means a later addition fails here, where the reason is written
    down, rather than passing review as an obvious convenience.
    """
    columns = {column.name for column in ExtConfirmation.__table__.columns}

    assert columns == {
        "utterance_id",
        "meeting_id",
        "reason",
        "sent_at",
        "resolved_kind",
        "responded_at",
        "created_at",
        "updated_at",
    }


def test_no_outcome_is_stored() -> None:
    """A status column and a clock can disagree, and the column would be wrong.

    Nothing runs at the deadline, so a stored ``pending`` would stay pending
    forever. The outcome exists only as a function of the timestamps.
    """
    columns = {column.name for column in ExtConfirmation.__table__.columns}

    assert "status" not in columns
    assert "outcome" not in columns
    assert callable(ExtConfirmation.outcome_at)


def test_the_quoted_utterance_is_not_copied_here() -> None:
    """The question is read by joining ``utterances``.

    Keeping a copy would leave meeting content behind a cascade that no longer
    reaches it, and this row survives exactly as long as the utterance does.
    """
    columns = {column.name for column in ExtConfirmation.__table__.columns}

    assert not {"text", "quoted_text", "utterance_text"} & columns


def test_it_is_deleted_with_its_meeting_and_with_its_utterance() -> None:
    utterance_fk = next(iter(ExtConfirmation.__table__.c.utterance_id.foreign_keys))
    meeting_fk = next(iter(ExtConfirmation.__table__.c.meeting_id.foreign_keys))

    assert utterance_fk.ondelete == "CASCADE"
    assert meeting_fk.ondelete == "CASCADE"


def test_one_utterance_gets_one_question() -> None:
    """The primary key is the idempotency key.

    Slack retries a click it has not heard back from within three seconds. With
    an id of its own this table would collect one row per retry.
    """
    assert [column.name for column in ExtConfirmation.__table__.primary_key] == ["utterance_id"]


# --- the buttons and the column agree ---------------------------------------


def test_every_button_resolves_to_a_kind_the_column_accepts() -> None:
    """The check constraint and ``ACTION_IDS`` are two statements of one rule.

    A button added on one side and not the other fails at insert time, in
    production, on a click.
    """
    assert {kind.value for kind in ACTION_IDS.values()} == set(CONFIRMATION_KINDS)


def test_denial_is_recorded_as_a_concern_not_as_a_deletion() -> None:
    """A speaker declining to commit is still something the meeting said."""
    assert "concern" in CONFIRMATION_KINDS


@pytest.mark.parametrize("kind", CONFIRMATION_KINDS)
def test_a_resolved_row_reads_as_resolved_for_every_kind(kind: str) -> None:
    row = confirmation(resolved_kind=kind, responded_at=SENT)

    assert row.outcome_at(SENT) == RESOLVED
