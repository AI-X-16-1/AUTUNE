"""What the approver reads is built at read time from the owning store."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autune_agent.main.preview import GONE, preview
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
