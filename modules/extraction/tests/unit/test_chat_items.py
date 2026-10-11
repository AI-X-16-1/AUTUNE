"""Action items the chat drafts from an utterance (decided with the user, 2026-10-01).

The rules under test: the item is drafted from an utterance id, never from text
a model wrote; its summary is written the way the pipeline writes one, from
consenting lines only; it waits for confirmation with the original utterances
under it; once confirmed they are hidden from the screens and the tools but
kept; only the summary goes to Notion; and drafting runs at L1.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Participant, TeamMember, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tools
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemRelated,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtClassification,
    ExtConfirmation,
    ExtEditEvent,
    ExtExternalRef,
    ExtSyncFailure,
)
from autune_extraction.noun_form import tidy
from autune_extraction.pipeline.base import Resolution, ResolutionRequest

TEAM = "team_1"
MEETING = "mtg_1"
OTHER_MEETING = "mtg_other"

LINES = [
    ("utt_0", "par_yes", "결제 로그에 필드 하나 더 넣는 건 얘기했죠"),
    ("utt_1", "par_yes", "그거 제가 금요일까지 할게요"),
    ("utt_2", "par_no", "제 개인 번호로 연락 주세요"),
]
SUMMARY = "결제 로그 필드 추가 제가 금요일까지 할게요"

TABLES = [
    User.__table__,
    Meeting.__table__,
    TeamMember.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtActionItemRelated.__table__,
    ExtEditEvent.__table__,
    ExtConfirmation.__table__,
    ExtExternalRef.__table__,
    # Every read of an item looks these up (#680): its failed copies, its event.
    ExtCalendarEvent.__table__,
    ExtSyncFailure.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as s:
        s.add(User(id="user_kim", email="kim@example.com", display_name="김"))
        s.add(TeamMember(team_id=TEAM, user_id="user_kim"))
        s.add(
            Meeting(
                id=MEETING,
                team_id=TEAM,
                title="주간 회의",
                started_at=datetime(2026, 9, 28, 1, tzinfo=UTC),
            )
        )
        s.add(Meeting(id=OTHER_MEETING, team_id="team_other", title="다른 팀"))
        s.add(
            Participant(
                id="par_yes",
                meeting_id=MEETING,
                user_id="user_kim",
                speaker_label="김",
                consented=True,
            )
        )
        s.add(Participant(id="par_no", meeting_id=MEETING, speaker_label="박", consented=False))
        for n, (uid, who, text) in enumerate(LINES):
            s.add(
                StoredUtterance(
                    id=uid,
                    meeting_id=MEETING,
                    participant_id=who,
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
        s.flush()
        yield s


class _CitingResolver:
    """Writes ``SUMMARY`` and cites a consenting line, a non-consenting one and
    an id that is not in the meeting -- only the first may be kept."""

    model_version = "test"

    def __init__(self) -> None:
        self.seen: list[ResolutionRequest] = []

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        return [r.target for r in requests]

    def resolve_with_evidence(self, requests: list[ResolutionRequest]) -> list[Resolution]:
        self.seen = requests
        return [Resolution(SUMMARY, used=("utt_0", "utt_2", "utt_elsewhere")) for _ in requests]


@pytest.fixture
def resolver(session: Session, monkeypatch: pytest.MonkeyPatch) -> _CitingResolver:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    fake = _CitingResolver()
    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools, "get_resolver", lambda: fake)
    return fake


def _draft(session: Session) -> ExtActionItem:
    result = tools.add_action_item(TEAM, MEETING, "utt_1")
    assert result["ok"] is True, result
    # The board's word for an item nobody confirmed.
    assert result["summary"] == "할 일 초안을 만들었습니다 (확인 필요)."
    row = session.get(ExtActionItem, result["items"][0]["id"])
    assert row is not None
    return row


def test_a_chat_draft_is_the_pipelines_summary_of_one_utterance(
    session: Session, resolver: _CitingResolver
) -> None:
    row = _draft(session)

    assert (row.origin, row.status) == ("chat", "needs_confirmation")
    assert row.description == tidy(SUMMARY)
    assert row.description_resolved is True
    assert row.assignee_id == "user_kim"  # the speaker, as for a model item
    assert row.due_date is not None and row.due_text  # "금요일까지", read from the line
    assert [s.utterance_id for s in row.sources] == ["utt_1"]
    # Cited lines kept only when the model could have been shown them.
    assert [r.utterance_id for r in row.related] == ["utt_0"]
    # Not a person finding what the model missed.
    assert session.query(ExtEditEvent).count() == 0


def test_a_non_consenting_line_never_reaches_the_summary(
    session: Session, resolver: _CitingResolver
) -> None:
    _draft(session)

    (request,) = resolver.seen
    sent = [request.target, *request.context, *request.context_after]
    sent += [text for _, text in request.related]
    assert "제 개인 번호로 연락 주세요" not in sent
    assert "utt_2" not in {line_id for line_id, _ in request.related}


@pytest.mark.parametrize(
    ("meeting", "utterance"),
    [
        (MEETING, "utt_2"),  # its speaker did not consent
        (MEETING, "utt_nope"),
        (OTHER_MEETING, "utt_1"),  # another team's meeting
    ],
)
def test_a_draft_is_refused_without_writing(
    session: Session, resolver: _CitingResolver, meeting: str, utterance: str
) -> None:
    result = tools.add_action_item(TEAM, meeting, utterance)

    assert result["ok"] is False
    assert session.query(ExtActionItem).count() == 0


def test_an_explicit_assignee_and_date_win_over_the_line(
    session: Session, resolver: _CitingResolver
) -> None:
    result = tools.add_action_item(TEAM, MEETING, "utt_1", due_date="2026-10-09")
    row = session.get(ExtActionItem, result["items"][0]["id"])

    assert row is not None and str(row.due_date) == "2026-10-09"
    assert row.due_text is None  # the phrase explains a date nobody chose here
    assert tools.add_action_item(TEAM, MEETING, "utt_1", assignee_id="user_gone")["ok"] is False


def test_once_confirmed_the_originals_are_hidden_not_deleted(
    session: Session, resolver: _CitingResolver
) -> None:
    row = _draft(session)

    waiting = service.read_detail(session, row)
    assert waiting.source_utterance_ids == ["utt_1"]
    assert [s.text for s in waiting.sources] == ["그거 제가 금요일까지 할게요"]
    assert waiting.related and waiting.context

    row.status = "todo"
    session.flush()

    confirmed = service.read_detail(session, row)
    assert confirmed.source_utterance_ids == []
    assert (confirmed.sources, confirmed.context, confirmed.related) == ([], [], [])
    assert confirmed.deleted_source_count == 0, "not shown as evidence that was lost"
    # Kept: D and E still count the source, and a meeting deletion takes it.
    assert session.scalar(select(ExtActionItemSource.utterance_id)) == "utt_1"
    assert session.query(ExtActionItemRelated).count() == 1
    assert service.contract_action_item(row).source_utterance_ids == ["utt_1"]


def test_a_confirmed_model_item_still_shows_its_originals(session: Session) -> None:
    row = ExtActionItem(
        meeting_id=MEETING,
        description="필드 추가",
        status="todo",
        confidence=0.9,
        origin="model",
        sources=[ExtActionItemSource(utterance_id="utt_1")],
    )
    session.add(row)
    session.flush()

    assert service.read_model(row).source_utterance_ids == ["utt_1"]


def test_only_the_summary_goes_to_notion(session: Session, resolver: _CitingResolver) -> None:
    row = _draft(session)
    row.status = "todo"
    session.flush()

    page: dict[str, Any] = service.notion_properties(row, "주간 회의", service.NOTION_PROPERTIES)

    sent = str(page)
    assert tidy(SUMMARY) in sent
    for _, _, text in LINES:
        assert text not in sent


def test_drafting_runs_at_l1_and_every_other_write_waits() -> None:
    assert [tools.add_action_item] == tools.L1_ACTIONS
    assert set(tools.L1_ACTIONS) <= set(tools.ACTIONS)
