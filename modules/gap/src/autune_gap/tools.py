"""Module C as tools an agent can call (agent-layer.md section 4).

Two reads for the Follow-up subagent
(``agent/docs/specs/2026-09-30-followup-subagent-design.md``). Each returns a
dict in the shape agent-layer.md calls ``ToolResult``::

    {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

**Plain functions, no ``autune_agent`` import**, the same shape as B's, A's and
E's ``tools.py``. ADR 0010 forbids a module importing the agent layer, so the
registry validates these dicts when it collects them.

What holds for both:

- **Topics, never people or roles.** Neither result carries participation, a
  participant id or a ``silent_share``. In a small team a role is a person, and
  whoever reads a Follow-up proposal is the team lead (agent-layer.md section
  3.1, privacy.md section 3). A gap's ``title`` is a template's item name
  plus a coverage phrase (``detect.MISSING_TITLE``, ``PARTIAL_TITLE``) and
  names no topic. Topic labels reach a caller only through ``body`` (the
  suggested question) and ``topics``, and a topic label is masked transcript
  text, so no raw utterance leaves here.
- **Undismissed gaps only.** A dismissal is a person saying the gap is wrong,
  and C's own report leaves those out too (``service.build_report``).
- ``evidence`` is gap ids. ``items`` holds at most five, most risky first, and
  ``truncated`` says when there were more.
- ``team_id`` comes from the run's scope (``RUN_SCOPE``), never from a model.
  The agent's toolbox also refuses a ``meeting_id`` from another team. The
  tools check it again, so another team's meeting reads as missing here too.
- Only reads, safe to call twice. C has no ``ACTIONS``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autune_core import Meeting

from .models import GapGap, GapRelatedTopic, GapTopic

MAX_ITEMS = 5
"""agent-layer.md section 4: a tool ranks and keeps five; the rest stay in C's tables."""


def _result(
    *,
    summary: str,
    items: list[dict[str, Any]],
    evidence: list[str],
    ok: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": reason,
        "summary": summary,
        "items": items[:MAX_ITEMS],
        "evidence": evidence,
        "confidence": 1.0 if ok else 0.0,
        "truncated": len(items) > MAX_ITEMS,
    }


def _missing(meeting_id: str) -> dict[str, Any]:
    return _result(
        ok=False,
        reason=f"meeting {meeting_id} not found",
        summary="그 회의를 찾을 수 없습니다.",
        items=[],
        evidence=[],
    )


def _meeting(session: Session, team_id: str, meeting_id: str) -> Meeting | None:
    meeting = session.get(Meeting, meeting_id)
    return meeting if meeting is not None and meeting.team_id == team_id else None


def _analysed(session: Session, meeting_id: str) -> bool:
    """Whether C built a topic graph for the meeting -- the same test the
    report and the rescore use (``service._analysed``)."""
    count = session.scalar(
        select(func.count()).select_from(GapTopic).where(GapTopic.meeting_id == meeting_id)
    )
    return (count or 0) > 0


def _open_gaps(session: Session, meeting_id: str) -> list[GapGap]:
    return list(
        session.scalars(
            select(GapGap)
            .where(GapGap.meeting_id == meeting_id, GapGap.dismissed_at.is_(None))
            .order_by(GapGap.risk_score.desc(), GapGap.id)
        )
    )


def _topic_labels(session: Session, gap_ids: list[str]) -> dict[str, list[str]]:
    labels: dict[str, list[str]] = defaultdict(list)
    if not gap_ids:
        return labels
    for gap_id, label in session.execute(
        select(GapRelatedTopic.gap_id, GapTopic.label)
        .join(GapTopic, GapTopic.id == GapRelatedTopic.topic_id)
        .where(GapRelatedTopic.gap_id.in_(gap_ids))
        .order_by(GapRelatedTopic.id)
    ).all():
        labels[gap_id].append(label)
    return labels


