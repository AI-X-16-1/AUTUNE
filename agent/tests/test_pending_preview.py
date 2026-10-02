"""What the approver reads is built at read time from the owning store."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autune_agent.main.preview import FOLLOWUP_GAPS_CLOSED, GONE, preview
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

    assert shown["title"] == "액션아이템 재배정"
    assert "API 문서" in shown["body"] and new.display_name in shown["body"]


def test_a_report_publish_points_at_the_dashboard(session: Session, team: dict[str, str]) -> None:
    shown = preview(session, _row(team, "intelligence.publish_meeting_report", {}), tools={})

    assert shown == {"title": "리포트 게시", "body": "리포트 초안 — 회의 대시보드에서 보기"}


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

    assert shown["title"] == "액션아이템 재배정"
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
) -> AgentPendingAction:
    """The two shapes Follow-up leaves (subagents/followup/graph.py).

    Woken by an event: the run's meeting is the row's, arguments are empty.
    Asked in chat: the run has no meeting, the proposal names it.
    """
    row = _row(
        team, "extraction.add_followup_item", {"meeting_id": team["meeting"]} if chat else {}
    )
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
