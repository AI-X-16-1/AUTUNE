"""Module B's agent tools (#261): the ToolResult shape, the five-item cap, and
that unconfirmed content is counted rather than quoted (#261 rule 3).

SQLite in memory, the way ``test_read_endpoints`` builds B's tables.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_contracts.enums import ActionStatus
from autune_core import Base, Meeting, TeamMember, User, Utterance
from autune_extraction import days_off, service, tools
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
    ExtNotionTarget,
    ExtProject,
    ExtPublicHoliday,
    ExtSyncFailure,
)
from autune_extraction.schemas import ActionItemCreate, ActionItemUpdate
from autune_extraction.slots import KST

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
    # Every read of an item looks these up (#680): its failed copies, its event.
    ExtCalendarEvent.__table__,
    ExtSyncFailure.__table__,
    ExtNotionTarget.__table__,
    # ``open_item_owners`` checks a project is the team's.
    ExtProject.__table__,
    # ``public_holidays`` reads the calendar B keeps.
    ExtPublicHoliday.__table__,
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
    tools.stalled_action_items: (TEAM,),
    tools.workload_by_owner: (TEAM,),
    tools.person_action_items: (TEAM, "user_in"),
    tools.action_item_status: (TEAM, "act_missing"),
    tools.open_followup_item: (TEAM,),
    tools.open_item_owners: (TEAM,),
    tools.public_holidays: ("2026-10-01", "2026-10-31"),
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
    assert "확인 필요 1건" in result["summary"]


# --- meeting_due_dates (#966) ----------------------------------------------------------


def test_due_dates_of_an_unknown_meeting_are_ok_false_not_an_exception(session: Session) -> None:
    result = tools.meeting_due_dates(session, "mtg_nope")

    assert result["ok"] is False and "mtg_nope" in result["reason"]
    assert result["items"] == [] and result["evidence"] == []


def test_due_dates_are_the_confirmed_unfinished_items_dates_and_titles_and_nobody(
    session: Session,
) -> None:
    """One row, every date in it, earliest first, a shared day twice, each with
    its item's title (#1038) -- and no assignee, and nothing of an item that is
    undated or done."""
    item(session, "act_b", due=TODAY + timedelta(days=5))
    item(session, "act_a", due=TODAY + timedelta(days=2), status="in_progress")
    item(session, "act_c", due=TODAY + timedelta(days=5), assignee="user_gone")
    item(session, "act_late", due=TODAY - timedelta(days=3))
    item(session, "act_undated")
    item(session, "act_done", status="done", due=TODAY + timedelta(days=1))

    result = tools.meeting_due_dates(session, MEETING)

    assert result["ok"] is True
    (row,) = result["items"]
    assert row == {
        "title": "기한",
        "due_dates": [
            {
                "date": (TODAY - timedelta(days=3)).isoformat(),
                "confirmed": True,
                "title": "act_late 할 일",
            },
            {
                "date": (TODAY + timedelta(days=2)).isoformat(),
                "confirmed": True,
                "title": "act_a 할 일",
            },
            {
                "date": (TODAY + timedelta(days=5)).isoformat(),
                "confirmed": True,
                "title": "act_b 할 일",
            },
            {
                "date": (TODAY + timedelta(days=5)).isoformat(),
                "confirmed": True,
                "title": "act_c 할 일",
            },
        ],
        "dated_open": 4,
        "dated_confirmed": 4,
    }
    assert result["evidence"] == ["act_late", "act_a", "act_b", "act_c"]
    assert result["summary"] == (
        "확정된 열린 액션아이템 5건 중 기한 있음 4건, 기한 없음 1건. 확인 필요 0건."
    )
    said = str(result)
    assert "act_undated" not in said and "act_done" not in said, "only the dated open ones"
    assert "박지영" not in said and "이건우" not in said and "user_" not in said, (
        "and nobody's name"
    )


def test_a_typed_description_carrying_personal_data_gives_a_date_and_no_title(
    session: Session,
) -> None:
    """#1038, the first condition. A person's own item is confirmed as written
    and its text never passed module A's masker, so the title is screened: the
    entry stays, with its date, and the text does not come out. Written and
    reworded through the board's own writes, which is where such text comes
    from."""
    plain = service.create_action_item(
        session,
        ActionItemCreate(
            meeting_id=MEETING, description="API 연동 마무리", due_date=TODAY + timedelta(days=2)
        ),
    )
    phone = service.create_action_item(
        session,
        ActionItemCreate(
            meeting_id=MEETING,
            description="거래처 010-1234-5678 로 견적 요청",
            due_date=TODAY + timedelta(days=3),
        ),
    )
    session.flush()

    result = tools.meeting_due_dates(session, MEETING)

    (row,) = result["items"]
    assert row["due_dates"] == [
        {
            "date": (TODAY + timedelta(days=2)).isoformat(),
            "confirmed": True,
            "title": "API 연동 마무리",
        },
        {"date": (TODAY + timedelta(days=3)).isoformat(), "confirmed": True},
    ]
    assert "010-1234-5678" not in str(result) and "견적" not in str(result)
    # Held back, not dropped: the date is counted and the item can be pointed at.
    assert (row["dated_open"], row["dated_confirmed"]) == (2, 2)
    assert result["evidence"] == [plain.id, phone.id]

    # An edit can put personal data in, and a rewording can take it out.
    service.update_action_item(
        session, plain, ActionItemUpdate(description="담당 kim@example.com 에게 전달")
    )
    service.update_action_item(session, phone, ActionItemUpdate(description="거래처에 견적 요청"))
    session.flush()

    after = tools.meeting_due_dates(session, MEETING)

    assert [entry.get("title") for entry in after["items"][0]["due_dates"]] == [
        None,
        "거래처에 견적 요청",
    ]
    assert "kim@example.com" not in str(after)


def test_an_unconfirmed_items_date_never_comes_out_only_its_count(session: Session) -> None:
    """#261 rule 3, and the user on #966: confirmed items' dates only. The
    draft's date is the earliest here, so it would lead the list if it leaked;
    confirming it -- through the board's own write -- is what lets it out."""
    draft_day = TODAY + timedelta(days=1)
    item(session, "act_confirmed", due=TODAY + timedelta(days=4))
    item(session, "act_draft", status="needs_confirmation", due=draft_day)
    item(session, "act_draft_undated", status="needs_confirmation")

    result = tools.meeting_due_dates(session, MEETING)

    dates = [entry["date"] for entry in result["items"][0]["due_dates"]]
    assert dates == [(TODAY + timedelta(days=4)).isoformat()]
    assert draft_day.isoformat() not in str(result)
    # Nor its title (#1038): a confirmed item's only, and "act_draft" is in neither.
    assert [entry["title"] for entry in result["items"][0]["due_dates"]] == ["act_confirmed 할 일"]
    assert "act_draft" not in str(result)
    assert result["evidence"] == ["act_confirmed"], "no card can point at a draft"
    assert "확인 필요 2건" in result["summary"]
    assert all(entry["confirmed"] is True for entry in result["items"][0]["due_dates"])
    # The dated draft is in the count and nowhere else; the undated one in neither.
    assert (result["items"][0]["dated_open"], result["items"][0]["dated_confirmed"]) == (2, 1)

    row = session.get(ExtActionItem, "act_draft")
    assert row is not None
    service.update_action_item(session, row, ActionItemUpdate(status=ActionStatus.TODO))
    session.flush()

    after = tools.meeting_due_dates(session, MEETING)
    assert [entry["date"] for entry in after["items"][0]["due_dates"]] == [
        draft_day.isoformat(),
        (TODAY + timedelta(days=4)).isoformat(),
    ]
    assert after["evidence"] == ["act_draft", "act_confirmed"]
    assert [entry["title"] for entry in after["items"][0]["due_dates"]] == [
        "act_draft 할 일",
        "act_confirmed 할 일",
    ]
    assert "확인 필요 1건" in after["summary"]
    assert (after["items"][0]["dated_open"], after["items"][0]["dated_confirmed"]) == (2, 2)