def open_gaps(session: Session, team_id: str, meeting_id: str) -> dict[str, Any]:
    """Use this to see what a meeting left open: the gaps C found that nobody
    dismissed, most risky first. Do not use it for whether the same thing was
    left open before -- ``recurring_open_gaps`` answers that.

    Returns at most five gaps. Each has its title, severity, template item key,
    suggested question and related topic labels. Nothing about who spoke.
    """
    meeting = _meeting(session, team_id, meeting_id)
    if meeting is None:
        return _missing(meeting_id)
    if not _analysed(session, meeting_id):
        return _result(
            ok=False,
            reason=f"meeting {meeting_id} has no gap analysis yet",
            summary="이 회의는 아직 갭 분석이 끝나지 않았습니다.",
            items=[],
            evidence=[],
        )

    gaps = _open_gaps(session, meeting_id)
    labels = _topic_labels(session, [gap.id for gap in gaps[:MAX_ITEMS]])
    high = sum(1 for gap in gaps if gap.severity == "high")
    summary = (
        f"열린 갭 {len(gaps)}건, 그중 높음 {high}건." if gaps else "이 회의에 열린 갭이 없습니다."
    )
    return _result(
        summary=summary,
        items=[
            {
                "id": gap.id,
                "title": gap.title,
                "body": gap.suggested_question or "",
                "score": gap.risk_score,
                "severity": gap.severity,
                "template_item_key": gap.template_item_key,
                "topics": labels.get(gap.id, []),
            }
            for gap in gaps
        ],
        evidence=[gap.id for gap in gaps[:MAX_ITEMS]],
    )


def _previous_analysed(session: Session, meeting: Meeting) -> Meeting | None:
    """The team's latest meeting before this one that C analysed. A meeting
    with no start time is placed by when its row was made."""
    when = func.coalesce(Meeting.started_at, Meeting.created_at)
    mine = meeting.started_at or meeting.created_at
    candidates = session.scalars(
        select(Meeting)
        .where(Meeting.team_id == meeting.team_id, Meeting.id != meeting.id, when < mine)
        .order_by(when.desc(), Meeting.id.desc())
    )
    for candidate in candidates:
        if _analysed(session, candidate.id):
            return candidate
    return None


def _item(gap: GapGap) -> tuple[str, str] | None:
    """A template item as ``uq_gap_gaps_template_item`` keys it within a meeting."""
    if gap.template_key is None or gap.template_item_key is None:
        return None
    return (gap.template_key, gap.template_item_key)


def recurring_open_gaps(session: Session, team_id: str, meeting_id: str) -> dict[str, Any]:
    """Use this to see whether a meeting left open what the team's previous
    meeting also left open: the template items with an undismissed gap in both.
    Do not use it for one meeting's gaps on their own -- ``open_gaps`` answers
    that.

    Returns at most five template items, most risky first. Each names the item
    and holds both meetings' gap ids. Nothing about who spoke.

    An item is the same item only under the same template: ``risk`` in
    ``general`` and ``risk`` in ``feature_planning`` are two checklists' items,
    so a template switch between the meetings carries nothing over. The
    previous meeting is the latest analysed one, even when it raised no gap at
    all -- a meeting in between that settled everything breaks the run, which
    is what it should do.
    """
    meeting = _meeting(session, team_id, meeting_id)
    if meeting is None:
        return _missing(meeting_id)
    if not _analysed(session, meeting_id):
        return _result(
            ok=False,
            reason=f"meeting {meeting_id} has no gap analysis yet",
            summary="이 회의는 아직 갭 분석이 끝나지 않았습니다.",
            items=[],
            evidence=[],
        )

    previous = _previous_analysed(session, meeting)
    if previous is None:
        return _result(
            summary="이 팀에서 이 회의 전에 분석된 회의가 없습니다.",
            items=[],
            evidence=[],
        )

    before = {_item(gap): gap for gap in _open_gaps(session, previous.id) if _item(gap) is not None}
    carried = [
        (gap, before[key])
        for gap in _open_gaps(session, meeting_id)
        if (key := _item(gap)) is not None and key in before
    ]
    summary = (
        f"직전 회의에 이어 이번에도 열린 항목 {len(carried)}개."
        if carried
        else "직전 회의에 이어 다시 열린 항목이 없습니다."
    )
    return _result(
        summary=summary,
        items=[
            {
                "id": now.id,
                "title": now.title,
                "score": now.risk_score,
                "severity": now.severity,
                "template_item_key": now.template_item_key,
                "previous_meeting_id": previous.id,
                "previous_gap_id": earlier.id,
            }
            for now, earlier in carried
        ],
        evidence=[gap.id for pair in carried[:MAX_ITEMS] for gap in pair],
    )


TOOLS = [open_gaps, recurring_open_gaps]

RUN_SCOPE = ("team_id",)
"""Parameters the agent fills from the run's authenticated scope, never from a model."""

PERSONAL_ONLY_TOOLS: list[Any] = []
"""C holds no figure about one person that belongs to that person alone."""
