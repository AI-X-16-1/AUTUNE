"""What the approver reads is built at read time from the owning store."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autune_agent.main.preview import (
    FOLLOWUP_GAPS_CLOSED,
    GONE,
    REPORT_ALREADY_POSTED,
    REPORT_ON_DASHBOARD,
    preview,
)
from autune_agent.main.registry import Tool
from autune_agent.models import AgentPendingAction, AgentResearchDocument
from autune_core import Meeting, User


def _row(team: dict[str, str], tool: str, arguments: dict[str, Any]) -> AgentPendingAction:
    return AgentPendingAction(
        team_id=team["team"],
        meeting_id=team["meeting"],
        subagent="x",
        tool=tool,
        kind="k",
        arguments=arguments,
        evidence=[],
        scope="any",
    )


def test_a_research_share_shows_the_document(session: Session, team: dict[str, str]) -> None:
    doc = AgentResearchDocument(
        team_id=team["team"], meeting_id=team["meeting"], body="## 제기된 질문"
    )
    session.add(doc)
    session.flush()

    shown = preview(
        session, _row(team, "agent.share_research_document", {"document_id": doc.id}), tools={}
    )

    assert shown == {"title": "리서치 문서 공유", "body": "## 제기된 질문"}


def test_a_missing_document_says_so(session: Session, team: dict[str, str]) -> None:
    shown = preview(
        session, _row(team, "agent.share_research_document", {"document_id": "rdoc_x"}), tools={}
    )

    assert shown["body"] == GONE


def test_a_document_of_another_meeting_is_not_shown(session: Session, team: dict[str, str]) -> None:
    other = Meeting(team_id=team["team"], title="다른 회의")
    session.add(other)
    session.flush()
    doc = AgentResearchDocument(team_id=team["team"], meeting_id=other.id, body="다른 회의 본문")
    session.add(doc)
    session.flush()

    shown = preview(
        session, _row(team, "agent.share_research_document", {"document_id": doc.id}), tools={}
    )

    assert shown["body"] == GONE


def test_a_reassignment_shows_the_item_and_the_new_assignee(
    session: Session, team: dict[str, str]
) -> None:
    status = Tool(
        name="extraction.action_item_status",
        description="Use this.",
        fn=lambda _s, team_id, action_item_id: {
            "ok": True,
            "summary": "1건",
            "items": [{"title": "API 문서", "body": "기한 10/9"}],
        },
    )
    new = session.get(User, team["member"])

    shown = preview(
        session,
        _row(
            team,
            "extraction.reassign_action_item",
            {"action_item_id": "act_1", "assignee_id": team["member"]},
        ),
        tools={"extraction.action_item_status": status},
    )

    assert shown["title"] == "할 일 재배정"
    assert "API 문서" in shown["body"] and new.display_name in shown["body"]


def _item_read(found: bool = True) -> tuple[Tool, list[dict[str, str]]]:
    """B's ``action_item_status``: one item, or its refusal for an id it does not know."""
    asked: list[dict[str, str]] = []

    def read(_s: Session, team_id: str, action_item_id: str) -> dict[str, Any]:
        asked.append({"team_id": team_id, "action_item_id": action_item_id})
        if not found:
            return {"ok": False, "reason": "no action item", "summary": "없습니다", "items": []}
        return {
            "ok": True,
            "summary": "1건",
            "items": [{"title": "API 문서", "body": "박지영 · 2026-10-01 · 기한 지남 · todo"}],
        }

    return Tool(name="extraction.action_item_status", description="Use this.", fn=read), asked


def _move(team: dict[str, str], due_date: str = "2026-10-14") -> AgentPendingAction:
    return _row(
        team,
        "extraction.set_action_item_due_date",
        {"action_item_id": "act_1", "due_date": due_date},
    )