def test_only_drafts_have_dates_means_a_row_of_counts_no_date_and_no_evidence(
    session: Session,
) -> None:
    """Right after a meeting nearly everything waits: the caller gets no date
    and falls back to its own rule, and a card can say "0/2 확정" (#966)."""
    draft_days = [TODAY + timedelta(days=1), TODAY + timedelta(days=6)]
    item(session, "act_draft", status="needs_confirmation", due=draft_days[0])
    item(session, "act_draft_two", status="needs_confirmation", due=draft_days[1])
    item(session, "act_open")

    result = tools.meeting_due_dates(session, MEETING)

    assert result["ok"] is True
    assert result["items"] == [
        {"title": "기한", "due_dates": [], "dated_open": 2, "dated_confirmed": 0}
    ]
    assert result["evidence"] == []
    assert result["summary"] == (
        "확정된 열린 액션아이템 1건 중 기한 있음 0건, 기한 없음 1건. 확인 필요 2건."
    )
    said = str(result)
    assert not any(day.isoformat() in said for day in draft_days), "a count, never a date"
    assert "act_draft" not in said, "and never which draft"


def test_no_unfinished_item_with_a_date_means_no_row(session: Session) -> None:
    """Nothing to count: a finished item's date and an undated draft are neither."""
    item(session, "act_open")
    item(session, "act_draft", status="needs_confirmation")
    item(session, "act_done", status="done", due=TODAY + timedelta(days=1))

    result = tools.meeting_due_dates(session, MEETING)

    assert result["ok"] is True
    assert result["items"] == [] and result["evidence"] == []


def test_every_date_is_in_the_one_row_past_the_five_item_cap(session: Session) -> None:
    """``_result`` keeps five rows; the rule this feeds needs all the dates."""
    for n in range(8):
        item(session, f"act_{n}", due=TODAY + timedelta(days=n))

    result = tools.meeting_due_dates(session, MEETING)

    assert len(result["items"]) == 1 and result["truncated"] is False
    assert len(result["items"][0]["due_dates"]) == 8
    assert len(result["evidence"]) == 8


def test_another_meetings_dates_are_not_this_meetings(session: Session) -> None:
    item(session, "act_here", due=TODAY + timedelta(days=2))
    item(session, "act_there", due=TODAY + timedelta(days=9), meeting=OTHER_MEETING)

    result = tools.meeting_due_dates(session, MEETING)

    assert result["evidence"] == ["act_here"]
    assert (TODAY + timedelta(days=9)).isoformat() not in str(result)


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


@pytest.mark.parametrize("count", [10**9, 10**30, 366, 1e300])
def test_a_day_count_too_large_is_pulled_into_range(session: Session, count: Any) -> None:
    """The ask loop lets a model write these arguments (#677). ``timedelta``
    cannot hold a billion days, and that exception was the tool's answer."""
    item(session, "act_far", due=TODAY + timedelta(days=300))

    result = tools.open_action_items(session, TEAM, within_days=count)
    load = tools.workload_by_owner(session, TEAM, days=count)

    assert result["ok"] is True and [i["id"] for i in result["items"]] == ["act_far"]
    assert "365일 안에 기한 1건" in result["summary"]
    assert load["ok"] is True


