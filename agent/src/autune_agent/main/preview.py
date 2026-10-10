"""What an approver reads, built when the list is read (plan-mode spec section 6).

Nothing is stored for display: the preview comes from the owning store, so
when the source is deleted the preview goes with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from sqlalchemy.orm import Session

from autune_agent.models import AgentPendingAction, AgentResearchDocument
from autune_core import TeamMember, User

from .registry import Tool

GONE = "원본이 더 이상 없습니다"
FOLLOWUP_GAPS_CLOSED = "근거가 된 갭이 모두 닫혔습니다"
REPORT_ON_DASHBOARD = "리포트 초안 — 회의 대시보드에서 보기"
REPORT_ALREADY_POSTED = "이미 게시된 리포트입니다"

# Follow-up's proposal: C's calendar event since #1107, B's board item before
# it. Both take the same arguments, and rows queued under the old name stay
# until they are approved or retired (#1105).
FOLLOWUP_TOOLS = frozenset({"gap.schedule_followup_meeting", "extraction.add_followup_item"})


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
            return {"title": "할 일 재배정", "body": GONE}
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
        return {"title": "할 일 재배정", "body": f"{item.title} · {item.body}\n→ {to}"}
    if row.tool in FOLLOWUP_TOOLS:
        gaps = _followup_gaps(session, row, tools)
        when = _suggested_date(row.arguments.get("due_date"))
        body = f"추천 날짜: {when}\n{gaps}" if when and gaps != GONE else gaps
        return {"title": "후속 회의 잡기", "body": body}
    if row.tool == "intelligence.publish_meeting_report":
        return {"title": "리포트 게시", "body": _report_draft(session, row, tools)}
    if row.tool == "extraction.set_action_item_due_date":
        # What Tracker proposes (#856), and the card of anything else that
        # names this write: the item as it stands now, then the date an
        # approval would give it.
        when = _suggested_date(args.get("due_date"))
        line = _item_line(session, row, tools)
        if line is None or when is None:
            return {"title": "기한 옮기기", "body": GONE}
        return {"title": "기한 옮기기", "body": f"{line}\n→ 새 기한: {when}"}
    ids = ", ".join(f"{k}={v}" for k, v in args.items())
    return {"title": row.kind, "body": ids}


def _item_line(session: Session, row: AgentPendingAction, tools: Mapping[str, Tool]) -> str | None:
    """The item a proposal would change, as B's ``action_item_status`` gives it
    now: its text, who holds it, its date and status -- or ``None`` when it is
    gone or not this team's (B checks the team, as ``bind_scope`` does on
    approval).

    Read when the card is read, so the approver sees the item as it stands,
    not as it stood when the proposal was made. An item still waiting for
    confirmation comes back from B without its text (rule 3).
    """
    read = tools.get("extraction.action_item_status")
    if read is None:
        return None
    result = read(
        session, team_id=row.team_id, action_item_id=row.arguments.get("action_item_id", "")
    )
    if not result.ok or not result.items:
        return None
    item = result.items[0]
    return f"{item.title} · {item.body}"


def _followup_gaps(session: Session, row: AgentPendingAction, tools: Mapping[str, Tool]) -> str:
    """The titles of the gaps a Follow-up proposal rests on, most risky first (#562).

    Read now from C's ``gaps_by_id`` with the row's evidence, so a gap dismissed
    since the proposal drops out, and a cited gap is found wherever it ranks
    (``open_gaps`` stops at five; #644). A title is C's template item and a
    fixed phrase ("{item} — 논의되지 않았습니다"); no topic label, no utterance.

    The meeting is the row's when a pipeline event woke Follow-up, and the
    proposal's ``meeting_id`` argument when it was asked in chat, where the run
    is about no meeting (#626 review). C checks that meeting against the row's
    team, as ``bind_scope`` does when the proposal is approved.
    """
    read = tools.get("gap.gaps_by_id")
    meeting_id = row.meeting_id or row.arguments.get("meeting_id")
    if read is None or not isinstance(meeting_id, str):
        return GONE
    result = read(session, team_id=row.team_id, meeting_id=meeting_id, gap_ids=list(row.evidence))
    if not result.ok:
        return GONE
    titles = [item.title for item in result.items]
    return "\n".join(f"· {t}" for t in titles) if titles else FOLLOWUP_GAPS_CLOSED


def _suggested_date(value: object) -> str | None:
    """The date Follow-up suggests (#852), as "10월 8일(목)" (#854).

    On approval it becomes the item's due date, so the approver sees it first.
    A proposal from before #852 has none, and a value that is not an ISO date
    is left off rather than shown as it came.
    """
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return f"{day.month}월 {day.day}일({'월화수목금토일'[day.weekday()]})"


def _report_draft(session: Session, row: AgentPendingAction, tools: Mapping[str, Tool]) -> str:
    """The report text this approval would post (#571).

    Read from E's ``meeting_report_draft`` with the proposal's ``draft_id``: the
    publish posts only that draft, so the card shows exactly what goes out.
    A later run that replaced the draft makes this approval post nothing
    ("draft not current"), and the card says the original is gone rather than
    showing text that will not be posted. The stored text was masked before E
    saved it.

    Before E exposes the read, the card points at the dashboard as it did.
    """
    read = tools.get("intelligence.meeting_report_draft")
    if read is None:
        return REPORT_ON_DASHBOARD
    meeting_id = row.meeting_id or row.arguments.get("meeting_id")
    draft_id = row.arguments.get("draft_id")
    if not (isinstance(meeting_id, str) and isinstance(draft_id, str)):
        return GONE
    result = read(session, team_id=row.team_id, meeting_id=meeting_id, draft_id=draft_id)
    if not result.ok or not result.items:
        return GONE
    draft = result.items[0]
    text = f"{draft.title}\n\n{draft.body}"
    return f"{REPORT_ALREADY_POSTED}\n\n{text}" if getattr(draft, "posted", False) else text
