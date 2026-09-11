"""Step 3 against a real session: commitments become draft items (#11).

SQLite in memory and the fake classifier. The rules under test are what a card
is filled with, what a second run leaves behind, and that a person's
corrections survive a reprocessed meeting.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from autune_contracts import TranscriptReady
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptSource,
    Utterance,
)
from autune_core import Base, Meeting, Participant
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtDecision,
    ExtDecisionSource,
    ExtEditEvent,
)
from autune_extraction.pipeline import FakeClassifier
from autune_extraction.schemas import ActionItemCreate, ActionItemUpdate

MEETING = "mtg_1"
# 10:00 in Seoul on Wednesday 2026-09-09.
STARTED = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)

TABLES = [
    Meeting.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
]

LINES = [
    # id, start, speaker, speaker_id, text
    ("utt_1", 0.0, "김민경", "user_001", "제가 다음 주 화요일까지 정리하겠습니다"),
    ("utt_2", 4.0, "Speaker 2", None, "그건 제가 확인하겠습니다"),
    ("utt_3", 8.0, "김민경", "user_001", "네 좋아요"),
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의", started_at=STARTED))
        session.flush()
        yield session


def spoken(lines=LINES) -> list[Utterance]:
    return [
        Utterance(
            id=uid,
            speaker=speaker,
            speaker_id=speaker_id,
            start=start,
            end=start + 3.0,
            text=text,
            confidence=0.9,
        )
        for uid, start, speaker, speaker_id, text in lines
    ]


def draft(session: Session, lines=LINES) -> list[ExtActionItem] | None:
    utterances = spoken(lines)
    classified = service.classify_utterances(
        FakeClassifier(), utterances, consented={u.id for u in utterances}
    )
    return service.build_action_items(
        session, meeting_id=MEETING, utterances=utterances, classified=classified
    )


def model_items(session: Session) -> list[ExtActionItem]:
    return list(
        session.scalars(
            select(ExtActionItem)
            .where(ExtActionItem.origin == "model")
            .order_by(ExtActionItem.description)
        )
    )


# --- what a card holds ---------------------------------------------------------


def test_one_draft_per_commitment_and_none_for_anything_else(session: Session) -> None:
    items = draft(session)

    assert items is not None
    assert len(items) == 2, "utt_3 is none of the kinds"


def test_an_identified_speakers_commitment_is_filled_in(session: Session) -> None:
    draft(session)
    item = next(i for i in model_items(session) if i.assignee_id == "user_001")

    assert item.description == "제가 다음 주 화요일까지 정리하겠습니다"
    assert item.assignee_label is None
    assert item.due_date == date(2026, 9, 15)
    assert item.due_text == "다음 주 화요일"
    assert item.status == "needs_confirmation"
    assert item.origin == "model"
    assert [s.utterance_id for s in item.sources] == ["utt_1"]
    assert 0 < item.confidence <= 1


def test_an_unidentified_speaker_is_a_label_and_no_account(session: Session) -> None:
    draft(session)
    item = next(i for i in model_items(session) if i.assignee_id is None)

    assert item.assignee_label == "Speaker 2"
    assert item.due_date is None and item.due_text is None, "no date was said"


def test_a_meeting_with_no_start_time_keeps_the_phrase_and_no_date(session: Session) -> None:
    session.get(Meeting, MEETING).started_at = None  # type: ignore[union-attr]

    draft(session)
    item = next(i for i in model_items(session) if i.assignee_id == "user_001")

    assert item.due_date is None
    assert item.due_text == "다음 주 화요일"


# --- what a second run leaves ----------------------------------------------------


def test_running_twice_leaves_one_draft(session: Session) -> None:
    draft(session)
    draft(session)

    assert len(model_items(session)) == 2
    assert session.query(ExtActionItemSource).count() == 2


def test_a_hand_added_item_is_never_rebuilt_away(session: Session) -> None:
    """A person typing an item records an edit, so the rebuild is skipped
    entirely -- and even before that rule, only model items are replaced."""
    service.create_action_item(
        session, ActionItemCreate(meeting_id=MEETING, description="모델이 놓친 일")
    )

    draft(session)

    descriptions = {item.description for item in session.scalars(select(ExtActionItem))}
    assert "모델이 놓친 일" in descriptions


def test_once_a_person_has_edited_the_draft_a_rerun_changes_nothing(session: Session) -> None:
    """ADR 0006: the draft is theirs to finish. A reprocessed meeting must not
    reset an edited item or bring back a deleted one."""
    first = draft(session)
    assert first is not None
    service.delete_action_item(session, first[0])
    service.update_action_item(session, first[1], ActionItemUpdate(status="todo"))

    again = draft(session)

    assert again is None
    remaining = model_items(session)
    assert len(remaining) == 1
    assert remaining[0].status == "todo"


# --- a person setting the date ---------------------------------------------------


def test_a_person_setting_the_date_clears_the_phrase(session: Session) -> None:
    """The phrase explained the model's date, not theirs (#109)."""
    items = draft(session)
    assert items is not None
    item = next(i for i in items if i.due_text is not None)

    service.update_action_item(session, item, ActionItemUpdate(due_date=date(2026, 9, 20)))

    assert item.due_date == date(2026, 9, 20)
    assert item.due_text is None


def test_an_edit_that_leaves_the_date_alone_keeps_the_phrase(session: Session) -> None:
    items = draft(session)
    assert items is not None
    item = next(i for i in items if i.due_text is not None)

    service.update_action_item(session, item, ActionItemUpdate(status="todo"))

    assert item.due_text == "다음 주 화요일"


# --- the task --------------------------------------------------------------------


def stored(session: Session, *, consented: dict[str, bool]) -> None:
    """What module A wrote, which the task's consent filter reads: one
    participant per speaker in ``LINES``, consenting or not as given."""
    for speaker, agreed in consented.items():
        session.add(
            Participant(
                id=f"par_{speaker}", meeting_id=MEETING, speaker_label=speaker, consented=agreed
            )
        )
    for uid, start, speaker, _speaker_id, text in LINES:
        session.add(
            StoredUtterance(
                id=uid,
                meeting_id=MEETING,
                participant_id=f"par_{speaker}",
                speaker_label=speaker,
                start_sec=start,
                end_sec=start + 3.0,
                text=text,
            )
        )
    session.flush()


def run_task(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_classifier", FakeClassifier)
    payload = TranscriptReady(
        meeting_id=MEETING,
        utterances=spoken(),
        metadata=TranscriptMetadata(
            duration=12.0,
            source=next(iter(TranscriptSource)),
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    ).model_dump(mode="json")

    tasks.on_transcript_ready(payload)


def test_the_task_drafts_items_from_a_transcript(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored(session, consented={"김민경": True, "Speaker 2": True})

    run_task(session, monkeypatch)

    assert len(model_items(session)) == 2
