"""What an approver reads, built when the list is read (plan-mode spec section 6).

Nothing is stored for display: the preview comes from the owning store, so
when the source is deleted the preview goes with it. A preview may carry
``href``, a page in the app where the source can be read in full.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from autune_agent.models import AgentPendingAction, AgentResearchDocument
from autune_core import TeamMember, User

from .registry import Tool

GONE = "원본이 더 이상 없습니다"


_SEVERITY = {"high": "높음", "medium": "보통", "low": "낮음"}


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
    if row.tool == "intelligence.publish_meeting_report":
        return _report(session, row, tools)
    if row.tool == "extraction.add_followup_item":
        return _followup(session, row, tools)
    ids = ", ".join(f"{k}={v}" for k, v in args.items())
    return {"title": row.kind, "body": ids}


def _report(session: Session, row: AgentPendingAction, tools: Mapping[str, Tool]) -> dict[str, str]:
    """The draft the approval would post -- that draft, by its ``draft_id``.

    E's ``meeting_report_draft`` answers nothing once a later run or a person's
    edit replaced the draft (#570, #642), and approving then posts nothing, so
    the card says the source is gone rather than showing the newer text under
    an approval that will not post it (#571). The link opens the meeting's
    report on the dashboard (#642).
    """
    href = f"/dashboard#report-{row.meeting_id}" if row.meeting_id else ""
    read = tools.get("intelligence.meeting_report_draft")
    item = None
    if read is not None and row.meeting_id:
        result = read(
            session,
            team_id=row.team_id,
            meeting_id=row.meeting_id,
            draft_id=row.arguments.get("draft_id", ""),
        )
        item = result.items[0] if result.ok and result.items else None
    if item is None:
        return {"title": "리포트 게시", "body": GONE, "href": href}
    posted = "\n\n이미 게시된 리포트입니다." if getattr(item, "posted", False) else ""
    return {"title": "리포트 게시", "body": f"{item.title}\n\n{item.body}{posted}", "href": href}


def _followup(
    session: Session, row: AgentPendingAction, tools: Mapping[str, Tool]
) -> dict[str, str]:
    """Why another meeting is proposed: the open gaps the proposal cites (#562).

    Read from C's ``gap.open_gaps`` now, filtered to the row's ``evidence``,
    in C's order (riskiest first). A title is a template item and a masked
    topic label, never utterance text. A gap dismissed since the proposal is
    no longer open and drops out; when every cited gap has, the card says so
    -- the proposal's reason is gone (#542).
    """
    read = tools.get("gap.open_gaps")
    title = "후속 회의 제안"
    if read is None or not row.meeting_id:
        return {"title": title, "body": GONE}
    result = read(session, team_id=row.team_id, meeting_id=row.meeting_id)
    if not result.ok:
        return {"title": title, "body": GONE}
    cited = set(row.evidence)
    gaps = [g for g in result.items if getattr(g, "id", None) in cited]
    if not gaps:
        return {"title": title, "body": "제안의 근거였던 갭이 모두 처리되었습니다."}
    lines = [f"· {g.title} ({_SEVERITY.get(getattr(g, 'severity', ''), '-')})" for g in gaps]
    return {"title": title, "body": "열린 갭\n" + "\n".join(lines)}
