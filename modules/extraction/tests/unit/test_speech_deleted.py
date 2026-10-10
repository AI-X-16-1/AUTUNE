"""A person deletes their own speech: B keeps the work and drops the words (#587).

Decided with the user (2026-10-01). Under test: unconfirmed drafts drawn from
the speech are deleted; a confirmed item whose description is the line itself
reads the placeholder and loses its date phrase, while a model's summary or a
person's writing stays; a decision loses ``original_statement`` and a line-only
statement reads the placeholder; the confirmed ones are queued to re-sync; and
the hook is safe to repeat, never stops on the queue, and stops on the database.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_core import Base, Meeting, User, Utterance
from autune_core.deletion import registered_speech_modules
from autune_extraction import service, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemRelated,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionRelated,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
)

GONE = ["utt_gone1", "utt_gone2"]
PLACEHOLDER = service.SPEECH_DELETED_TEXT


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id="mtg_1", team_id="team_1", title="주간 회의"))
        for n, uid in enumerate([*GONE, "utt_kept"]):
            s.add(
                Utterance(
                    id=uid,
                    meeting_id="mtg_1",
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=f"{uid} 말",
                )
            )
        s.flush()

        def item(
            item_id: str,
            *,
            status: str,
            origin: str = "model",
            resolved: bool = False,
            source: str = "utt_gone1",
        ) -> None:
            s.add(
                ExtActionItem(
                    id=item_id,
                    meeting_id="mtg_1",
                    description=f"{item_id} 원문",
                    description_resolved=resolved,
                    due_text="금요일까지",
                    status=status,
                    confidence=0.9,
                    origin=origin,
                    sources=[ExtActionItemSource(utterance_id=source)],
                )
            )

        item("act_draft", status="needs_confirmation")
        item("act_chatdraft", status="needs_confirmation", origin="chat")
        item("act_userdraft", status="needs_confirmation", origin="user")
        item("act_line", status="todo")
        item("act_summary", status="todo", resolved=True)
        item("act_edited", status="todo")
        item("act_oldedit", status="todo")
        item("act_other", status="todo", source="utt_kept")
        s.flush()
        s.add(ExtActionItemRelated(action_item_id="act_summary", utterance_id="utt_kept"))
        s.add(
            ExtEditEvent(
                meeting_id="mtg_1",
                action_item_id="act_edited",
                kind="edited",
                fields="description,due_date",
            )
        )
        s.add(
            ExtEditEvent(
                meeting_id="mtg_1", action_item_id="act_oldedit", kind="edited", fields=None
            )
        )

        def decision(
            dec_id: str, *, origin: str = "model", written_up: bool = False, confirmed: bool = True
        ) -> None:
            s.add(
                ExtDecision(
                    id=dec_id,
                    meeting_id="mtg_1",
                    statement=f"{dec_id} 원문",
                    original_statement=f"{dec_id} 원래 문장" if origin == "model" else None,
                    statement_resolved=written_up,
                    confidence=0.9,
                    origin=origin,
                    sources=[ExtDecisionSource(utterance_id="utt_gone2", position=0)],
                )
            )
            s.flush()
            if written_up:
                s.add(ExtDecisionRelated(decision_id=dec_id, utterance_id="utt_kept"))
            if confirmed:
                s.add(ExtDecisionReview(decision_id=dec_id, meeting_id="mtg_1", status="confirmed"))

        decision("dec_line")
        decision("dec_writeup", written_up=True)
        decision("dec_typed", origin="user")
        decision("dec_pending", confirmed=False)
        s.commit()
        yield s


@pytest.fixture
def queued(session: Session, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks, "session_scope", scope)
    for task in (
        "sync_item_copies",
        "sync_decision",
    ):
        stub = SimpleNamespace(delay=lambda ident, task=task: sent.append((task, ident)))
        monkeypatch.setattr(tasks, task, stub)
    return sent


def item_state(session: Session, item_id: str) -> tuple[str, str | None] | None:
    session.expire_all()
    row = session.get(ExtActionItem, item_id)
    return (row.description, row.due_text) if row else None


def decision_state(session: Session, dec_id: str) -> tuple[str, str | None]:
    session.expire_all()
    row = session.get(ExtDecision, dec_id)
    assert row is not None
    return row.statement, row.original_statement


def test_the_hook_is_registered() -> None:
    assert "extraction" in registered_speech_modules()


def test_unconfirmed_drafts_go_and_a_persons_draft_stays(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    tasks.forget_deleted_speech("user_1", GONE)

    assert item_state(session, "act_draft") is None
    assert item_state(session, "act_chatdraft") is None
    assert item_state(session, "act_userdraft") == ("act_userdraft 원문", None)


@pytest.mark.parametrize("system", ["notion", "jira"])
def test_a_draft_that_was_confirmed_once_is_kept_and_its_copy_follows(
    session: Session, queued: list[tuple[str, str]], system: str
) -> None:
    """Moved back to 확인 필요 it still has its page or its issue (#657). Deleting
    the row, as for a draft nobody accepted, would leave the deleted words
    outside with nothing left to find them by; so it reads the placeholder and
    its copies are queued, like a confirmed item."""
    session.add(
        ExtActionItem(
            id="act_back",
            meeting_id="mtg_1",
            description="act_back 원문",
            description_resolved=False,
            due_text="금요일까지",
            status="needs_confirmation",
            confidence=0.9,
            origin="model",
            sources=[ExtActionItemSource(utterance_id="utt_gone1")],
        )
    )
    session.flush()
    session.add(
        ExtExternalRef(
            action_item_id="act_back", system=system, meeting_id="mtg_1", external_id="x"
        )
    )
    session.commit()

    tasks.forget_deleted_speech("user_1", GONE)

    assert item_state(session, "act_back") == (PLACEHOLDER, None)
    assert {task for task, ident in queued if ident == "act_back"} == {"sync_item_copies"}
    assert item_state(session, "act_draft") is None, "a draft never confirmed still goes"


def test_a_draft_whose_only_copy_is_a_calendar_event_is_kept_too(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    """#672: the event's title is the item's description. Deleting the draft
    took the event's row with it and left the title on the calendar; kept, its
    calendar sync is queued and takes the event off."""
    session.add(
        ExtActionItem(
            id="act_event",
            meeting_id="mtg_1",
            description="act_event 원문",
            description_resolved=False,
            due_text="금요일까지",
            status="needs_confirmation",
            confidence=0.9,
            origin="model",
            sources=[ExtActionItemSource(utterance_id="utt_gone1")],
        )
    )
    session.flush()
    session.add(
        ExtCalendarEvent(
            action_item_id="act_event",
            meeting_id="mtg_1",
            user_id="user_1",
            event_id="evt_1",
            synced_due_date=date(2026, 10, 2),
        )
    )
    session.commit()

    tasks.forget_deleted_speech("user_1", GONE)

    assert item_state(session, "act_event") == (PLACEHOLDER, None)
    assert ("sync_item_copies", "act_event") in queued


def test_the_hook_is_registered_in_a_process_that_only_imported_the_router() -> None:
    """A calls the speech hooks from the API process (#628), and B's hook lives
    in ``tasks``, which that process has only because ``autune_extraction.router``
    imports it. ``test_the_hook_is_registered`` runs where ``tasks`` is already
    imported and would pass without that import -- and a speech deletion would
    then never reach B. A fresh interpreter, as #671 does for C."""
    probe = (
        "import autune_extraction.router\n"
        "from autune_core.deletion import registered_speech_modules\n"
        "assert 'extraction' in registered_speech_modules()\n"
    )

    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr[-500:]


def test_a_confirmed_line_reads_the_placeholder_the_rest_stays(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    tasks.forget_deleted_speech("user_1", GONE)

    assert item_state(session, "act_line") == (PLACEHOLDER, None)
    assert item_state(session, "act_summary") == ("act_summary 원문", None), "a summary stays"
    assert item_state(session, "act_edited") == ("act_edited 원문", None), "a person wrote it"
    assert item_state(session, "act_oldedit") == ("act_oldedit 원문", None), "cannot tell: kept"
    assert item_state(session, "act_other") == ("act_other 원문", "금요일까지"), "other speech"
    assert session.scalar(select(ExtEditEvent.id).where(ExtEditEvent.kind == "deleted")) is None


def test_decisions_lose_the_original_and_a_line_only_statement(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    tasks.forget_deleted_speech("user_1", GONE)

    assert decision_state(session, "dec_line") == (PLACEHOLDER, None)
    assert decision_state(session, "dec_writeup") == ("dec_writeup 원문", None)
    assert decision_state(session, "dec_typed") == ("dec_typed 원문", None)


def test_only_confirmed_changes_are_queued_to_follow(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    tasks.forget_deleted_speech("user_1", GONE)

    items = {ident for task, ident in queued if task != "sync_decision"}
    decisions = {ident for task, ident in queued if task == "sync_decision"}
    assert items == {"act_line", "act_summary", "act_edited", "act_oldedit"}
    assert {task for task, ident in queued if ident == "act_line"} == {"sync_item_copies"}
    assert decisions == {"dec_line", "dec_writeup"}, "not the unconfirmed one, not untouched"


def test_running_it_twice_changes_and_queues_nothing_more(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    tasks.forget_deleted_speech("user_1", GONE)
    first = len(queued)

    tasks.forget_deleted_speech("user_1", GONE)

    assert len(queued) == first
    assert item_state(session, "act_line") == (PLACEHOLDER, None)


def test_a_queue_failure_does_not_stop_the_deletion(
    session: Session, queued: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(_: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks, "sync_item_copies", SimpleNamespace(delay=down))

    tasks.forget_deleted_speech("user_1", GONE)  # must not raise

    assert item_state(session, "act_line") == (PLACEHOLDER, None), "the words went anyway"


def test_a_database_failure_stops_the_deletion(
    session: Session, queued: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any) -> None:
        raise RuntimeError("database gone")

    monkeypatch.setattr(tasks.service, "forget_speech", broken)

    with pytest.raises(RuntimeError):
        tasks.forget_deleted_speech("user_1", GONE)


def test_a_decision_put_back_with_its_page_still_there_is_queued_too(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    """#669: ``dec_pending`` is not confirmed, but a page from an earlier
    confirmation would keep the deleted words. Queued, its sync retires the
    page."""
    session.add(
        ExtDecisionRef(
            decision_id="dec_pending", system="notion", meeting_id="mtg_1", external_id="page_9"
        )
    )
    session.commit()

    tasks.forget_deleted_speech("user_1", GONE)

    decisions = {ident for task, ident in queued if task == "sync_decision"}
    assert decisions == {"dec_line", "dec_writeup", "dec_pending"}


# --- the short title of a line that is gone (reviews of #1141) ------------------


def _give_titles(session: Session) -> None:
    for item_id in ("act_line", "act_summary", "act_other"):
        item = session.get(ExtActionItem, item_id)
        assert item is not None
        item.title = f"{item_id} 제목"
    for dec_id in ("dec_line", "dec_writeup"):
        decision = session.get(ExtDecision, dec_id)
        assert decision is not None
        decision.title = f"{dec_id} 제목"
    session.commit()


def test_deleting_speech_takes_the_title_of_its_line_with_it(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    """A title is the deleted line in twenty characters: it goes when the line
    does. Through the deletion itself and not the model's validator alone, so a
    deletion that one day writes the placeholder some other way still has to
    clear it."""
    _give_titles(session)

    tasks.forget_deleted_speech("user_1", GONE)

    session.expire_all()
    line = session.get(ExtActionItem, "act_line")
    said = session.get(ExtDecision, "dec_line")
    assert line is not None and said is not None
    assert (line.description, line.title) == (PLACEHOLDER, None)
    assert (said.statement, said.title) == (PLACEHOLDER, None)


def test_deleting_speech_takes_every_title_of_that_meeting(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    """Module B's owner, 2026-10-10. A title is written in a request that
    carried the meeting's other sentences, and one word of it may be another
    row's -- a row this deletion removes or blanks. No title says which
    request wrote it, so every title of the meeting goes: on a row whose
    sentence stays (a summary is the team's record; its title was never
    accepted by anybody), and on a row drawn from speech that was not deleted.
    Until then the title of a row that stayed stayed with it (#1141)."""
    _give_titles(session)
    session.add(Meeting(id="mtg_2", team_id="team_1", title="다른 회의"))
    session.add(
        ExtActionItem(
            id="act_elsewhere",
            meeting_id="mtg_2",
            description="다른 회의의 할 일",
            title="다른 회의 제목",
            status="todo",
            confidence=0.9,
            origin="model",
        )
    )
    session.commit()

    tasks.forget_deleted_speech("user_1", GONE)

    session.expire_all()
    titles = {
        row.id: row.title
        for model in (ExtActionItem, ExtDecision)
        for row in session.scalars(select(model).where(model.meeting_id == "mtg_1"))
    }
    assert titles["act_summary"] is None, "its sentence stays, its title does not"
    assert titles["dec_writeup"] is None
    assert titles["act_other"] is None, "drawn from a line nobody deleted, in the same meeting"
    assert set(titles.values()) == {None}
    summary, other = (
        session.get(ExtActionItem, "act_summary"),
        session.get(ExtActionItem, "act_other"),
    )
    assert summary is not None and other is not None
    assert (summary.description, other.description) == ("act_summary 원문", "act_other 원문")
    elsewhere = session.get(ExtActionItem, "act_elsewhere")
    assert elsewhere is not None and elsewhere.title == "다른 회의 제목", "another meeting's"
    # A title is not a change of text: no copy outside is queued for it.
    assert ("sync_item_copies", "act_other") not in queued


def test_no_title_is_asked_for_a_row_that_reads_the_placeholder(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    before = {target.id for target in service.title_targets(session, "mtg_1")}
    assert {"act_line", "dec_line", "dec_pending"} <= before

    tasks.forget_deleted_speech("user_1", GONE)

    targets = service.title_targets(session, "mtg_1")
    assert PLACEHOLDER not in {target.text for target in targets}
    assert not {"act_line", "dec_line", "dec_pending"} & {target.id for target in targets}
    assert {"act_summary", "act_other", "dec_writeup"} <= {target.id for target in targets}, (
        "a row that still says a sentence of the pipeline's is still asked about"
    )


def test_a_title_answered_while_the_speech_was_deleted_is_not_stored(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    """The model answers with no session open. Its title is of the line that
    was sent, and that line is gone."""
    targets = [
        target
        for target in service.title_targets(session, "mtg_1")
        if target.id in ("act_line", "dec_line")
    ]
    assert len(targets) == 2

    tasks.forget_deleted_speech("user_1", GONE)
    written = service.store_titles(session, targets, ["지워진 말의 제목"] * 2)

    assert written == 0
    session.expire_all()
    line, said = session.get(ExtActionItem, "act_line"), session.get(ExtDecision, "dec_line")
    assert line is not None and said is not None
    assert (line.title, said.title) == (None, None)


@pytest.mark.parametrize(
    "gone",
    [
        "act_draft",  # an unconfirmed draft: the deletion removes the row
        "act_line",  # a confirmed line: the row stays and reads the placeholder
    ],
)
def test_a_title_of_another_row_answered_while_the_speech_was_deleted_is_not_stored(
    session: Session, queued: list[tuple[str, str]], gone: str
) -> None:
    """The request that titled ``act_other`` carried the deleted sentence
    beside it, and one word of the answer may be that sentence's. ``act_other``
    still says what it said, so nothing about its own row stops the title;
    the row that is gone from the same request does."""
    targets = [
        target
        for target in service.title_targets(session, "mtg_1")
        if target.id in ("act_other", gone)
    ]
    assert {target.id for target in targets} == {"act_other", gone}

    tasks.forget_deleted_speech("user_1", GONE)
    written = service.store_titles(session, targets, ["지워진 말을 빌린 제목"] * 2)

    assert written == 0
    session.expire_all()
    other = session.get(ExtActionItem, "act_other")
    assert other is not None
    assert (other.description, other.title) == ("act_other 원문", None)


def test_a_title_is_stored_when_every_sentence_of_its_request_still_stands(
    session: Session, queued: list[tuple[str, str]]
) -> None:
    """The deletion does not close the meeting to titles: a request made of
    what is left afterwards is stored."""
    tasks.forget_deleted_speech("user_1", GONE)
    targets = service.title_targets(session, "mtg_1")
    assert "act_other" in {target.id for target in targets}

    written = service.store_titles(session, targets, ["남은 문장의 제목"] * len(targets))

    assert written == len(targets)
    session.expire_all()
    other = session.get(ExtActionItem, "act_other")
    assert other is not None and other.title == "남은 문장의 제목"
