"""Module B's agent tools (#261): the ToolResult shape, the five-item cap, and
that unconfirmed content is counted rather than quoted (#261 rule 3).

SQLite in memory, the way ``test_read_endpoints`` builds B's tables.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

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
    ExtNotionTarget,
)
from autune_extraction.schemas import ActionItemCreate

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
    ExtNotionTarget.__table__,
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


ARGS = {
    tools.open_action_items: (TEAM,),
    tools.workload_by_owner: (TEAM,),
    tools.person_action_items: (TEAM, "user_in"),
    tools.action_item_status: (TEAM, "act_missing"),
    tools.open_followup_item: (TEAM,),
}


def test_every_tool_returns_the_tool_result_shape(session: Session) -> None:
    for tool in tools.TOOLS:
        assert set(tool(session, *ARGS.get(tool, (MEETING,)))) == KEYS, tool.__name__


def test_every_docstring_starts_with_when_to_use_it() -> None:
    """agent-layer.md section 4 rule 2: the docstring is the prompt."""
    for tool in tools.TOOLS:
        assert (tool.__doc__ or "").startswith("Use this"), tool.__name__


@pytest.mark.parametrize(
    "tool",
    [
        tools.meeting_action_items,
        tools.unresolved_questions,
        tools.review_state,
        tools.meeting_decisions,
    ],
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


def test_another_teams_jira_issue_does_not_break_this_teams_open_items(session: Session) -> None:
    """The list is read across teams and narrowed to this one afterwards, so
    before #650 a Jira ref on anyone's item raised here for every team."""
    item(session, "act_mine", due=TODAY + timedelta(days=1))
    item(session, "act_theirs", due=TODAY + timedelta(days=1), meeting=OTHER_MEETING)
    session.add(
        ExtExternalRef(
            action_item_id="act_theirs",
            system="jira",
            meeting_id=OTHER_MEETING,
            external_id="AUT-7",
            url="https://x.atlassian.net/browse/AUT-7",
        )
    )
    session.flush()

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


# --- workload_by_owner (#261 section 3.1, Workload) ----------------------------------


def member(s: Session, uid: str, name: str, team: str = TEAM) -> str:
    s.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
    s.add(TeamMember(team_id=team, user_id=uid))
    s.flush()
    return uid


def rows(result: dict) -> dict[str, str]:
    return {i["id"]: i["body"] for i in result["items"]}


def test_the_loaded_come_first_and_the_free_are_shown(session: Session) -> None:
    member(session, "user_busy", "김바쁨")
    member(session, "user_idle", "최한가")
    for n in range(4):
        item(session, f"act_busy_{n}", assignee="user_busy")
    item(session, "act_idle_done", assignee="user_idle", status="done")
    item(session, "act_in", assignee="user_in")

    result = tools.workload_by_owner(session, TEAM)

    ids = [i["id"] for i in result["items"]]
    assert ids[0] == "user_busy"
    assert rows(result)["user_busy"] == "진행 중 4 · 기한 지남 0 · 완료 0 · 몰림"
    assert rows(result)["user_idle"] == "진행 중 0 · 기한 지남 0 · 완료 1 · 여유"
    assert "몰림 1명" in result["summary"]


def test_two_overdue_items_are_enough_to_be_loaded(session: Session) -> None:
    for n in range(2):
        item(session, f"act_late_{n}", due=TODAY - timedelta(days=3))

    result = tools.workload_by_owner(session, TEAM)

    assert rows(result)["user_in"].endswith("· 몰림")
    assert "기한 지남 2" in rows(result)["user_in"]


def test_a_member_with_no_items_is_free_too(session: Session) -> None:
    member(session, "user_new", "정새로")

    result = tools.workload_by_owner(session, TEAM)

    assert rows(result)["user_new"] == "진행 중 0 · 기한 지남 0 · 완료 0 · 여유"