def test_a_due_date_move_shows_the_item_as_it_stands_and_the_new_date(
    session: Session, team: dict[str, str]
) -> None:
    status, asked = _item_read()

    shown = preview(session, _move(team), tools={"extraction.action_item_status": status})

    assert shown == {
        "title": "기한 옮기기",
        "body": "API 문서 · 박지영 · 2026-10-01 · 기한 지남 · todo\n→ 새 기한: 10월 14일(수)",
    }
    assert asked == [{"team_id": team["team"], "action_item_id": "act_1"}], "the row's team"


def test_a_due_date_move_whose_item_is_gone_says_so(session: Session, team: dict[str, str]) -> None:
    status, _ = _item_read(found=False)

    assert preview(session, _move(team), tools={"extraction.action_item_status": status}) == {
        "title": "기한 옮기기",
        "body": GONE,
    }
    assert preview(session, _move(team), tools={})["body"] == GONE, "B's read is not loaded"


def test_a_due_date_move_never_shows_a_date_it_cannot_read(
    session: Session, team: dict[str, str]
) -> None:
    status, _ = _item_read()

    shown = preview(
        session, _move(team, "next_friday"), tools={"extraction.action_item_status": status}
    )

    assert shown["body"] == GONE, "not the value as it came"


def test_a_report_publish_points_at_the_dashboard_until_e_can_be_read(
    session: Session, team: dict[str, str]
) -> None:
    shown = preview(session, _row(team, "intelligence.publish_meeting_report", {}), tools={})

    assert shown == {"title": "리포트 게시", "body": REPORT_ON_DASHBOARD}


def _report_draft(
    *, current: str | None, posted: bool = False
) -> tuple[Tool, list[dict[str, Any]]]:
    """E's ``meeting_report_draft``: the stored draft when ``draft_id`` is ``current``."""
    asked: list[dict[str, Any]] = []

    def read(_s: Session, team_id: str, meeting_id: str, draft_id: str) -> dict[str, Any]:
        asked.append({"team_id": team_id, "meeting_id": meeting_id, "draft_id": draft_id})
        items = (
            [{"title": "주간 회의 · 10/2", "body": "## 결정\n· 출시일 확정", "posted": posted}]
            if draft_id == current
            else []
        )
        return {"ok": True, "summary": "초안", "items": items}

    return Tool(name="intelligence.meeting_report_draft", description="Use this.", fn=read), asked


def _publish(team: dict[str, str], draft_id: str = "rdr_1") -> AgentPendingAction:
    return _row(
        team,
        "intelligence.publish_meeting_report",
        {"meeting_id": team["meeting"], "draft_id": draft_id},
    )


def test_a_report_publish_shows_the_draft_it_would_post(
    session: Session, team: dict[str, str]
) -> None:
    tool, asked = _report_draft(current="rdr_1")

    shown = preview(session, _publish(team), tools={"intelligence.meeting_report_draft": tool})

    assert shown == {"title": "리포트 게시", "body": "주간 회의 · 10/2\n\n## 결정\n· 출시일 확정"}
    assert asked == [{"team_id": team["team"], "meeting_id": team["meeting"], "draft_id": "rdr_1"}]


def test_a_replaced_draft_reads_as_gone(session: Session, team: dict[str, str]) -> None:
    """A later run replaced the draft; approving this one would post nothing."""
    tool, _ = _report_draft(current="rdr_2")

    shown = preview(session, _publish(team), tools={"intelligence.meeting_report_draft": tool})

    assert shown["body"] == GONE


def test_a_report_already_posted_says_so(session: Session, team: dict[str, str]) -> None:
    tool, _ = _report_draft(current="rdr_1", posted=True)

    shown = preview(session, _publish(team), tools={"intelligence.meeting_report_draft": tool})

    assert shown["body"].startswith(REPORT_ALREADY_POSTED)


def test_a_publish_without_a_draft_id_is_gone(session: Session, team: dict[str, str]) -> None:
    tool, asked = _report_draft(current="rdr_1")
    row = _row(team, "intelligence.publish_meeting_report", {"meeting_id": team["meeting"]})

    shown = preview(session, row, tools={"intelligence.meeting_report_draft": tool})

    assert (shown["body"], asked) == (GONE, [])


