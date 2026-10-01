"""A speaker identified after extraction becomes the item's assignee (#360).

SQLite in memory. What is under test: which items the fill touches -- a label
only, the model's, one identified and consenting speaker, no assignee edit by
a person -- and what the task does after it: sync an item that has already
been confirmed, and publish nothing, as a board edit publishes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance
from autune_extraction import service, tasks
from autune_extraction.models import ExtActionItem, ExtActionItemSource, ExtEditEvent

MEETING = "mtg_1"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(User(id="user_kim", email="kim@example.com", display_name="김민경"))
        s.add(User(id="user_lee", email="lee@example.com", display_name="이승환"))
        s.add_all(
            [
                Participant(
                    id="par_2", meeting_id=MEETING, speaker_label="Speaker 2", consented=True
                ),
                Participant(
                    id="par_3", meeting_id=MEETING, speaker_label="Speaker 3", consented=True
                ),
            ]
        )
        s.flush()
        yield s


def said(session: Session, utterance_id: str, participant_id: str | None) -> None:
    session.add(
        Utterance(
            id=utterance_id,
            meeting_id=MEETING,
            participant_id=participant_id,
            speaker_label="Speaker 2",
            start_sec=0.0,
            end_sec=2.0,
            text="제가 금요일까지 정리하겠습니다",
        )
    )


def item(
    session: Session,
    item_id: str,
    sources: list[str],
    *,
    origin: str = "model",
    status: str = "needs_confirmation",
    assignee_id: str | None = None,
) -> ExtActionItem:
    for source in sources:
        if session.get(Utterance, source) is None:
            said(session, source, "par_2")
    row = ExtActionItem(
        id=item_id,
        meeting_id=MEETING,
        description="금요일까지 정리할 예정",
        assignee_id=assignee_id,
        assignee_label=None if assignee_id else "Speaker 2",
        status=status,
        confidence=0.9,
        origin=origin,
        sources=[ExtActionItemSource(utterance_id=u) for u in sources],
    )
    session.add(row)
    session.flush()
    return row


def identify(session: Session, participant_id: str, user_id: str) -> None:
    """A's speaker confirmation: fills ``participants.user_id``."""
    participant = session.get(Participant, participant_id)
    assert participant is not None
    participant.user_id = user_id
    session.flush()


def test_an_identified_speaker_becomes_the_assignee_and_the_label_goes(session: Session) -> None:
    row = item(session, "act_1", ["utt_1"])
    identify(session, "par_2", "user_kim")

    filled = service.fill_identified_assignees(session)

    assert [i.id for i in filled] == ["act_1"]
    session.refresh(row)
    assert (row.assignee_id, row.assignee_label) == ("user_kim", None)


def test_nothing_changes_while_the_speaker_is_still_unidentified(session: Session) -> None:
    item(session, "act_1", ["utt_1"])

    assert service.fill_identified_assignees(session) == []


def test_a_second_run_changes_nothing(session: Session) -> None:
    item(session, "act_1", ["utt_1"])
    identify(session, "par_2", "user_kim")
    service.fill_identified_assignees(session)

    assert service.fill_identified_assignees(session) == []


@pytest.mark.parametrize("fields", ["assignee_id", "assignee_label", "assignee_id,due_date"])
def test_an_assignee_a_person_edited_is_left_alone(session: Session, fields: str) -> None:
    """Cleared on purpose is a choice too."""
    row = item(session, "act_1", ["utt_1"])
    session.add(
        ExtEditEvent(meeting_id=MEETING, action_item_id="act_1", kind="edited", fields=fields)
    )
    identify(session, "par_2", "user_kim")

    assert service.fill_identified_assignees(session) == []
    session.refresh(row)
    assert row.assignee_id is None


def test_an_edit_that_names_no_fields_is_read_as_maybe_the_assignee(session: Session) -> None:
    """Edit rows written before ``fields`` existed are NULL; one of them may
    have been the edit that relabelled the item (lsh2217's review of #536)."""
    row = item(session, "act_1", ["utt_1"])
    session.add(ExtEditEvent(meeting_id=MEETING, action_item_id="act_1", kind="edited"))
    identify(session, "par_2", "user_kim")

    assert service.fill_identified_assignees(session) == []
    session.refresh(row)
    assert row.assignee_id is None


def test_a_label_a_person_renamed_keeps_its_name(session: Session) -> None:
    """ "Speaker 2" became "민경님" on the board: not the label it was drafted
    with, so whoever typed it decided, whatever the edit log says."""
    row = item(session, "act_1", ["utt_1"])
    row.assignee_label = "민경님"
    session.flush()
    identify(session, "par_2", "user_kim")

    assert service.fill_identified_assignees(session) == []
    session.refresh(row)
    assert (row.assignee_id, row.assignee_label) == (None, "민경님")


def test_an_item_older_than_the_window_is_not_scanned(session: Session) -> None:
    row = item(session, "act_1", ["utt_1"])
    row.created_at = datetime.now(UTC) - service.FILL_WINDOW - timedelta(days=1)
    session.flush()
    identify(session, "par_2", "user_kim")

    assert service.fill_identified_assignees(session) == []


def test_an_edit_to_another_field_does_not_hold_it_back(session: Session) -> None:
    item(session, "act_1", ["utt_1"])
    session.add(
        ExtEditEvent(meeting_id=MEETING, action_item_id="act_1", kind="edited", fields="due_date")
    )
    identify(session, "par_2", "user_kim")

    assert [i.id for i in service.fill_identified_assignees(session)] == ["act_1"]


def test_an_item_a_person_typed_is_left_alone(session: Session) -> None:
    item(session, "act_1", ["utt_1"], origin="user")
    identify(session, "par_2", "user_kim")

    assert service.fill_identified_assignees(session) == []


def test_sources_spoken_by_two_people_are_left_for_a_person(session: Session) -> None:
    said(session, "utt_1", "par_2")
    said(session, "utt_2", "par_3")
    item(session, "act_1", ["utt_1", "utt_2"])
    identify(session, "par_2", "user_kim")
    identify(session, "par_3", "user_lee")

    assert service.fill_identified_assignees(session) == []


def test_a_speaker_who_did_not_consent_is_not_read(session: Session) -> None:
    item(session, "act_1", ["utt_1"])
    identify(session, "par_2", "user_kim")
    participant = session.get(Participant, "par_2")
    assert participant is not None
    participant.consented = False
    session.flush()

    assert service.fill_identified_assignees(session) == []


# --- the task --------------------------------------------------------------------


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    sent: dict[str, list] = {"published": [], "synced": []}
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(
        tasks, "publish", lambda event, payload: sent["published"].append((event, payload)) or []
    )
    monkeypatch.setattr(tasks, "sync_after_confirmation", sent["synced"].append)
    return sent


def test_the_task_syncs_only_a_confirmed_item_and_publishes_nothing(
    session: Session, wired: dict[str, list]
) -> None:
    """A board edit publishes no ``ExtractionResult``; neither does this."""
    item(session, "act_draft", ["utt_1"])
    item(session, "act_done", ["utt_2"], status="todo")
    identify(session, "par_2", "user_kim")

    filled = tasks.fill_identified_assignees()

    assert sorted(filled) == ["act_done", "act_draft"]
    assert wired["published"] == []
    assert wired["synced"] == ["act_done"]


def test_the_task_publishes_nothing_when_nothing_changed(
    session: Session, wired: dict[str, list]
) -> None:
    item(session, "act_1", ["utt_1"])

    assert tasks.fill_identified_assignees() == []
    assert wired == {"published": [], "synced": []}
