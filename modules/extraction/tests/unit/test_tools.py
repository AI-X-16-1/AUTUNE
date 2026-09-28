"""Module B's agent tools (#261): the ToolResult shape, the five-item cap, and
that unconfirmed content is counted rather than quoted (#261 rule 3).

SQLite in memory, the way ``test_read_endpoints`` builds B's tables.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import Base, Meeting, TeamMember, User, Utterance
from autune_extraction import service, tools
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
)

TEAM, OTHER_TEAM = "team_1", "team_2"
MEETING, OTHER_MEETING = "mtg_1", "mtg_9"
TODAY = date.today()
KEYS = {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

TABLES = [
    Meeting.__table__,
    User.__table__,
    TeamMember.__table__,
    Utterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    ExtDecisionRef.__table__,
    ExtConfirmation.__table__,
    ExtEditEvent.__table__,
    ExtExternalRef.__table__,
]


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTUNE_EXTRACTION_CANDIDATE_CONFIDENCE", raising=False)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
        s.add(Meeting(id=OTHER_MEETING, team_id=OTHER_TEAM, title="다른 팀 회의"))
        for uid, name, team in (("user_in", "박지영", TEAM), ("user_gone", "이건우", None)):
            s.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
            if team:
                s.add(TeamMember(team_id=team, user_id=uid))
        s.flush()
        yield s


def utterance(s: Session, uid: str, text: str, start: float, meeting: str = MEETING) -> str:
    s.add(
        Utterance(
            id=uid,
            meeting_id=meeting,
            speaker_label="S1",
            start_sec=start,
            end_sec=start + 1,
            text=text,
        )
    )
    s.flush()
    return uid


def item(
    s: Session,
    item_id: str,
    *,
    status: str = "todo",
    due: date | None = None,
    assignee: str | None = "user_in",
    meeting: str = MEETING,
    source: str | None = None,
) -> None:
    row = ExtActionItem(
        id=item_id,
        meeting_id=meeting,
        description=f"{item_id} 할 일",
        status=status,
        assignee_id=assignee,
        due_date=due,
        confidence=0.9,
        origin="model",
    )
    row.sources = [ExtActionItemSource(utterance_id=source)] if source else []
    s.add(row)
    s.flush()


def classification(s: Session, uid: str, kind: str) -> None:
    s.add(
        ExtClassification(
            utterance_id=uid,
            meeting_id=MEETING,
            kind=kind,
            confidence=0.9,
            model_version="test",
            nli_verified=False,
        )
    )
    s.flush()


# --- every tool -------------------------------------------------------------------


def test_every_tool_returns_the_tool_result_shape(session: Session) -> None:
    for tool in tools.TOOLS:
        args = (TEAM,) if tool is tools.open_action_items else (MEETING,)
        assert set(tool(session, *args)) == KEYS, tool.__name__


def test_every_docstring_starts_with_when_to_use_it() -> None:
    """agent-layer.md section 4 rule 2: the docstring is the prompt."""
    for tool in tools.TOOLS:
        assert (tool.__doc__ or "").startswith("Use this"), tool.__name__


@pytest.mark.parametrize(
    "tool", [tools.meeting_action_items, tools.unresolved_questions, tools.review_state]
)
def test_an_unknown_meeting_is_ok_false_not_an_exception(session: Session, tool) -> None:
    result = tool(session, "mtg_nope")
    assert result["ok"] is False
    assert "mtg_nope" in result["reason"]


# --- meeting_action_items -------------------------------------------------------------


def test_unconfirmed_items_are_counted_never_quoted(session: Session) -> None:
    """#261 rule 3: B content leaves only once a person confirmed it."""
    item(session, "act_confirmed", status="todo")
    item(session, "act_draft", status="needs_confirmation")

    result = tools.meeting_action_items(session, MEETING)

    titles = [i["title"] for i in result["items"]]
    assert titles == ["act_confirmed 할 일"]
    assert "확인 대기 1건" in result["summary"]


def test_reassignment_then_overdue_come_first(session: Session) -> None:
    item(session, "act_later", due=TODAY + timedelta(days=10))
    item(session, "act_late", due=TODAY - timedelta(days=2))
    item(session, "act_orphan", assignee="user_gone", due=TODAY + timedelta(days=20))
    item(session, "act_done", status="done", due=TODAY - timedelta(days=5))

    result = tools.meeting_action_items(session, MEETING)

    assert [i["id"] for i in result["items"]] == ["act_orphan", "act_late", "act_later", "act_done"]
    assert "재배정 필요" in result["items"][0]["body"]
    assert "기한 지남" in result["items"][1]["body"]
    assert "기한 지남" not in result["items"][3]["body"]  # done is never late
    assert "재배정이 필요한 항목 1건" in result["summary"]


