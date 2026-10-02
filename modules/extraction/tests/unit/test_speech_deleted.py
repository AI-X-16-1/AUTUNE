"""A person deletes their own speech: B keeps the work and drops the words (#587).

Decided with the user (2026-10-01). Under test: unconfirmed drafts drawn from
the speech are deleted; a confirmed item whose description is the line itself
reads the placeholder and loses its date phrase, while a model's summary or a
person's writing stays; a decision loses ``original_statement`` and a line-only
statement reads the placeholder; the confirmed ones are queued to re-sync; and
the hook is safe to repeat, never stops on the queue, and stops on the database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
    ExtDecision,
    ExtDecisionRelated,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
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
        "sync_action_item",
        "sync_action_item_jira",
        "sync_action_item_calendar",
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
    assert {task for task, ident in queued if ident == "act_line"} == {
        "sync_action_item",
        "sync_action_item_jira",
        "sync_action_item_calendar",
    }
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

    monkeypatch.setattr(tasks, "sync_action_item", SimpleNamespace(delay=down))

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
