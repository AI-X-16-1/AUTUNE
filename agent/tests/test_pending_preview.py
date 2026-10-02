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


def _report_tool(seen: list[dict[str, Any]], *, draft: str | None, posted: bool = False) -> Tool:
    def fn(session: Session, **kw: Any) -> dict[str, Any]:
        seen.append(kw)
        if draft is None or kw["draft_id"] != draft:
            return {"ok": True, "summary": "없음", "items": []}
        item = {"title": "주간 회의 · 10/1", "body": "## 결정\n- 금요일 배포", "posted": posted}
        return {"ok": True, "summary": "초안", "items": [item]}

    return Tool(name="intelligence.meeting_report_draft", description="", fn=fn)


def test_a_report_publish_shows_the_draft_it_would_post(
    session: Session, team: dict[str, str]
) -> None:
    seen: list[dict[str, Any]] = []
    row = _row(team, "intelligence.publish_meeting_report", {"draft_id": "rdr_1"})

    shown = preview(
        session, row, tools={"intelligence.meeting_report_draft": _report_tool(seen, draft="rdr_1")}
    )

    assert shown["body"] == "주간 회의 · 10/1\n\n## 결정\n- 금요일 배포"
    assert shown["href"] == f"/dashboard#report-{team['meeting']}"
    assert seen == [{"team_id": team["team"], "meeting_id": team["meeting"], "draft_id": "rdr_1"}]


def test_a_replaced_report_draft_is_not_shown_under_the_old_approval(
    session: Session, team: dict[str, str]
) -> None:
    """#571: a later run or an edit replaced the draft; approving posts nothing."""
    row = _row(team, "intelligence.publish_meeting_report", {"draft_id": "rdr_old"})

    shown = preview(
        session, row, tools={"intelligence.meeting_report_draft": _report_tool([], draft="rdr_new")}
    )

    assert shown["body"] == GONE
    assert shown["href"] == f"/dashboard#report-{team['meeting']}"


def test_a_posted_report_says_so(session: Session, team: dict[str, str]) -> None:
    row = _row(team, "intelligence.publish_meeting_report", {"draft_id": "rdr_1"})
    tool = _report_tool([], draft="rdr_1", posted=True)

    shown = preview(session, row, tools={"intelligence.meeting_report_draft": tool})

    assert shown["body"].endswith("이미 게시된 리포트입니다.")


def _gaps_tool(items: list[dict[str, Any]], *, ok: bool = True) -> Tool:
    def fn(session: Session, **kw: Any) -> dict[str, Any]:
        return {"ok": ok, "summary": "갭", "items": items}

    return Tool(name="gap.open_gaps", description="", fn=fn)


GAPS = [
    {"id": "gap_a", "title": "로그 스키마 · 데이터 요건", "severity": "high", "score": 0.9},
    {"id": "gap_b", "title": "롤백 계획 · 배포", "severity": "medium", "score": 0.6},
    {"id": "gap_c", "title": "범위 밖 갭", "severity": "low", "score": 0.2},
]


def test_a_followup_shows_the_open_gaps_it_cites_riskiest_first(
    session: Session, team: dict[str, str]
) -> None:
    """#562: the lead sees why another meeting is proposed, not gap ids."""
    row = _row(team, "extraction.add_followup_item", {})
    row.evidence = ["gap_b", "gap_a"]

    shown = preview(session, row, tools={"gap.open_gaps": _gaps_tool(GAPS)})

    assert shown == {
        "title": "후속 회의 제안",
        "body": "열린 갭\n· 로그 스키마 · 데이터 요건 (높음)\n· 롤백 계획 · 배포 (보통)",
    }


def test_a_followup_whose_gaps_were_all_dismissed_says_so(
    session: Session, team: dict[str, str]
) -> None:
    row = _row(team, "extraction.add_followup_item", {})
    row.evidence = ["gap_gone"]

    shown = preview(session, row, tools={"gap.open_gaps": _gaps_tool(GAPS)})

    assert shown["body"] == "제안의 근거였던 갭이 모두 처리되었습니다."


def test_a_followup_whose_gaps_cannot_be_read_says_the_source_is_gone(
    session: Session, team: dict[str, str]
) -> None:
    row = _row(team, "extraction.add_followup_item", {})
    row.evidence = ["gap_a"]

    shown = preview(session, row, tools={"gap.open_gaps": _gaps_tool([], ok=False)})

    assert shown["body"] == GONE


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


def test_the_tools_the_previews_read_are_registered() -> None:
    """The fakes above stand in for these; a renamed tool would leave every
    card saying the source is gone without any test failing."""
    from autune_agent.main.registry import collect_tools

    tools = collect_tools()
    assert "intelligence.meeting_report_draft" in tools
    assert "gap.open_gaps" in tools
    assert {"team_id", "meeting_id", "draft_id"} <= tools[
        "intelligence.meeting_report_draft"
    ].parameters
    assert {"team_id", "meeting_id"} <= tools["gap.open_gaps"].parameters