def test_open_work_nobody_on_the_team_holds_is_one_row(session: Session) -> None:
    """A departed assignee's id is cleared at read (ADR 0007), so the item is
    counted as the team's to hand out, not as the departed person's."""
    item(session, "act_orphan", assignee="user_gone")
    item(session, "act_nobody", assignee=None)
    item(session, "act_gone_done", assignee="user_gone", status="done")

    result = tools.workload_by_owner(session, TEAM)

    assert rows(result)["unowned"] == "진행 중 2 · 기한 지남 0"
    assert "user_gone" not in rows(result)
    assert "담당 없는 진행 중 항목 2건" in result["summary"]


def test_unconfirmed_items_are_nobodys_work_yet(session: Session) -> None:
    item(session, "act_draft", status="needs_confirmation")

    result = tools.workload_by_owner(session, TEAM)

    assert rows(result)["user_in"] == "진행 중 0 · 기한 지남 0 · 완료 0 · 여유"


def test_other_teams_and_old_meetings_are_left_out(session: Session) -> None:
    session.add(
        Meeting(
            id="mtg_old",
            team_id=TEAM,
            title="석 달 전 회의",
            started_at=datetime.now(UTC) - timedelta(days=90),
        )
    )
    session.flush()
    item(session, "act_old", meeting="mtg_old")
    item(session, "act_other_team", meeting=OTHER_MEETING)

    result = tools.workload_by_owner(session, TEAM)

    assert rows(result)["user_in"].startswith("진행 중 0 ")


def test_both_ends_fit_inside_five(session: Session) -> None:
    for n in range(6):
        uid = member(session, f"user_busy_{n}", f"바쁨{n}")
        for k in range(4):
            item(session, f"act_{n}_{k}", assignee=uid)
    member(session, "user_idle", "최한가")

    result = tools.workload_by_owner(session, TEAM)

    assert len(result["items"]) == 5
    assert result["truncated"] is True
    assert "user_idle" in rows(result)


def test_workload_cites_no_utterance_and_quotes_no_item(session: Session) -> None:
    """Counts only: no utterance id, no item text reaches the manager's subagent."""
    said = utterance(session, "utt_said", "금요일까지 제가 정리할게요", 1.0)
    item(session, "act_1", source=said)

    result = tools.workload_by_owner(session, TEAM)

    assert result["evidence"] == []
    assert all("할 일" not in i["body"] and "할 일" not in i["title"] for i in result["items"])


# --- meeting_decisions ------------------------------------------------------------


def decision(s: Session, dec_id: str, *, status: str | None, meeting: str = MEETING) -> None:
    s.add(ExtDecision(id=dec_id, meeting_id=meeting, statement=f"{dec_id} 결정", confidence=0.8))
    s.flush()
    if status is not None:
        s.add(ExtDecisionReview(decision_id=dec_id, meeting_id=meeting, status=status))
        s.flush()


def test_only_confirmed_decisions_are_quoted(session: Session) -> None:
    decision(session, "dec_ok", status="confirmed")
    decision(session, "dec_wait", status=None)
    decision(session, "dec_no", status="rejected")

    result = tools.meeting_decisions(session, MEETING)

    assert [i["title"] for i in result["items"]] == ["dec_ok 결정"]
    assert "확인 대기 1건" in result["summary"]
    assert "dec_wait 결정" not in str(result)
    assert "dec_no 결정" not in str(result)


# --- person_action_items ----------------------------------------------------------


def test_one_persons_open_confirmed_items_in_this_team(session: Session) -> None:
    item(session, "act_mine", due=TODAY - timedelta(days=1))
    item(session, "act_done", status="done")
    item(session, "act_draft", status="needs_confirmation")
    item(session, "act_other", assignee=None)
    item(session, "act_elsewhere", meeting=OTHER_MEETING)

    result = tools.person_action_items(session, TEAM, "user_in")

    assert [i["id"] for i in result["items"]] == ["act_mine"]
    assert result["summary"] == "박지영님의 진행 중 액션아이템 1건, 기한 지남 1건."


def test_someone_off_the_team_is_refused(session: Session) -> None:
    result = tools.person_action_items(session, TEAM, "user_gone")
    assert result["ok"] is False
    assert result["items"] == []


# --- action_item_status -----------------------------------------------------------