def test_a_day_count_too_small_is_pulled_into_range(session: Session) -> None:
    item(session, "act_today", due=TODAY)
    item(session, "act_tomorrow", due=TODAY + timedelta(days=1))

    result = tools.open_action_items(session, TEAM, within_days=-5)

    assert [i["id"] for i in result["items"]] == ["act_today"], "0 days: due today or earlier"
    assert tools.workload_by_owner(session, TEAM, days=0)["ok"] is True


def test_a_whole_number_written_as_a_float_is_that_number(session: Session) -> None:
    item(session, "act_soon", due=TODAY + timedelta(days=3))

    assert tools.open_action_items(session, TEAM, within_days=7.0) == tools.open_action_items(  # type: ignore[arg-type]
        session, TEAM, within_days=7
    )


@pytest.mark.parametrize("count", ["7", 7.5, None, True, float("nan"), float("inf"), [7]])
def test_a_value_that_is_not_a_whole_number_of_days_is_refused(
    session: Session, count: Any
) -> None:
    item(session, "act_soon", due=TODAY + timedelta(days=3))

    late = tools.open_action_items(session, TEAM, within_days=count)
    load = tools.workload_by_owner(session, TEAM, days=count)

    for result in (late, load):
        assert set(result) == KEYS
        assert (result["ok"], result["items"]) == (False, [])
    assert late["reason"] == "within_days is not a whole number of days"
    assert load["reason"] == "days is not a whole number of days"


def test_a_refused_day_count_is_not_repeated_in_the_result(session: Session) -> None:
    """The value is whatever the model wrote and can carry text from the
    person's question. ``reason`` goes back to the model and into logs, so it
    names the argument and nothing else (pr, before the PR was opened)."""
    typed = "010-1234-5678로 연락 주세요"

    for result in (
        tools.open_action_items(session, TEAM, within_days=typed),  # type: ignore[arg-type]
        tools.workload_by_owner(session, TEAM, days=typed),  # type: ignore[arg-type]
    ):
        assert result["ok"] is False
        assert "010" not in str(result) and "연락" not in str(result)


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

    assert "결정 확인 필요 1건" in result["summary"]
    assert "액션아이템 확인 필요 1건" in result["summary"]
    assert {i["id"] for i in result["items"]} == {"dec_1", "act_draft"}
    # The words the board and the decision list use for a row nobody confirmed.
    assert {i["id"]: i["title"] for i in result["items"]} == {
        "dec_1": "결정 확인 필요",
        "act_draft": "액션아이템 확인 필요",
    }
    assert all(i["body"] == "" for i in result["items"])
    assert result["evidence"] == ["utt_d"]


# --- public_holidays (#985, Follow-up's suggested date) -----------------------------


def test_public_holidays_are_one_row_of_iso_days_both_ends_included(session: Session) -> None:
    """The calendar B keeps answers while its read is fresh -- a day declared
    after the table in code was made is there, and the table is not mixed in."""
    declared = date(2026, 10, 8)
    days_off.store_public_holidays(
        session,
        {date(2026, 10, 5), declared, date(2026, 11, 19), date(2026, 11, 20)},
        now=datetime.now(UTC),
    )

    result = tools.public_holidays(session, "2026-10-05", "2026-11-19")

    assert set(result) == KEYS and result["ok"] is True
    assert result["items"] == [
        {"title": "공휴일", "days": ["2026-10-05", "2026-10-08", "2026-11-19"]}
    ]
    assert result["summary"] == "2026-10-05 ~ 2026-11-19 공휴일 3일."
    assert result["evidence"] == [] and result["truncated"] is False


def test_public_holidays_come_from_the_table_when_the_calendar_was_not_read_lately(
    session: Session,
) -> None:
    """Nothing read, or a read older than two weeks: the table in code, with
    its substitute day (개천절 fell on a Saturday in 2026)."""
    october = ["2026-10-03", "2026-10-05", "2026-10-09"]

    (row,) = tools.public_holidays(session, "2026-10-01", "2026-10-31")["items"]
    assert row["days"] == october

    old = datetime.now(UTC) - days_off.FRESH_FOR - timedelta(hours=1)
    days_off.store_public_holidays(session, {date(2026, 10, 8)}, now=old)

    (row,) = tools.public_holidays(session, "2026-10-01", "2026-10-31")["items"]
    assert row["days"] == october


def test_a_range_with_no_public_holiday_is_an_empty_list_and_not_a_refusal(
    session: Session,
) -> None:
    result = tools.public_holidays(session, "2026-11-02", "2026-11-06")

    assert result["ok"] is True
    assert result["items"] == [{"title": "공휴일", "days": []}]
    assert result["summary"] == "2026-11-02 ~ 2026-11-06 공휴일 0일."


def test_one_day_is_a_range_and_a_year_is_the_longest(session: Session) -> None:
    (one,) = tools.public_holidays(session, "2026-10-09", "2026-10-09")["items"]
    assert one["days"] == ["2026-10-09"]

    year = tools.public_holidays(session, "2026-01-01", "2026-12-31")
    assert year["ok"] is True
    (row,) = year["items"]
    assert len(row["days"]) > 5, "all of them in the one row: a row a day would stop at five"
    assert row["days"] == sorted(row["days"])

    leap = tools.public_holidays(session, "2028-01-01", "2028-12-31")
    assert leap["ok"] is True, "366 days, a leap year whole"
    assert tools.public_holidays(session, "2026-01-01", "2027-01-02")["ok"] is False


