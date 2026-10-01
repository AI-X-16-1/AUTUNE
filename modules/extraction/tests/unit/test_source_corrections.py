"""A corrected transcript reaches what B drew from it (#586).

A PII report masks stored lines again and republishes ``TranscriptReady``
without saying which lines changed. B keeps a digest of each item's and
decision's source text and, on every run -- even in a meeting a person edited --
fixes or flags what changed. Under test: digests at creation; a first digest is
a baseline; the line itself is rewritten, a summary is rewritten and flagged, a
person's text is only flagged; ``due_text`` follows the new line; confirmed
changes are returned for re-sync; a person's edit or review clears the flag; and
the whole thing through the task, on a meeting with an edit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts import TranscriptReady
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptSource,
    Utterance,
)
from autune_core import Base, Meeting, Participant, TeamMember, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtDecision,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
)
from autune_extraction.noun_form import tidy
from autune_extraction.pipeline import FakeClassifier, FakeNli
from autune_extraction.schemas import ActionItemCreate, ActionItemUpdate, DecisionReviewUpdate
from autune_extraction.slots import meeting_day, parse_due

MEETING = "mtg_1"
OLD = "김민경 010-1234-5678로 다음 주 화요일까지 정리하겠습니다"
NEW = "김민경 [전화번호]로 다음 주 화요일까지 정리하겠습니다"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Participant, StoredUtterance, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(User(id="user_001", email="a@example.com", display_name="김민경"))
        s.add(
            Meeting(
                id=MEETING,
                team_id="team_1",
                title="주간 회의",
                started_at=datetime(2026, 9, 9, 1, tzinfo=UTC),
            )
        )
        s.add(
            Participant(
                id="par_kim",
                meeting_id=MEETING,
                user_id="user_001",
                speaker_label="김민경",
                consented=True,
            )
        )
        s.flush()
        s.add(
            StoredUtterance(
                id="utt_1",
                meeting_id=MEETING,
                participant_id="par_kim",
                speaker_label="김민경",
                start_sec=0.0,
                end_sec=3.0,
                text=OLD,
            )
        )
        s.commit()
        yield s


def item(
    session: Session,
    item_id: str,
    *,
    origin: str = "model",
    status: str = "todo",
    resolved: bool = False,
    description: str | None = None,
) -> ExtActionItem:
    row = ExtActionItem(
        id=item_id,
        meeting_id=MEETING,
        description=description or tidy(OLD),
        description_resolved=resolved,
        due_text="다음 주 화요일까지",
        status=status,
        confidence=0.9,
        origin=origin,
        sources=[ExtActionItemSource(utterance_id="utt_1")],
        source_digest=service.source_digest([OLD]),
    )
    session.add(row)
    session.flush()
    return row


def correct(session: Session) -> service.SourceCorrections:
    done = service.apply_source_corrections(session, meeting_id=MEETING, spoken={"utt_1": NEW})
    session.flush()
    return done


def test_an_item_added_by_a_person_records_its_digest(session: Session) -> None:
    row = service.create_action_item(
        session,
        ActionItemCreate(meeting_id=MEETING, description="정리", source_utterance_ids=["utt_1"]),
    )

    assert row.source_digest == service.source_digest([OLD])


def test_a_first_digest_is_a_baseline_not_a_correction(session: Session) -> None:
    row = item(session, "act_1")
    row.source_digest = None

    done = correct(session)

    assert row.source_digest == service.source_digest([NEW])
    assert (row.description, row.needs_recheck, done.changed_items) == (tidy(OLD), False, ())


def test_the_line_itself_is_rewritten_and_its_date_phrase_read_again(session: Session) -> None:
    row = item(session, "act_1")

    done = correct(session)

    assert row.description == tidy(NEW)
    assert "010-1234-5678" not in row.description
    due = parse_due(NEW, meeting_day(datetime(2026, 9, 9, 1, tzinfo=UTC)))
    assert due is not None and row.due_text == due.text
    assert row.needs_recheck is False, "B fixed it; nothing for a person to do"
    assert done.changed_items == ("act_1",)


def test_a_summary_is_rewritten_from_the_line_and_flagged(session: Session) -> None:
    row = item(session, "act_1", resolved=True, description="연락처로 정리 예정")

    correct(session)

    assert (row.description, row.description_resolved, row.needs_recheck) == (
        tidy(NEW),
        False,
        True,
    )


@pytest.mark.parametrize("who", ["typed", "edited"])
def test_a_persons_text_is_only_flagged(session: Session, who: str) -> None:
    row = item(
        session, "act_1", origin="user" if who == "typed" else "model", description="사람이 쓴 문장"
    )
    if who == "edited":
        session.add(
            ExtEditEvent(
                meeting_id=MEETING, action_item_id="act_1", kind="edited", fields="description"
            )
        )
        session.flush()

    correct(session)

    assert (row.description, row.needs_recheck) == ("사람이 쓴 문장", True)


def test_nothing_changes_when_the_line_did_not(session: Session) -> None:
    row = item(session, "act_1")

    done = service.apply_source_corrections(session, meeting_id=MEETING, spoken={"utt_1": OLD})

    assert (row.description, done.changed_items, done.flagged) == (tidy(OLD), (), 0)


def test_an_unconfirmed_draft_is_fixed_but_needs_no_resync(session: Session) -> None:
    row = item(session, "act_1", status="needs_confirmation")

    done = correct(session)

    assert row.description == tidy(NEW) and done.changed_items == ()


def test_decisions_a_person_wrote_are_flagged_and_confirmed_ones_resync(
    session: Session,
) -> None:
    for dec_id, origin, reworded in (
        ("dec_model", "model", None),
        ("dec_typed", "user", None),
        ("dec_reworded", "model", "사람의 문장"),
    ):
        session.add(
            ExtDecision(
                id=dec_id,
                meeting_id=MEETING,
                statement="결정",
                confidence=0.9,
                origin=origin,
                source_digest=service.source_digest([OLD]),
                sources=[ExtDecisionSource(utterance_id="utt_1", position=0)],
            )
        )
        session.flush()
        session.add(
            ExtDecisionReview(
                decision_id=dec_id, meeting_id=MEETING, status="confirmed", statement=reworded
            )
        )
    session.flush()

    done = correct(session)

    flags = {d.id: d.needs_recheck for d in session.scalars(select(ExtDecision))}
    assert flags == {"dec_model": False, "dec_typed": True, "dec_reworded": True}
    assert set(done.changed_decisions) == {"dec_model", "dec_typed", "dec_reworded"}


def test_a_persons_edit_and_review_clear_the_flag(session: Session) -> None:
    row = item(session, "act_1", origin="user")
    correct(session)
    assert service.read_model(row).needs_recheck is True

    service.update_action_item(session, row, ActionItemUpdate(description="고쳐 쓴 문장"))

    assert row.needs_recheck is False

    session.add(
        ExtDecision(
            id="dec_1",
            meeting_id=MEETING,
            statement="결정",
            confidence=0.9,
            origin="user",
            needs_recheck=True,
            sources=[ExtDecisionSource(utterance_id="utt_1", position=0)],
        )
    )
    session.flush()
    session.add(ExtDecisionReview(decision_id="dec_1", meeting_id=MEETING, status="confirmed"))
    session.flush()
    decision = session.get(ExtDecision, "dec_1")
    assert decision is not None
    reviewed = service.review_decision(session, decision, DecisionReviewUpdate(status="confirmed"))
    assert reviewed.needs_recheck is False


def test_through_the_task_on_a_meeting_a_person_edited(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case #586 found: an edit keeps every item, so before this a
    corrected line stayed in a confirmed item and in its Notion page."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_classifier", FakeClassifier)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    for task in (
        "sync_action_item",
        "sync_action_item_jira",
        "sync_action_item_calendar",
        "sync_decision",
    ):
        monkeypatch.setattr(
            tasks,
            task,
            SimpleNamespace(delay=lambda ident, task=task: queued.append((task, ident))),
        )

    def publish(text: str) -> None:
        tasks.on_transcript_ready(
            TranscriptReady(
                meeting_id=MEETING,
                utterances=[
                    Utterance(
                        id="utt_1",
                        speaker="김민경",
                        speaker_id="user_001",
                        start=0.0,
                        end=3.0,
                        text=text,
                        confidence=0.9,
                    )
                ],
                metadata=TranscriptMetadata(
                    duration=3.0,
                    source=next(iter(TranscriptSource)),
                    privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
                ),
            ).model_dump(mode="json")
        )

    publish(OLD)
    (drafted,) = session.scalars(select(ExtActionItem)).all()
    service.update_action_item(session, drafted, ActionItemUpdate(status="todo"))
    session.commit()

    session.get(StoredUtterance, "utt_1").text = NEW  # A masks again
    session.commit()
    publish(NEW)

    session.expire_all()
    (kept,) = session.scalars(select(ExtActionItem)).all()
    assert kept.id == drafted.id, "the edit kept the item"
    assert "010-1234-5678" not in kept.description
    assert ("sync_action_item", kept.id) in queued