def test_more_than_five_are_ranked_cut_and_marked_truncated(session: Session) -> None:
    for n in range(8):
        item(session, f"act_{n}", due=TODAY + timedelta(days=n))
    result = tools.meeting_action_items(session, MEETING)
    assert len(result["items"]) == 5
    assert result["truncated"] is True
    assert "확정된 액션아이템 8건" in result["summary"]


def test_evidence_is_utterance_ids_only(session: Session) -> None:
    said = utterance(session, "utt_said", "금요일까지 제가 정리할게요", 1.0)
    item(session, "act_1", source=said)
    result = tools.meeting_action_items(session, MEETING)
    assert result["evidence"] == ["utt_said"]


# --- open_action_items ---------------------------------------------------------------


def test_open_items_stay_inside_the_team(session: Session) -> None:
    item(session, "act_mine", due=TODAY + timedelta(days=1))
    item(session, "act_theirs", due=TODAY + timedelta(days=1), meeting=OTHER_MEETING)
    result = tools.open_action_items(session, TEAM)
    assert [i["id"] for i in result["items"]] == ["act_mine"]


def test_open_items_are_late_soon_or_ownerless_and_never_done_or_draft(session: Session) -> None:
    item(session, "act_late", due=TODAY - timedelta(days=1))
    item(session, "act_soon", status="in_progress", due=TODAY + timedelta(days=3))
    item(session, "act_far", due=TODAY + timedelta(days=30))
    item(session, "act_orphan", assignee="user_gone")
    item(session, "act_done", status="done", due=TODAY - timedelta(days=1))
    item(session, "act_draft", status="needs_confirmation", due=TODAY)

    result = tools.open_action_items(session, TEAM, within_days=7)

    assert [i["id"] for i in result["items"]] == ["act_orphan", "act_late", "act_soon"]
    assert "기한 지남 1건" in result["summary"]
    assert "7일 안에 기한 1건" in result["summary"]
    assert "재배정 필요 1건" in result["summary"]


def test_a_team_with_no_meetings_is_an_empty_answer(session: Session) -> None:
    result = tools.open_action_items(session, "team_empty")
    assert result["ok"] is True
    assert result["items"] == []


# --- unresolved_questions ---------------------------------------------------------------


def test_questions_and_concerns_in_spoken_order_with_their_text(session: Session) -> None:
    q = utterance(session, "utt_q", "그 예산은 누가 승인하나요?", 1.0)
    c = utterance(session, "utt_c", "일정이 너무 빡빡할 것 같아요", 2.0)
    k = utterance(session, "utt_k", "제가 금요일까지 할게요", 3.0)
    classification(session, q, "open_question")
    classification(session, c, "concern")
    classification(session, k, "commitment")

    result = tools.unresolved_questions(session, MEETING)

    assert [(i["title"], i["body"]) for i in result["items"]] == [
        ("질문", "그 예산은 누가 승인하나요?"),
        ("우려", "일정이 너무 빡빡할 것 같아요"),
    ]
    assert result["evidence"] == ["utt_q", "utt_c"]
    assert "검토 전" in result["summary"]


# --- review_state ------------------------------------------------------------------------


def test_review_state_counts_what_waits_and_quotes_none_of_it(session: Session) -> None:
    said = utterance(session, "utt_d", "이번엔 B안으로 가요", 1.0)
    decision = ExtDecision(id="dec_1", meeting_id=MEETING, statement="B안으로 간다", confidence=0.8)
    decision.sources = [ExtDecisionSource(utterance_id=said, position=0)]
    session.add(decision)
    item(session, "act_draft", status="needs_confirmation")
    session.flush()

    result = tools.review_state(session, MEETING)

    assert "결정 확인 대기 1건" in result["summary"]
    assert "액션아이템 확인 대기 1건" in result["summary"]
    assert {i["id"] for i in result["items"]} == {"dec_1", "act_draft"}
    assert all(i["body"] == "" for i in result["items"])
    assert result["evidence"] == ["utt_d"]