def test_anything_else_shows_kind_and_ids(session: Session, team: dict[str, str]) -> None:
    shown = preview(session, _row(team, "gap.something", {"gap_id": "gap_1"}), tools={})

    assert shown["title"] == "k" and "gap_1" in shown["body"]


def test_an_assignee_outside_the_team_is_not_named(session: Session, team: dict[str, str]) -> None:
    status = Tool(
        name="extraction.action_item_status",
        description="Use this.",
        fn=lambda _s, team_id, action_item_id: {
            "ok": True,
            "summary": "1건",
            "items": [{"title": "API 문서", "body": "기한 10/9"}],
        },
    )

    shown = preview(
        session,
        _row(
            team,
            "extraction.reassign_action_item",
            {"action_item_id": "act_1", "assignee_id": team["outsider"]},
        ),
        tools={"extraction.action_item_status": status},
    )

    assert shown["title"] == "할 일 재배정"
    assert "알 수 없는 사람" in shown["body"]


def _gaps_by_id(*gaps: tuple[str, str], ok: bool = True) -> tuple[Tool, list[dict[str, Any]]]:
    """C's ``gaps_by_id``: of the cited ids, the open ones, most risky first.

    ``gaps`` is every open gap of the meeting in risk order; the fake keeps the
    cited ones, as C does, and records what it was asked.
    """
    asked: list[dict[str, Any]] = []

    def read(_s: Session, team_id: str, meeting_id: str, gap_ids: list[str]) -> dict[str, Any]:
        asked.append({"team_id": team_id, "meeting_id": meeting_id, "gap_ids": gap_ids})
        return {
            "ok": ok,
            "summary": "근거 갭",
            "items": [{"id": i, "title": t, "body": "질문?"} for i, t in gaps if i in set(gap_ids)],
        }

    return Tool(name="gap.gaps_by_id", description="Use this.", fn=read), asked


def _followup(
    team: dict[str, str],
    evidence: list[str],
    *,
    chat: bool = False,
    due_date: str | None = None,
    tool: str = "gap.schedule_followup_meeting",
) -> AgentPendingAction:
    """The two shapes Follow-up leaves (subagents/followup/graph.py).

    Woken by an event: the run's meeting is the row's, arguments are empty.
    Asked in chat: the run has no meeting, the proposal names it.
    """
    arguments: dict[str, Any] = {"meeting_id": team["meeting"]} if chat else {}
    if due_date is not None:
        arguments["due_date"] = due_date
    row = _row(team, tool, arguments)
    if chat:
        row.meeting_id = None
    row.evidence = evidence
    return row


def test_a_followup_shows_the_gaps_behind_it_most_risky_first(
    session: Session, team: dict[str, str]
) -> None:
    gaps, asked = _gaps_by_id(
        ("gap_a", "일정 · 출시일"), ("gap_b", "담당자 · 결제"), ("gap_c", "예산")
    )

    # Evidence lists carried-over gaps first; the preview orders them by risk.
    shown = preview(session, _followup(team, ["gap_c", "gap_a"]), tools={"gap.gaps_by_id": gaps})

    assert shown["title"] == "후속 회의 잡기"
    assert shown["body"] == "· 일정 · 출시일\n· 예산"
    assert asked == [
        {"team_id": team["team"], "meeting_id": team["meeting"], "gap_ids": ["gap_c", "gap_a"]}
    ]


def test_a_followup_queued_under_the_board_item_tool_keeps_its_card(
    session: Session, team: dict[str, str]
) -> None:
    """#1105: a row queued before #1107 still names B's ``add_followup_item``
    and is shown the same way until it is approved or retired."""
    gaps, _ = _gaps_by_id(("gap_a", "일정 · 출시일"))
    row = _followup(team, ["gap_a"], due_date="2026-10-15", tool="extraction.add_followup_item")

    shown = preview(session, row, tools={"gap.gaps_by_id": gaps})

    assert shown == {"title": "후속 회의 잡기", "body": "추천 날짜: 10월 15일(목)\n· 일정 · 출시일"}