@pytest.mark.parametrize(
    ("start", "end", "reason"),
    [
        ("다음 주", "2026-10-31", "start is not a date"),
        ("2026-10-01", "10/31", "end is not a date"),
        ("2026-02-30", "2026-03-01", "start is not a date"),
        ("2026-10-31", "2026-10-01", "end is before start"),
        ("2026-01-01", "2028-01-01", "the range is longer than 366 days"),
        (None, "2026-10-31", "start is not a date"),
    ],
)
def test_a_range_that_cannot_be_answered_is_refused_without_repeating_it(
    session: Session, start: Any, end: Any, reason: str
) -> None:
    result = tools.public_holidays(session, start, end)

    assert set(result) == KEYS
    assert (result["ok"], result["reason"], result["items"]) == (False, reason, [])
    assert "다음 주" not in str(result) and "10/31" not in str(result), (
        "what was written is not echoed"
    )


def test_public_holidays_ask_about_no_team_and_no_person() -> None:
    """Dates of public record: the tool takes a range and nothing that names
    a team, a meeting or a person, so no scope is bound to it."""
    import inspect

    assert list(inspect.signature(tools.public_holidays).parameters) == ["session", "start", "end"]
    assert tools.public_holidays in tools.TOOLS and tools.public_holidays not in tools.ACTIONS


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
    assert "확인 필요 1건" in result["summary"]
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
    assert result["items"][0]["title"] == "확인 필요"
    assert result["summary"].startswith("확인이 필요한 액션아이템입니다.")


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
        tools.close_action_item(TEAM, "act_theirs"),
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


