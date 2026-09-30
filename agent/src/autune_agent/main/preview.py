"""What an approver reads, built when the list is read (plan-mode spec section 6).

Nothing is stored for display: the preview comes from the owning store, so
when the source is deleted the preview goes with it.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from autune_agent.models import AgentPendingAction, AgentResearchDocument
from autune_core import TeamMember, User

from .registry import Tool

GONE = "원본이 더 이상 없습니다"


def preview(
    session: Session, row: AgentPendingAction, *, tools: Mapping[str, Tool]
) -> dict[str, str]:
    args = row.arguments
    if row.tool == "agent.share_research_document":
        doc = session.get(AgentResearchDocument, args.get("document_id", ""))
        ok = doc is not None and doc.team_id == row.team_id
        return {"title": "리서치 문서 공유", "body": doc.body if ok and doc else GONE}
    if row.tool == "extraction.reassign_action_item":
        status = tools.get("extraction.action_item_status")
        item = None
        if status is not None:
            result = status(
                session, team_id=row.team_id, action_item_id=args.get("action_item_id", "")
            )
            item = result.items[0] if result.ok and result.items else None
        who = session.get(User, args.get("assignee_id", ""))
        if item is None:
            return {"title": "액션아이템 재배정", "body": GONE}
        # Check if the assignee is a member of the team
        to = "알 수 없는 사람"
        if who is not None:
            is_member = (
                session.query(TeamMember)
                .filter(TeamMember.team_id == row.team_id, TeamMember.user_id == who.id)
                .first()
                is not None
            )
            if is_member:
                to = who.display_name
        return {"title": "액션아이템 재배정", "body": f"{item.title} · {item.body}\n→ {to}"}
    if row.tool == "intelligence.publish_meeting_report":
        return {"title": "리포트 게시", "body": "리포트 초안 — 회의 대시보드에서 보기"}
    ids = ", ".join(f"{k}={v}" for k, v in args.items())
    return {"title": row.kind, "body": ids}