def test_a_followup_asked_in_chat_reads_the_meeting_it_names(
    session: Session, team: dict[str, str]
) -> None:
    """#626 review: a chat run has no meeting, so the row's is NULL; the argument names it."""
    gaps, asked = _gaps_by_id(("gap_a", "일정 · 출시일"))

    shown = preview(session, _followup(team, ["gap_a"], chat=True), tools={"gap.gaps_by_id": gaps})

    assert shown["body"] == "· 일정 · 출시일"
    assert asked[0]["meeting_id"] == team["meeting"]


def test_a_followup_naming_no_meeting_at_all_is_gone(
    session: Session, team: dict[str, str]
) -> None:
    row = _followup(team, ["gap_a"])
    row.meeting_id = None

    shown = preview(session, row, tools={"gap.gaps_by_id": _gaps_by_id(("gap_a", "일정"))[0]})

    assert shown["body"] == GONE


def test_a_followup_whose_gaps_were_all_dismissed_says_so(
    session: Session, team: dict[str, str]
) -> None:
    gaps, asked = _gaps_by_id(("gap_b", "담당자 · 결제"))

    shown = preview(session, _followup(team, ["gap_a"]), tools={"gap.gaps_by_id": gaps})

    assert shown["body"] == FOLLOWUP_GAPS_CLOSED


def test_a_followup_without_c_falls_back_to_gone(session: Session, team: dict[str, str]) -> None:
    unread = preview(
        session,
        _followup(team, ["gap_a"]),
        tools={"gap.gaps_by_id": _gaps_by_id(("gap_a", "일정"), ok=False)[0]},
    )
    absent = preview(session, _followup(team, ["gap_a"]), tools={})

    assert unread["body"] == GONE
    assert absent["body"] == GONE


def test_a_followup_shows_its_suggested_date_above_the_gaps(
    session: Session, team: dict[str, str]
) -> None:
    """#854: the date Follow-up suggests (#852) becomes the item's due date on approval."""
    gaps, _ = _gaps_by_id(("gap_a", "리스크 — 논의되지 않았습니다"))

    shown = preview(
        session,
        _followup(team, ["gap_a"], due_date="2026-10-08"),
        tools={"gap.gaps_by_id": gaps},
    )

    assert shown["body"] == "추천 날짜: 10월 8일(목)\n· 리스크 — 논의되지 않았습니다"


def test_a_suggested_date_stays_when_the_gaps_have_closed(
    session: Session, team: dict[str, str]
) -> None:
    gaps, _ = _gaps_by_id(("gap_b", "담당자 · 결제"))

    shown = preview(
        session,
        _followup(team, ["gap_a"], due_date="2026-10-08"),
        tools={"gap.gaps_by_id": gaps},
    )

    assert shown["body"] == f"추천 날짜: 10월 8일(목)\n{FOLLOWUP_GAPS_CLOSED}"


def test_a_gone_followup_shows_no_date(session: Session, team: dict[str, str]) -> None:
    shown = preview(session, _followup(team, ["gap_a"], due_date="2026-10-08"), tools={})

    assert shown["body"] == GONE


def test_an_unreadable_date_is_left_off(session: Session, team: dict[str, str]) -> None:
    """A proposal from before #852, or a value that is not a date, shows the gaps alone."""
    gaps, _ = _gaps_by_id(("gap_a", "일정"))

    for bad in ("다음 주", "2026-13-01", ""):
        shown = preview(
            session, _followup(team, ["gap_a"], due_date=bad), tools={"gap.gaps_by_id": gaps}
        )
        assert shown["body"] == "· 일정"
