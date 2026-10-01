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
FOLLOWUP_GAPS_CLOSED = "근거가 된 갭이 모두 닫혔습니다"


def preview(
    session: Session, row: AgentPendingAction, *, tools: Mapping[str, Tool]
) -> dict[str, str]:
    args = row.arguments
    if row.tool == "agent.share_research_document":
        doc = session.get(AgentResearchDocument, args.get("document_id", ""))
        # The document must be this row's team's and this row's meeting's: an id
        # pointing elsewhere shows nothing rather than another meeting's text.
        ok = doc is not None and doc.team_id == row.team_id and doc.meeting_id == row.meeting_id
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
    if row.tool == "extraction.add_followup_item":
        return {"title": "후속 회의 잡기", "body": _followup_gaps(session, row, tools)}
    if row.tool == "intelligence.publish_meeting_report":
        return {"title": "리포트 게시", "body": "리포트 초안 — 회의 대시보드에서 보기"}
    ids = ", ".join(f"{k}={v}" for k, v in args.items())
    return {"title": row.kind, "body": ids}


def _followup_gaps(session: Session, row: AgentPendingAction, tools: Mapping[str, Tool]) -> str:
    """The titles of the gaps a Follow-up proposal rests on, most risky first (#562).

    Read from C's ``open_gaps`` now, so a gap dismissed since the proposal drops
    out. A title is a template item and a masked topic label; no utterance text.
    ``open_gaps`` returns its five riskiest gaps, so a cited gap ranked lower
    reads as closed -- a known limit while a meeting rarely has more.
    """
    read = tools.get("gap.open_gaps")
    if read is None or row.meeting_id is None:
        return GONE
    result = read(session, team_id=row.team_id, meeting_id=row.meeting_id)
    if not result.ok:
        return GONE
    cited = set(row.evidence)
    titles = [item.title for item in result.items if getattr(item, "id", None) in cited]
    return "\n".join(f"· {t}" for t in titles) if titles else FOLLOWUP_GAPS_CLOSED
