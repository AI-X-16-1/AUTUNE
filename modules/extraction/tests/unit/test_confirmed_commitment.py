"""A speaker's "yes, that was a commitment" makes a draft item, against a session.

SQLite in memory. The rules under test are what the draft is filled with, that
every retry and rerun lands on one item, that the later answer wins, and that a
person's work is never taken back by a click on a DM.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus, UtteranceKind
from autune_core import Base, Meeting, Participant, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service
from autune_extraction.confirmations import ConfirmationResponse
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
)

MEETING = "mtg_1"
UTTERANCE = "utt_1"
# 10:00 in Seoul on Wednesday 2026-09-09.
STARTED = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
TEXT = "그럼 제가 다음 주 화요일까지 볼게요"

TABLES = [
    User.__table__,
    Meeting.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
    ExtConfirmation.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(User(id="user_001", email="a@example.com", display_name="김민경"))
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의", started_at=STARTED))
        session.flush()
        yield session


def seed(
    session: Session,
    *,
    text: str = TEXT,
    consented: bool = True,
    user_id: str | None = "user_001",
    asked: bool = True,
) -> ExtConfirmation:
    """One ambiguous agreement, by default already asked about."""
    session.add(
        Participant(
            id="par_1",
            meeting_id=MEETING,
            user_id=user_id,
            speaker_label="김민경",
            consented=consented,
        )
    )
    session.add(
        StoredUtterance(
            id=UTTERANCE,
            meeting_id=MEETING,
            participant_id="par_1",
            speaker_label="김민경",
            start_sec=0.0,
            end_sec=3.0,
            text=text,
        )
    )
    row = ExtConfirmation(
        utterance_id=UTTERANCE,
        meeting_id=MEETING,
        reason="weak_assent",
        sent_at=datetime.now(UTC) if asked else None,
    )
    session.add(row)
    session.flush()
    return row


def answer(kind: UtteranceKind) -> ConfirmationResponse:
    return ConfirmationResponse(
        utterance_id=UTTERANCE, resolved_kind=kind, responder_id="U_SPEAKER"
    )


def items(session: Session) -> list[ExtActionItem]:
    return list(session.scalars(select(ExtActionItem)))


# --- the draft ----------------------------------------------------------------


def test_a_commitment_answer_makes_a_draft_from_the_stored_utterance(session: Session) -> None:
    seed(session)

    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    (item,) = items(session)
    assert item.description == TEXT
    assert item.description_resolved is False
    assert item.assignee_id == "user_001"
    assert item.due_date == date(2026, 9, 15)
    assert item.due_text is not None
    assert item.status == ActionStatus.NEEDS_CONFIRMATION.value
    assert item.origin == "model"
    assert item.confidence == 1.0
    assert [s.utterance_id for s in item.sources] == [UTTERANCE]


def test_an_unidentified_speaker_keeps_only_the_label(session: Session) -> None:
    seed(session, user_id=None)

    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    (item,) = items(session)
    assert item.assignee_id is None
    assert item.assignee_label == "김민경"


@pytest.mark.parametrize("kind", [UtteranceKind.CONCERN, UtteranceKind.DECISION])
def test_any_other_answer_makes_no_item(session: Session, kind: UtteranceKind) -> None:
    seed(session)

    service.resolve_confirmation(session, answer(kind))

    assert items(session) == []


def test_a_slack_retry_lands_on_the_same_item(session: Session) -> None:
    seed(session)

    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))
    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    assert len(items(session)) == 1


def test_an_excluded_speaker_makes_no_item(session: Session) -> None:
    """Excluded speech is not stored, so there is nothing to quote (privacy.md 5)."""
    seed(session, consented=False)

    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    assert items(session) == []


def test_a_blank_utterance_makes_no_item(session: Session) -> None:
    seed(session, text="  ")

    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    assert items(session) == []


def test_an_answer_to_a_question_never_asked_makes_no_item(session: Session) -> None:
    seed(session, asked=False)

    assert service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT)) is None
    assert items(session) == []


# --- the later answer wins ------------------------------------------------------


def test_changing_the_answer_takes_an_untouched_draft_back(session: Session) -> None:
    seed(session)
    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    service.resolve_confirmation(session, answer(UtteranceKind.CONCERN))

    assert items(session) == []
    # It is not a person's correction, so the edit-cost counter does not move.
    assert session.scalar(select(ExtEditEvent.id)) is None


def test_changing_the_answer_leaves_a_draft_a_person_has_moved(session: Session) -> None:
    seed(session)
    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))
    (item,) = items(session)
    item.status = ActionStatus.TODO.value
    session.flush()

    service.resolve_confirmation(session, answer(UtteranceKind.CONCERN))

    assert len(items(session)) == 1


def test_changing_the_answer_leaves_a_draft_a_person_has_edited(session: Session) -> None:
    seed(session)
    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))
    (item,) = items(session)
    session.add(
        ExtEditEvent(meeting_id=MEETING, action_item_id=item.id, kind="edited", fields="due_date")
    )
    session.flush()

    service.resolve_confirmation(session, answer(UtteranceKind.CONCERN))

    assert len(items(session)) == 1


# --- a rerun of the meeting -----------------------------------------------------


def rerun(session: Session, *, kind: UtteranceKind) -> list[ExtActionItem] | None:
    """The meeting extracted again, this utterance classified as ``kind``."""
    said = service.TranscriptUtterance(
        id=UTTERANCE,
        speaker="김민경",
        speaker_id="user_001",
        start=0.0,
        end=3.0,
        text=TEXT,
        confidence=0.9,
    )
    classified = [
        ClassifiedUtterance(id=UTTERANCE, kind=kind, confidence=0.6, text=TEXT, speaker="김민경")
    ]
    return service.build_action_items(
        session, meeting_id=MEETING, utterances=[said], classified=classified
    )


def test_a_rerun_keeps_the_draft_a_confirmation_made(session: Session) -> None:
    """Model drafts are replaced on a rerun; the speaker's answer is not."""
    seed(session)
    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    rebuilt = rerun(session, kind=UtteranceKind.AMBIGUOUS)

    assert rebuilt is not None and len(rebuilt) == 1
    (item,) = items(session)
    assert [s.utterance_id for s in item.sources] == [UTTERANCE]
    assert item.due_date == date(2026, 9, 15)


def test_a_rerun_that_now_calls_it_a_commitment_still_makes_one_item(session: Session) -> None:
    seed(session)
    service.resolve_confirmation(session, answer(UtteranceKind.COMMITMENT))

    rerun(session, kind=UtteranceKind.COMMITMENT)

    assert len(items(session)) == 1


def test_a_rerun_makes_nothing_for_an_agreement_answered_otherwise(session: Session) -> None:
    seed(session)
    service.resolve_confirmation(session, answer(UtteranceKind.CONCERN))

    rerun(session, kind=UtteranceKind.AMBIGUOUS)

    assert items(session) == []