def test_a_confirmed_item_with_where_it_went(session: Session) -> None:
    item(session, "act_1", due=TODAY + timedelta(days=2))
    session.add(
        ExtExternalRef(
            action_item_id="act_1",
            system="notion",
            meeting_id=MEETING,
            external_id="page",
            url="https://notion.so/page",
        )
    )
    session.flush()

    (finding,) = tools.action_item_status(session, TEAM, "act_1")["items"]

    assert finding["title"] == "act_1 할 일"
    assert finding["body"].endswith("notion 연동됨")


def test_an_unconfirmed_item_is_reported_without_its_text(session: Session) -> None:
    item(session, "act_draft", status="needs_confirmation")

    result = tools.action_item_status(session, TEAM, "act_draft")

    assert "act_draft 할 일" not in str(result)
    assert result["items"][0]["title"] == "확인 대기"


def test_another_teams_item_is_the_same_as_an_unknown_one(session: Session) -> None:
    item(session, "act_theirs", meeting=OTHER_MEETING)

    theirs = tools.action_item_status(session, TEAM, "act_theirs")
    unknown = tools.action_item_status(session, TEAM, "act_nope")

    assert theirs["ok"] is False
    assert unknown["ok"] is False
    assert theirs["summary"] == unknown["summary"]


# --- actions: L2, run by the main agent after approval ------------------------------


