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
from autune_core import Base, Meeting, Participant, User
from autune_core import Utterance as StoredUtterance
from autune_core.errors import NotFoundError, ValidationError
from autune_extraction import service, tasks
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
from autune_extraction.pipeline import FakeClassifier, FakeNli
from autune_extraction.schemas import ActionItemCreate, ActionItemUpdate

MEETING = "mtg_1"
# 10:00 in Seoul on Wednesday 2026-09-09.
STARTED = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)

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
        session.add(User(id="user_001", email="a@example.com", display_name="김민경"))
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
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
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


def test_a_non_consenting_speakers_commitment_never_becomes_a_draft(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """privacy.md section 5, raised on #151 and #152.

    ``Speaker 2`` did not consent. Their "그건 제가 확인하겠습니다" is a
    commitment to the classifier, but it never reaches the classifier, so no
    card is drafted from it -- no description quoting it, no assignee label, no
    due phrase.
    """
    stored(session, consented={"김민경": True, "Speaker 2": False})

    run_task(session, monkeypatch)

    (item,) = model_items(session)
    assert [source.utterance_id for source in item.sources] == ["utt_1"]
    assert all("확인하겠습니다" not in i.description for i in model_items(session))


def test_a_speaker_whose_account_is_gone_is_a_label(session: Session) -> None:
    """An id that is not in ``users`` would fail the foreign key and take every
    item of the meeting with it (PARKJAEKYUNG0525, #152)."""
    lines = [("utt_x", 0.0, "김민경", "user_gone", "제가 정리하겠습니다")]

    draft(session, lines)
    (item,) = model_items(session)

    assert item.assignee_id is None
    assert item.assignee_label == "김민경"


def test_deleting_alone_is_enough_to_keep_the_draft(session: Session) -> None:
    """A person who only deleted a wrong item has started finishing the list too;
    a rerun must not bring the item back (PARKJAEKYUNG0525, #152)."""
    first = draft(session)
    assert first is not None
    service.delete_action_item(session, first[0])

    again = draft(session)

    assert again is None
    assert len(model_items(session)) == 1


# --- what a hand-added item may point at ----------------------------------------


def _stored(session: Session, uid: str, meeting_id: str) -> None:
    session.add(
        StoredUtterance(
            id=uid,
            meeting_id=meeting_id,
            speaker_label="Speaker 1",
            start_sec=0.0,
            end_sec=1.0,
            text="회의 내용",
        )
    )
    session.flush()


def test_an_item_for_an_unknown_meeting_is_not_found(session: Session) -> None:
    """A 404, not the foreign key's 500 -- found by a local end-to-end run."""
    with pytest.raises(NotFoundError):
        service.create_action_item(
            session, ActionItemCreate(meeting_id="mtg_nope", description="일")
        )
    assert session.scalars(select(ExtActionItem)).all() == []


def test_a_source_that_does_not_exist_is_refused_by_name(session: Session) -> None:
    with pytest.raises(ValidationError) as caught:
        service.create_action_item(
            session,
            ActionItemCreate(meeting_id=MEETING, description="일", source_utterance_ids=["utt_x"]),
        )
    assert caught.value.details == {"field": "source_utterance_ids"}
    assert session.scalars(select(ExtActionItem)).all() == []


def test_a_source_from_another_meeting_is_refused(session: Session) -> None:
    """The utterance exists, but the detail endpoint would quote another
    meeting's words on this meeting's board."""
    session.add(Meeting(id="mtg_2", team_id="team_1", title="다른 회의", started_at=STARTED))
    _stored(session, "utt_other", "mtg_2")

    with pytest.raises(ValidationError):
        service.create_action_item(
            session,
            ActionItemCreate(
                meeting_id=MEETING, description="일", source_utterance_ids=["utt_other"]
            ),
        )


def test_a_source_of_this_meeting_is_kept(session: Session) -> None:
    _stored(session, "utt_here", MEETING)

    item = service.create_action_item(
        session,
        ActionItemCreate(
            meeting_id=MEETING,
            description="일",
            source_utterance_ids=["utt_here", "utt_here"],
        ),
    )

    assert [source.utterance_id for source in item.sources] == ["utt_here"]


def test_an_unknown_assignee_is_refused_by_name(session: Session) -> None:
    """``user_ghost`` passes the schema's ``^user_`` pattern -- the same gap
    ``slots.assignee_of`` already closed for the model's own path -- so only
    this existence check stands between it and the foreign key's 500."""
    with pytest.raises(ValidationError) as caught:
        service.create_action_item(
            session,
            ActionItemCreate(meeting_id=MEETING, description="일", assignee_id="user_ghost"),
        )
    assert caught.value.details == {"field": "assignee_id"}
    assert session.scalars(select(ExtActionItem)).all() == []


def test_a_known_assignee_is_kept(session: Session) -> None:
    item = service.create_action_item(
        session,
        ActionItemCreate(meeting_id=MEETING, description="일", assignee_id="user_001"),
    )

    assert item.assignee_id == "user_001"


def test_updating_to_an_unknown_assignee_is_refused_and_changes_nothing(session: Session) -> None:
    item = service.create_action_item(
        session, ActionItemCreate(meeting_id=MEETING, description="일")
    )

    with pytest.raises(ValidationError) as caught:
        service.update_action_item(session, item, ActionItemUpdate(assignee_id="user_ghost"))
    assert caught.value.details == {"field": "assignee_id"}
    assert item.assignee_id is None


def test_updating_can_still_clear_an_assignee(session: Session) -> None:
    item = service.create_action_item(
        session,
        ActionItemCreate(meeting_id=MEETING, description="일", assignee_id="user_001"),
    )

    service.update_action_item(session, item, ActionItemUpdate(assignee_id=None))

    assert item.assignee_id is None