def test_a_write_that_names_the_items_meeting_runs_and_a_wrong_meeting_is_refused(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """Workload and Tracker name the item's meeting so that their proposal is
    filed under it (#959). The write checks it: an item of another meeting
    reads as missing, and nothing changes."""
    member(session, "user_free", "최여유")
    session.add(Meeting(id="mtg_2", team_id=TEAM, title="다른 회의"))
    item(session, "act_1", due=TODAY)

    wrong = (
        tools.set_action_item_due_date(TEAM, "act_1", "2026-10-20", meeting_id="mtg_2"),
        tools.reassign_action_item(TEAM, "act_1", "user_free", meeting_id="mtg_2"),
        tools.set_action_item_due_date(TEAM, "act_1", "2026-10-20", meeting_id=OTHER_MEETING),
    )
    row = session.get(ExtActionItem, "act_1")
    assert row is not None
    assert [r["ok"] for r in wrong] == [False, False, False]
    assert (row.due_date, row.assignee_id) == (TODAY, "user_in")
    assert acting["items"] == [] and session.query(ExtEditEvent).count() == 0

    assert tools.set_action_item_due_date(TEAM, "act_1", "2026-10-20", meeting_id=MEETING)["ok"]
    assert tools.reassign_action_item(TEAM, "act_1", "user_free", meeting_id=MEETING)["ok"]
    assert (row.due_date, row.assignee_id) == (date(2026, 10, 20), "user_free")
    assert acting["items"] == ["act_1", "act_1"]


def test_a_status_outside_the_board_is_refused(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1")
    assert tools.set_action_item_status(TEAM, "act_1", "needs_confirmation")["ok"] is False
    assert tools.set_action_item_status(TEAM, "act_1", "done")["ok"] is True
    assert session.get(ExtActionItem, "act_1").status == "done"  # type: ignore[union-attr]


# --- close_action_item: closed without being finished (#856) -------------------------


def test_closing_an_item_ends_it_and_keeps_it_apart_from_finished_work(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1", status="in_progress")
    item(session, "act_2")

    assert tools.close_action_item(TEAM, "act_1")["ok"] is True
    assert tools.set_action_item_status(TEAM, "act_2", "done")["ok"] is True

    assert session.get(ExtActionItem, "act_1").status == "done"  # type: ignore[union-attr]
    assert acting["items"] == ["act_1", "act_2"], "its copies outside follow, as for any change"
    assert service.closed_unfinished(session, ["act_1", "act_2"]) == {"act_1"}
    assert {i.id: i.closed_unfinished for i in service.list_action_items(session)} == {
        "act_1": True,
        "act_2": False,
    }
    closed, finished = (session.get(ExtActionItem, i) for i in ("act_1", "act_2"))
    assert closed is not None and finished is not None
    assert service.read_one(session, closed).closed_unfinished is True
    assert service.read_one(session, finished).closed_unfinished is False
    (entry,) = service.edit_history(session, "act_1")
    assert (entry.kind, entry.fields) == ("closed", []), "no field: it is not an edit of the status"


def test_a_close_is_not_a_correction_of_what_the_model_wrote(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1")

    assert tools.close_action_item(TEAM, "act_1")["ok"] is True

    cost = service.edit_cost_for_meeting(session, MEETING)
    assert (cost.model_items, cost.edited_items, cost.added_items, cost.edits) == (1, 0, 0, 0)


def test_only_a_confirmed_open_item_of_the_team_can_be_closed(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_draft", status="needs_confirmation")
    item(session, "act_done", status="done")
    item(session, "act_theirs", meeting=OTHER_MEETING)

    for ident in ("act_draft", "act_done", "act_theirs", "act_missing"):
        assert tools.close_action_item(TEAM, ident)["ok"] is False, ident

    assert {i.id: i.status for i in session.query(ExtActionItem)} == {
        "act_draft": "needs_confirmation",
        "act_done": "done",
        "act_theirs": "todo",
    }
    assert session.query(ExtEditEvent).count() == 0
    assert acting["items"] == []
    assert service.closed_unfinished(session, ["act_done"]) == set(), "finished, not closed"


def test_an_item_reopened_after_a_close_is_no_longer_marked_closed(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1")
    assert tools.close_action_item(TEAM, "act_1")["ok"] is True

    assert tools.set_action_item_status(TEAM, "act_1", "todo")["ok"] is True
    row = session.get(ExtActionItem, "act_1")
    assert row is not None
    assert service.read_one(session, row).closed_unfinished is False

    assert tools.set_action_item_status(TEAM, "act_1", "done")["ok"] is True
    assert service.read_one(session, row).closed_unfinished is False, "finished this time"
    assert [e.kind for e in service.edit_history(session, "act_1")] == [
        "closed",
        "edited",
        "edited",
    ]


def test_a_closed_item_is_reported_as_closed_and_a_finished_one_as_done(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """What Report and the chat print is this line: "done" on a closed item
    would have them say it was finished (review of #979)."""
    item(session, "act_closed")
    item(session, "act_finished")
    assert tools.close_action_item(TEAM, "act_closed")["ok"] is True
    assert tools.set_action_item_status(TEAM, "act_finished", "done")["ok"] is True

    listed = rows(tools.meeting_action_items(session, MEETING))
    (one,) = tools.action_item_status(session, TEAM, "act_closed")["items"]
    (other,) = tools.action_item_status(session, TEAM, "act_finished")["items"]

    assert listed["act_closed"].endswith(" · closed")
    assert listed["act_finished"].endswith(" · done")
    assert " · closed · " in one["body"] and " · done" not in one["body"]
    assert " · done · " in other["body"]


def test_closing_twice_says_it_is_closed_and_closing_a_finished_item_says_it_is_done(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_closed")
    item(session, "act_finished", status="done")
    assert tools.close_action_item(TEAM, "act_closed")["ok"] is True

    again = tools.close_action_item(TEAM, "act_closed")
    finished = tools.close_action_item(TEAM, "act_finished")

    assert (again["ok"], again["summary"]) == (False, "이미 닫힌 액션아이템입니다.")
    assert (finished["ok"], finished["summary"]) == (False, "이미 완료된 액션아이템입니다.")
    assert [e.kind for e in session.query(ExtEditEvent)] == ["closed"]
    assert acting["items"] == ["act_closed"]


def test_the_last_status_written_wins_whatever_time_its_row_carries(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """On PostgreSQL an event's time is when its transaction began. A board edit
    that began before a close and was written after it carries the earlier
    time, and it is still the last word (review of #979)."""
    item(session, "act_1")
    assert tools.close_action_item(TEAM, "act_1")["ok"] is True
    (close,) = session.query(ExtEditEvent).all()
    assert tools.set_action_item_status(TEAM, "act_1", "todo")["ok"] is True
    assert tools.set_action_item_status(TEAM, "act_1", "done")["ok"] is True
    for event in session.query(ExtEditEvent).filter(ExtEditEvent.id != close.id):
        event.created_at = close.created_at - timedelta(seconds=5)
    session.flush()

    assert service.closed_unfinished(session, ["act_1"]) == set(), "finished, written last"


def test_a_closed_item_is_not_counted_as_work_its_holder_finished(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1")
    item(session, "act_2")
    assert tools.set_action_item_status(TEAM, "act_1", "done")["ok"] is True
    assert tools.close_action_item(TEAM, "act_2")["ok"] is True

    load = rows(tools.workload_by_owner(session, TEAM))

    assert load["user_in"] == "진행 중 0 · 기한 지남 0 · 완료 1 · 여유"


def test_closing_clears_the_recheck_flag_as_any_change_a_person_makes(
    session: Session, acting: dict[str, list[str]]
) -> None:
    item(session, "act_1")
    row = session.get(ExtActionItem, "act_1")
    assert row is not None
    row.needs_recheck = True
    session.flush()

    assert tools.close_action_item(TEAM, "act_1")["ok"] is True

    assert row.needs_recheck is False


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
    assert result["summary"] == "후속 회의 항목을 추가했습니다 (확인 필요)."
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


# --- the day Follow-up recommends becomes the item's due date (#853) -------------------

KOREA_TODAY = datetime.now(tz=KST).date()


def test_a_recommended_date_becomes_the_items_due_date_and_nothing_else(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """The date the team lead saw on the card. No assignee is set and the item
    still waits, so nothing goes to a calendar and nobody is reminded."""
    day = KOREA_TODAY + timedelta(days=3)

    result = tools.add_followup_item(TEAM, MEETING, day.isoformat())

    (row,) = session.query(ExtActionItem).all()
    assert result["ok"] is True and result["items"][0]["id"] == row.id
    assert (row.due_date, row.assignee_id, row.status, row.origin) == (
        day,
        None,
        "needs_confirmation",
        "followup",
    )
    assert row.description == tools.FOLLOWUP_DESCRIPTION
    assert acting["items"] == []
    assert session.query(ExtEditEvent).count() == 0


@pytest.mark.parametrize("basis", ["confirmed", "draft", "cadence", None, "", "anything else"])
def test_what_followup_took_its_date_from_is_accepted_and_changes_nothing(
    session: Session, acting: dict[str, list[str]], basis: str | None
) -> None:
    """``basis`` is for the approval card (#963, #966). An approved proposal
    carries it, and the approval step refuses an argument the tool does not
    declare -- so B declares it, and does nothing with it: the same item, no
    value refused, nothing stored or sent."""
    day = KOREA_TODAY + timedelta(days=3)

    result = tools.add_followup_item(TEAM, MEETING, day.isoformat(), basis)

    (row,) = session.query(ExtActionItem).all()
    assert result["ok"] is True and result["items"][0]["id"] == row.id
    assert (row.description, row.due_date, row.assignee_id, row.status, row.origin) == (
        tools.FOLLOWUP_DESCRIPTION,
        day,
        None,
        "needs_confirmation",
        "followup",
    )
    if basis:
        assert basis not in str(result), "the answer does not carry it either"
        kept = [
            str(getattr(row, column.name))
            for column in ExtActionItem.__table__.columns
            if getattr(row, column.name) is not None
        ]
        assert not any(basis == value for value in kept), "and no column of the item holds it"
    assert acting["items"] == []
    assert session.query(ExtEditEvent).count() == 0


def test_the_followup_tool_declares_basis_as_an_optional_argument() -> None:
    """What the approval step binds against: declared, by that name, optional."""
    parameters = inspect.signature(tools.add_followup_item).parameters

    assert list(parameters) == ["team_id", "meeting_id", "due_date", "basis"]
    assert parameters["basis"].default is None


def test_a_date_of_today_is_still_a_date(session: Session, acting: dict[str, list[str]]) -> None:
    assert tools.add_followup_item(TEAM, MEETING, KOREA_TODAY.isoformat())["ok"] is True

    assert session.query(ExtActionItem).one().due_date == KOREA_TODAY


@pytest.mark.parametrize("text", ["2026-13-45", "2026-02-30", "내일", "next week", ""])
def test_text_that_is_not_a_date_is_refused_and_makes_no_item(
    session: Session, acting: dict[str, list[str]], text: str
) -> None:
    """``2026-13-45`` passes a ``YYYY-MM-DD`` pattern and is no day (#853)."""
    result = tools.add_followup_item(TEAM, MEETING, text)

    assert result["ok"] is False and "not a date" in result["reason"]
    assert session.query(ExtActionItem).count() == 0


def test_a_date_that_has_passed_is_left_off_and_the_item_is_still_made(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """The approval was for the item. A recommendation approved after its day
    must not fail it, and must not make an item that is born overdue."""
    yesterday = KOREA_TODAY - timedelta(days=1)

    result = tools.add_followup_item(TEAM, MEETING, yesterday.isoformat())

    (row,) = session.query(ExtActionItem).all()
    assert result["ok"] is True and result["items"][0]["id"] == row.id
    assert row.due_date is None
    assert result["summary"] == (
        "후속 회의 항목을 추가했습니다 (확인 필요). 추천 날짜가 이미 지나 기한은 넣지 않았습니다."
    )


def test_once_confirmed_it_is_an_ordinary_item_with_a_date(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """From here the date does what any item's date does: the copies that
    follow a confirmation (the board's sync, the assignee's calendar) are
    queued for it."""
    day = KOREA_TODAY + timedelta(days=3)
    tools.add_followup_item(TEAM, MEETING, day.isoformat())
    (row,) = session.query(ExtActionItem).all()

    assert tools.set_action_item_status(TEAM, row.id, "todo")["ok"] is True

    session.refresh(row)
    assert (row.status, row.due_date) == ("todo", day)
    assert acting["items"] == [row.id]


def test_a_second_followup_item_is_refused_whatever_date_it_brings(
    session: Session, acting: dict[str, list[str]]
) -> None:
    tools.add_followup_item(TEAM, MEETING)

    refused = tools.add_followup_item(TEAM, MEETING, (KOREA_TODAY + timedelta(days=3)).isoformat())

    assert refused["ok"] is False and "already has an open follow-up item" in refused["reason"]
    assert session.query(ExtActionItem).one().due_date is None


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


def test_rejecting_a_decision_that_has_a_page_queues_the_sync_that_retires_it(
    session: Session, acting: dict[str, list[str]]
) -> None:
    """#669: a verdict other than *confirmed* reaches Notion too while the
    decision still has the page its confirmation made."""
    decision(session, "dec_paged", status="confirmed")
    decision(session, "dec_plain", status="confirmed")
    session.add(
        ExtDecisionRef(
            decision_id="dec_paged", system="notion", meeting_id=MEETING, external_id="page_1"
        )
    )
    session.flush()

    assert tools.review_decision(TEAM, "dec_paged", "rejected")["ok"] is True
    assert tools.review_decision(TEAM, "dec_plain", "rejected")["ok"] is True

    assert acting["decisions"] == ["dec_paged"]


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


# --- stalled_action_items (#856: what a team should be asked to look at again) ---------

THEN = "mtg_then"


def held(s: Session, meeting_id: str, days_ago: int) -> None:
    s.add(
        Meeting(
            id=meeting_id,
            team_id=TEAM,
            title=f"{days_ago}일 전 회의",
            status="complete",
            started_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    s.flush()


def a_month_of_meetings(s: Session) -> None:
    """A meeting a month ago and three held since: an item made in the first
    and still open has been carried through ``STALE_AFTER`` meetings."""
    held(s, THEN, 30)
    for n, days_ago in enumerate((20, 10, 5)):
        held(s, f"mtg_later_{n}", days_ago)


def made(s: Session, item_id: str, days_ago: int) -> None:
    row = s.get(ExtActionItem, item_id)
    assert row is not None
    row.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    s.flush()


def stalled(result: dict[str, Any]) -> dict[str, list[str]]:
    return {i["id"]: i["stalled"] for i in result["items"]}


def test_work_that_stopped_moving_is_named_with_the_way_it_stopped(session: Session) -> None:
    a_month_of_meetings(session)
    item(session, "act_late", due=TODAY - timedelta(days=1))
    item(session, "act_carried", meeting=THEN)
    item(session, "act_both", due=TODAY - timedelta(days=9), meeting=THEN)
    item(session, "act_moving", due=TODAY + timedelta(days=2))
    item(session, "act_done_late", status="done", due=TODAY - timedelta(days=9), meeting=THEN)

    result = tools.stalled_action_items(session, TEAM)

    assert result["ok"] is True
    assert stalled(result) == {
        "act_both": ["overdue", "carried"],
        "act_late": ["overdue"],
        "act_carried": ["carried"],
    }, "both ways first, late before carried; moving and finished work is not here"
    assert [i["id"] for i in result["items"]] == ["act_both", "act_late", "act_carried"]
    (carried,) = [i for i in result["items"] if i["id"] == "act_carried"]
    assert carried["carried_meetings"] == service.STALE_AFTER
    assert "기한 지남 2건" in result["summary"]
    assert f"회의 {service.STALE_AFTER}번 이상 이월 2건" in result["summary"]


def test_an_item_carried_through_fewer_meetings_is_not_stalled(session: Session) -> None:
    held(session, THEN, 30)
    held(session, "mtg_later_0", 20)
    held(session, "mtg_later_1", 10)
    item(session, "act_recent", meeting=THEN)

    assert tools.stalled_action_items(session, TEAM)["items"] == []


def test_an_item_nobody_confirmed_is_given_by_id_and_never_quoted(session: Session) -> None:
    """#261 rule 3, kept here too: what a model drafted is not quoted before a
    person has confirmed it -- not its text, not who it named, not its date."""
    utterance(session, "utt_1", "제가 금요일까지 계약서 보낼게요", 0.0)
    item(
        session,
        "act_waiting",
        status="needs_confirmation",
        due=TODAY + timedelta(days=1),
        source="utt_1",
    )
    made(session, "act_waiting", 4)
    item(session, "act_fresh", status="needs_confirmation")
    made(session, "act_fresh", 2)

    result = tools.stalled_action_items(session, TEAM)

    assert result["items"] == [
        {
            "title": "액션아이템 확인 필요",
            "body": "4일째 확인 필요",
            "score": 0.5,
            "id": "act_waiting",
            "meeting_id": MEETING,
            "stalled": ["unconfirmed"],
            "waiting_days": 4,
        }
    ]
    assert "act_waiting 할 일" not in repr(result) and "박지영" not in repr(result)
    assert result["evidence"] == [], "nothing unconfirmed is sourced either"
    assert "3일 넘게 확인 필요 1건" in result["summary"]


def test_how_long_unconfirmed_counts_is_the_callers_to_say(session: Session) -> None:
    item(session, "act_fresh", status="needs_confirmation")
    made(session, "act_fresh", 2)

    assert stalled(tools.stalled_action_items(session, TEAM, unconfirmed_days=2)) == {
        "act_fresh": ["unconfirmed"]
    }
    assert tools.stalled_action_items(session, TEAM, unconfirmed_days=3)["items"] == []
    # Below a day is a day: "0 days" would call every draft stalled the moment it is made.
    made(session, "act_fresh", 0)
    assert tools.stalled_action_items(session, TEAM, unconfirmed_days=0)["items"] == []


@pytest.mark.parametrize("typed", ["three", 2.5, True, None, float("nan")])
def test_a_wait_that_is_not_a_day_count_is_refused(session: Session, typed: object) -> None:
    result = tools.stalled_action_items(session, TEAM, unconfirmed_days=typed)  # type: ignore[arg-type]

    assert result["ok"] is False
    assert result["reason"] == "unconfirmed_days is not a whole number of days"


def test_confirmed_work_comes_before_drafts_and_only_it_is_sourced(session: Session) -> None:
    utterance(session, "utt_1", "배포 일정 제가 확인하겠습니다", 0.0)
    item(session, "act_late", due=TODAY - timedelta(days=1), source="utt_1")
    item(session, "act_waiting", status="needs_confirmation")
    made(session, "act_waiting", 10)

    result = tools.stalled_action_items(session, TEAM)

    assert [i["id"] for i in result["items"]] == ["act_late", "act_waiting"]
    assert result["evidence"] == ["utt_1"]


def test_the_draft_that_has_waited_longest_comes_first(session: Session) -> None:
    for item_id, days in (("act_four", 4), ("act_ten", 10), ("act_six", 6)):
        item(session, item_id, status="needs_confirmation")
        made(session, item_id, days)

    result = tools.stalled_action_items(session, TEAM)

    assert [(i["id"], i["waiting_days"]) for i in result["items"]] == [
        ("act_ten", 10),
        ("act_six", 6),
        ("act_four", 4),
    ]


def test_it_is_a_tool_the_agent_layer_is_offered_and_not_a_write() -> None:
    assert tools.stalled_action_items in tools.TOOLS
    assert tools.stalled_action_items not in tools.ACTIONS


def test_five_are_listed_and_all_are_counted(session: Session) -> None:
    for n in range(7):
        item(session, f"act_late_{n}", due=TODAY - timedelta(days=n + 1))

    result = tools.stalled_action_items(session, TEAM)

    assert len(result["items"]) == tools.MAX_ITEMS
    assert "기한 지남 7건" in result["summary"]


def test_another_teams_stalled_work_is_not_this_teams(session: Session) -> None:
    item(session, "act_theirs", due=TODAY - timedelta(days=3), meeting=OTHER_MEETING)
    item(session, "act_theirs_draft", status="needs_confirmation", meeting=OTHER_MEETING)
    made(session, "act_theirs_draft", 30)

    assert tools.stalled_action_items(session, TEAM)["items"] == []
    assert stalled(tools.stalled_action_items(session, OTHER_TEAM)) == {
        "act_theirs": ["overdue"],
        "act_theirs_draft": ["unconfirmed"],
    }


def test_it_counts_items_and_never_people(session: Session) -> None:
    """Not a tally of who is behind: the summary has no name in it, and an
    item's line is the one every other read tool gives."""
    item(session, "act_late", due=TODAY - timedelta(days=1))

    result = tools.stalled_action_items(session, TEAM)

    assert "박지영" not in result["summary"]
    assert set(result["items"][0]) == {
        "title",
        "body",
        "score",
        "id",
        "meeting_id",
        "overdue",
        "needs_reassignment",
        "stalled",
        "carried_meetings",
    }


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
        (tools.stalled_action_items, (TEAM,)),
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


# --- open_item_owners (#756) ----------------------------------------------------------


def project(s: Session, project_id: str, team: str = TEAM) -> None:
    s.add(ExtProject(id=project_id, team_id=team, name=f"{project_id} 프로젝트"))
    s.flush()


def owned(
    s: Session, item_id: str, who: str | None, *, of: str | None = None, **more: object
) -> None:
    """A confirmed, open item, of a project when ``of`` names one."""
    item(s, item_id, assignee=who, **more)  # type: ignore[arg-type]
    if of is not None:
        row = s.get(ExtActionItem, item_id)
        assert row is not None
        row.project_id = of
        s.flush()


def teammate(s: Session, user_id: str, name: str) -> None:
    s.add(User(id=user_id, email=f"{user_id}@example.com", display_name=name))
    s.add(TeamMember(team_id=TEAM, user_id=user_id))
    s.flush()


def test_the_owners_of_a_projects_open_items_most_items_first(session: Session) -> None:
    teammate(session, "user_kim", "김하늘")
    project(session, "prj_pay")
    project(session, "prj_other")
    yesterday = date.today() - timedelta(days=1)
    owned(session, "act_1", "user_in", of="prj_pay", due=yesterday)
    owned(session, "act_2", "user_in", of="prj_pay", due=date.today() + timedelta(days=3))
    owned(session, "act_3", "user_kim", of="prj_pay", status="in_progress")
    owned(session, "act_4", "user_kim", of="prj_other")  # another project's
    owned(session, "act_5", "user_kim", of="prj_pay", status="done")  # not open
    owned(session, "act_6", "user_kim", of="prj_pay", status="needs_confirmation")

    answer = tools.open_item_owners(session, TEAM, project_id="prj_pay")

    assert answer["ok"] is True
    assert answer["summary"] == "진행 중인 확정 액션아이템의 담당자 2명, 담당 없는 항목 0건."
    assert [(row["id"], row["title"], row["open"], row["overdue"]) for row in answer["items"]] == [
        ("user_in", "박지영", 2, 1),
        ("user_kim", "김하늘", 1, 0),
    ]
    assert answer["items"][0]["nearest_due"] == yesterday.isoformat()
    assert answer["items"][1]["nearest_due"] is None


def test_what_a_meeting_left_open_and_nobody_is_guessed_for_an_item_without_an_owner(
    session: Session,
) -> None:
    owned(session, "act_1", "user_in")
    owned(session, "act_2", None)  # nobody's
    owned(session, "act_3", "user_gone")  # no longer on the team: nobody's here

    answer = tools.open_item_owners(session, TEAM, meeting_id=MEETING)

    assert answer["summary"] == "진행 중인 확정 액션아이템의 담당자 1명, 담당 없는 항목 2건."
    assert [(row["id"], row["title"], row["open"]) for row in answer["items"]] == [
        ("user_in", "박지영", 1),
        ("unowned", "담당 없음", 2),
    ]


def test_owners_cite_no_utterance_and_quote_no_item(session: Session) -> None:
    """A person is here for the work they hold. Nothing that was said, and not
    the items' own text."""
    utterance(session, "utt_1", "제가 금요일까지 하겠습니다", 1.0)
    owned(session, "act_1", "user_in", source="utt_1")

    answer = tools.open_item_owners(session, TEAM, meeting_id=MEETING)

    assert answer["evidence"] == []
    assert "act_1 할 일" not in repr(answer) and "금요일" not in repr(answer)


@pytest.mark.parametrize(
    "ids",
    [
        {},
        {"project_id": "prj_pay", "meeting_id": MEETING},
        {"meeting_id": OTHER_MEETING},
        {"meeting_id": "mtg_nope"},
        {"project_id": "prj_theirs"},
        {"project_id": "prj_nope"},
    ],
)
def test_owners_need_one_id_of_this_team(session: Session, ids: dict) -> None:
    project(session, "prj_pay")
    project(session, "prj_theirs", team=OTHER_TEAM)
    owned(session, "act_1", "user_in", of="prj_pay")

    answer = tools.open_item_owners(session, TEAM, **ids)

    assert answer["ok"] is False and answer["items"] == []


def test_an_item_of_another_teams_meeting_is_never_counted_whatever_project_it_names(
    session: Session,
) -> None:
    project(session, "prj_pay")
    owned(session, "act_1", "user_in", of="prj_pay")
    owned(session, "act_far", "user_in", of="prj_pay", meeting=OTHER_MEETING)

    answer = tools.open_item_owners(session, TEAM, project_id="prj_pay")

    assert [(row["id"], row["open"]) for row in answer["items"]] == [("user_in", 1)]