@pytest.fixture
def acting(session: Session, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Actions own their transaction; here it is this session. The after-commit
    syncs are recorded instead of reaching Notion or a calendar."""
    synced: dict[str, list[str]] = {"items": [], "decisions": []}

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tools, "session_scope", scope)
    monkeypatch.setattr(tools.tasks, "sync_after_confirmation", synced["items"].append)
    monkeypatch.setattr(tools.tasks, "sync_decision_after_confirmation", synced["decisions"].append)
    return synced


def test_actions_are_not_offered_as_tools() -> None:
    """A model calls ``TOOLS``; an action runs only after a person approves,
    except a draft (``L1_ACTIONS``), which waits on the board instead."""
    assert not set(tools.ACTIONS) & set(tools.TOOLS)
    for action in tools.ACTIONS:
        level = "L1" if action in tools.L1_ACTIONS else "L2"
        assert level in (action.__doc__ or ""), action.__name__
        assert "delete" not in action.__name__, "L3 is forbidden"


def test_confirming_an_item_makes_it_a_todo_and_syncs_it(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_draft", status="needs_confirmation")

    result = tools.confirm_action_item(TEAM, "act_draft")

    assert result["ok"] is True
    assert session.get(ExtActionItem, "act_draft").status == "todo"  # type: ignore[union-attr]
    assert acting["items"] == ["act_draft"]
    assert session.query(ExtEditEvent).count() == 1, "counted as the board counts an edit"


def test_confirming_twice_is_refused(session: Session, acting: dict[str, list[str]]) -> None:
    item(session, "act_1", status="todo")
    assert tools.confirm_action_item(TEAM, "act_1")["ok"] is False
    assert acting["items"] == []


def test_reassigning_lands_only_on_a_team_member(
    session: Session, acting: dict[str, list[str]]
) -> None:
    member(session, "user_free", "최여유")
    item(session, "act_1")

    refused = tools.reassign_action_item(TEAM, "act_1", "user_gone")
    moved = tools.reassign_action_item(TEAM, "act_1", "user_free")

    assert refused["ok"] is False
    assert moved["ok"] is True
    assert session.get(ExtActionItem, "act_1").assignee_id == "user_free"  # type: ignore[union-attr]


def test_an_action_never_reaches_another_teams_item(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_theirs", meeting=OTHER_MEETING)

    for result in (
        tools.set_action_item_status(TEAM, "act_theirs", "done"),
        tools.set_action_item_due_date(TEAM, "act_theirs", "2026-10-02"),
        tools.reassign_action_item(TEAM, "act_theirs", "user_in"),
        tools.confirm_action_item(TEAM, "act_theirs"),
        tools.add_action_item(TEAM, OTHER_MEETING, "끼워넣기"),
    ):
        assert result["ok"] is False
    assert session.get(ExtActionItem, "act_theirs").status == "todo"  # type: ignore[union-attr]
    assert session.query(ExtActionItem).count() == 1
    assert acting["items"] == []


def test_a_due_date_arrives_as_text_and_can_be_cleared(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1", due=TODAY)

    assert tools.set_action_item_due_date(TEAM, "act_1", "2026-10-02")["ok"] is True
    assert session.get(ExtActionItem, "act_1").due_date == date(2026, 10, 2)  # type: ignore[union-attr]
    assert tools.set_action_item_due_date(TEAM, "act_1", "다음 주")["ok"] is False
    assert tools.set_action_item_due_date(TEAM, "act_1", None)["ok"] is True
    assert session.get(ExtActionItem, "act_1").due_date is None  # type: ignore[union-attr]


def test_a_status_outside_the_board_is_refused(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1")
    assert tools.set_action_item_status(TEAM, "act_1", "needs_confirmation")["ok"] is False
    assert tools.set_action_item_status(TEAM, "act_1", "done")["ok"] is True
    assert session.get(ExtActionItem, "act_1").status == "done"  # type: ignore[union-attr]


def test_a_followup_item_is_followups_fixed_text_and_waits(
    session: Session, acting: dict[str, list[str]]
) -> None:
    result = tools.add_followup_item(TEAM, MEETING)

    (row,) = session.query(ExtActionItem).all()
    assert result["ok"] is True and result["items"][0]["id"] == row.id
    assert (row.description, row.status, row.origin) == (
        tools.FOLLOWUP_DESCRIPTION,
        "needs_confirmation",
        "followup",
    )
    assert acting["items"] == []  # unconfirmed: nothing leaves
    # Not a person finding what the model missed: edit cost gets no "created".
    assert session.query(ExtEditEvent).count() == 0


def test_a_second_followup_item_is_refused_while_one_is_open(
    session: Session, acting: dict[str, list[str]]
) -> None:
    tools.add_followup_item(TEAM, MEETING)
    (row,) = session.query(ExtActionItem).all()
    row.description = "다음 주 화요일 후속 회의"  # a person rewords it
    row.status = "in_progress"
    session.flush()

    refused = tools.add_followup_item(TEAM, MEETING)

    assert refused["ok"] is False and "already has an open follow-up item" in refused["reason"]
    assert session.query(ExtActionItem).count() == 1

    row.status = "done"
    session.flush()
    assert tools.add_followup_item(TEAM, MEETING)["ok"] is True  # a closed one does not block


def test_a_followup_item_only_on_the_teams_own_meeting(
    session: Session, acting: dict[str, list[str]]
) -> None:
    result = tools.add_followup_item(TEAM, OTHER_MEETING)

    assert result["ok"] is False
    assert session.query(ExtActionItem).count() == 0


def test_the_open_followup_read_keys_on_origin_not_text(session: Session) -> None:
    assert tools.open_followup_item(session, TEAM)["items"] == []
    item(session, "act_lookalike", status="todo")  # a model item, whatever it says
    session.get(ExtActionItem, "act_lookalike").description = tools.FOLLOWUP_DESCRIPTION
    session.flush()
    assert tools.open_followup_item(session, TEAM)["items"] == []

    session.add(
        ExtActionItem(
            id="act_fu",
            meeting_id=MEETING,
            description="고쳐 쓴 문장",
            status="needs_confirmation",
            confidence=1.0,
            origin="followup",
        )
    )
    session.flush()

    result = tools.open_followup_item(session, TEAM)
    assert [(i["id"], i["body"]) for i in result["items"]] == [("act_fu", "needs_confirmation")]
    assert "고쳐 쓴" not in str(result)  # id and status only
    assert tools.open_followup_item(session, OTHER_TEAM)["items"] == []


def test_an_unknown_origin_is_refused_before_anything_is_written(session: Session) -> None:
    with pytest.raises(ValueError):
        service.create_action_item(
            session, ActionItemCreate(meeting_id=MEETING, description="x"), origin="agent"
        )
    assert session.query(ExtActionItem).count() == 0


def test_reviewing_a_decision(session: Session, acting: dict[str, list[str]]) -> None:
    decision(session, "dec_1", status=None)
    decision(session, "dec_2", status=None)

    assert tools.review_decision(TEAM, "dec_1", "confirmed")["ok"] is True
    assert tools.review_decision(TEAM, "dec_2", "rejected")["ok"] is True
    assert tools.review_decision(TEAM, "dec_2", "maybe")["ok"] is False

    assert acting["decisions"] == ["dec_1"]
    statuses = {r.decision_id: r.status for r in session.query(ExtDecisionReview)}
    assert statuses == {"dec_1": "confirmed", "dec_2": "rejected"}


def test_workload_rows_carry_their_counts_as_fields(session: Session) -> None:
    """The Workload subagent reads numbers, not the Korean body."""
    member(session, "user_free", "최여유")
    for n in range(3):
        item(session, f"act_{n}", due=TODAY - timedelta(days=1))

    rows_by_id = {r["id"]: r for r in tools.workload_by_owner(session, TEAM)["items"]}

    assert {k: rows_by_id["user_in"][k] for k in ("open", "overdue", "done", "state")} == {
        "open": 3,
        "overdue": 3,
        "done": 0,
        "state": "loaded",
    }
    assert rows_by_id["user_free"]["state"] == "free"


def test_item_rows_say_whether_they_are_late(session: Session) -> None:
    item(session, "act_late", due=TODAY - timedelta(days=1))

    (row,) = tools.person_action_items(session, TEAM, "user_in")["items"]

    assert (row["overdue"], row["needs_reassignment"]) == (True, False)


# --- a meeting past its retention window (#656) -------------------------------------

EXPIRED = "mtg_expired"


def expired_meeting(s: Session) -> None:
    """One of the team's own meetings, held last week, past a short retention
    window and still in the table: A's sweep has not taken it yet. It holds an
    overdue item of ``user_in`` and an open follow-up item."""
    now = datetime.now(UTC)
    s.add(
        Meeting(
            id=EXPIRED,
            team_id=TEAM,
            title="지난 회의",
            started_at=now - timedelta(days=7),
            expires_at=now - timedelta(days=1),
        )
    )
    s.flush()
    item(s, "act_old", due=TODAY - timedelta(days=1), meeting=EXPIRED)
    s.add(
        ExtActionItem(
            id="act_old_followup",
            meeting_id=EXPIRED,
            description="후속 회의 잡기",
            status="todo",
            confidence=1.0,
            origin="followup",
        )
    )
    s.flush()


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (tools.open_action_items, (TEAM,)),
        (tools.workload_by_owner, (TEAM,)),
        (tools.person_action_items, (TEAM, "user_in")),
        (tools.open_followup_item, (TEAM,)),
    ],
)
def test_a_team_read_does_not_see_a_meeting_past_retention(
    session: Session, tool: Any, args: tuple[str, ...]
) -> None:
    item(session, "act_live", due=TODAY + timedelta(days=1))
    before = tool(session, *args)

    expired_meeting(session)

    assert tool(session, *args) == before


@pytest.mark.parametrize(
    "tool",
    [
        tools.meeting_action_items,
        tools.unresolved_questions,
        tools.review_state,
        tools.meeting_decisions,
    ],
)
def test_a_meeting_past_retention_is_a_meeting_that_is_not_there(
    session: Session, tool: Any
) -> None:
    expired_meeting(session)

    result = tool(session, EXPIRED)

    assert (result["ok"], result["reason"], result["items"]) == (False, f"no meeting {EXPIRED}", [])


def test_an_item_of_a_meeting_past_retention_is_neither_found_nor_changed(
    session: Session, acting: dict[str, list[str]]
) -> None:
    expired_meeting(session)

    found = tools.action_item_status(session, TEAM, "act_old")
    changed = tools.set_action_item_status(TEAM, "act_old", "done")

    assert (found["ok"], changed["ok"]) == (False, False)
    row = session.get(ExtActionItem, "act_old")
    assert row is not None and row.status == "todo"
    assert acting["items"] == []
