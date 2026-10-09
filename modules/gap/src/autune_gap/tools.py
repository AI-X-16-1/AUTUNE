"""Module C as tools an agent can call (agent-layer.md section 4).

Two reads for the Follow-up subagent
(``agent/docs/specs/2026-09-30-followup-subagent-design.md``), two for the
approvals card of a Follow-up proposal -- what it cited (#644) and the days
people picked for the next meeting -- and one for what the team sent on to its
next meeting (#824). One write: the follow-up meeting a Follow-up proposal
asked for, once the team lead approves it. Each returns a dict in the shape
agent-layer.md calls ``ToolResult``::

    {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

**Plain functions, no ``autune_agent`` import**, the same shape as B's, A's and
E's ``tools.py``. ADR 0010 forbids a module importing the agent layer, so the
registry validates these dicts when it collects them.

What holds for the five reads:

- **Topics, never people or roles** -- with one exception, below. No result
  carries participation, a
  participant id or a ``silent_share``. In a small team a role is a person, and
  whoever reads a Follow-up proposal is the team lead (agent-layer.md section
  3.1, privacy.md section 3). A gap's ``title`` is a template's item name
  plus a coverage phrase (``detect.MISSING_TITLE``, ``PARTIAL_TITLE``) and
  names no topic. Topic labels reach a caller only through ``body`` (the
  suggested question) and ``topics``, and a topic label is masked transcript
  text, so no raw utterance leaves here.
  The exception is ``next_meeting_days``: it names who picked each day for the
  next meeting, by display name. That is an act a member took for the team,
  not anything they said or how they took part; a team with a Slack channel
  connected already sees the same name in C's notice (privacy.md). It is for
  the Follow-up approvals card only and is not offered to the chat model.
- **Undismissed gaps only.** A dismissal is a person saying the gap is wrong,
  and C's own report leaves those out too (``service.build_report``).
- ``evidence`` is gap ids. ``items`` holds at most five, most risky first, and
  ``truncated`` says when there were more.
- ``team_id`` comes from the run's scope (``RUN_SCOPE``), never from a model.
  The agent's toolbox also refuses a ``meeting_id`` from another team. The
  tools check it again, so another team's meeting reads as missing here too.
- Only reads, safe to call twice.

The write, ``schedule_followup_meeting``, is in ``ACTIONS`` and not in
``TOOLS``: no model is offered it, and the agent layer runs it only once an
approver approves the proposal, as that approver (``user_id``).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autune_core import Meeting, TeamMember, User

from . import followup_meeting, team_notice
from .models import GapAgendaEvent, GapGap, GapRelatedTopic, GapTopic

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


def _open_gaps(session: Session, meeting_id: str, gap_ids: list[str] | None = None) -> list[GapGap]:
    query = select(GapGap).where(GapGap.meeting_id == meeting_id, GapGap.dismissed_at.is_(None))
    if gap_ids is not None:
        query = query.where(GapGap.id.in_(gap_ids))
    return list(session.scalars(query.order_by(GapGap.risk_score.desc(), GapGap.id)))


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


def _gap_item(gap: GapGap, labels: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "id": gap.id,
        "title": gap.title,
        "body": gap.suggested_question or "",
        "score": gap.risk_score,
        "severity": gap.severity,
        "template_item_key": gap.template_item_key,
        "topics": labels.get(gap.id, []),
    }


def _not_analysed(meeting_id: str) -> dict[str, Any]:
    return _result(
        ok=False,
        reason=f"meeting {meeting_id} has no gap analysis yet",
        summary="이 회의는 아직 갭 분석이 끝나지 않았습니다.",
        items=[],
        evidence=[],
    )


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
        return _not_analysed(meeting_id)

    gaps = _open_gaps(session, meeting_id)
    labels = _topic_labels(session, [gap.id for gap in gaps[:MAX_ITEMS]])
    high = sum(1 for gap in gaps if gap.severity == "high")
    summary = (
        f"열린 갭 {len(gaps)}건, 그중 높음 {high}건." if gaps else "이 회의에 열린 갭이 없습니다."
    )
    return _result(
        summary=summary,
        items=[_gap_item(gap, labels) for gap in gaps],
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
        return _not_analysed(meeting_id)

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


def gaps_by_id(
    session: Session, team_id: str, meeting_id: str, gap_ids: list[str]
) -> dict[str, Any]:
    """Use this to check whether the gaps something cited are still open: of
    the given gap ids, the ones in this meeting that nobody has dismissed since,
    most risky first. Do not use it to find what a meeting left open --
    ``open_gaps`` answers that.

    Reads the first five distinct ``gap_ids``, the most a proposal cites, and
    returns the open ones in the same shape as ``open_gaps``. An id that is
    dismissed, unknown or from another meeting is left out, so a cited gap
    missing from the result is closed. Nothing about who spoke.
    """
    meeting = _meeting(session, team_id, meeting_id)
    if meeting is None:
        return _missing(meeting_id)
    if not _analysed(session, meeting_id):
        return _not_analysed(meeting_id)

    asked = list(dict.fromkeys(gap_ids))[:MAX_ITEMS]
    gaps = _open_gaps(session, meeting_id, asked) if asked else []
    labels = _topic_labels(session, [gap.id for gap in gaps])
    # Count only this meeting's ids, so an id from elsewhere does not read as
    # a cited gap that has since closed.
    cited = session.scalar(
        select(func.count())
        .select_from(GapGap)
        .where(GapGap.meeting_id == meeting_id, GapGap.id.in_(asked))
    )
    summary = (
        f"근거 갭 {cited}건 중 {len(gaps)}건이 아직 열려 있습니다."
        if gaps
        else "근거 갭 중 열린 것이 없습니다."
    )
    return _result(
        summary=summary,
        items=[_gap_item(gap, labels) for gap in gaps],
        evidence=[gap.id for gap in gaps],
    )


def carried_gaps(session: Session, team_id: str) -> dict[str, Any]:
    """Use this to find what the team sent on to its next meeting: gaps
    somebody marked "다음 회의 어젠다로" on a meeting's gap report and nobody
    has dismissed since, most recently sent first. Do not use it for what one
    meeting left open -- ``open_gaps`` answers that.

    Each item names the meeting it came from (``meeting_id``). A mark stays
    until somebody takes it back or dismisses the gap, so a gap the next
    meeting settled is still listed until then; the reader says "sent on", not
    "still open". Nothing about who spoke or who pressed the button (#824).
    """
    gaps = list(
        session.scalars(
            select(GapGap)
            .join(Meeting, Meeting.id == GapGap.meeting_id)
            .where(
                Meeting.team_id == team_id,
                GapGap.carried_at.is_not(None),
                GapGap.dismissed_at.is_(None),
            )
            .order_by(GapGap.carried_at.desc(), GapGap.id)
        )
    )
    shown = gaps[:MAX_ITEMS]
    labels = _topic_labels(session, [gap.id for gap in shown])
    return _result(
        summary=(
            f"다음 회의로 넘긴 갭이 {len(gaps)}건 있습니다."
            if gaps
            else "다음 회의로 넘긴 갭이 없습니다."
        ),
        items=[{**_gap_item(gap, labels), "meeting_id": gap.meeting_id} for gap in gaps],
        evidence=[gap.id for gap in shown],
    )


def next_meeting_days(session: Session, team_id: str, meeting_id: str) -> dict[str, Any]:
    """Use this when a follow-up meeting's day is being chosen, for the
    approvals card only; not offered to the chat model. The days people
    picked for the next meeting with "다음 회의 잡기" on this meeting's gap
    report -- the start day of each calendar event a gap's line went onto.
    Do not use it to learn which gaps were sent on: that is ``carried_gaps``.

    Returns one row, ``다음 회의 날짜``, whose ``days`` lists each such day
    once, earliest first, from today on in Korea, as ``{"day": ISO,
    "picked_by": [display name, ...]}``; there is no row when there is none.
    Two people who picked the same day are one day with both names; two who
    picked different days are two, for the person deciding to choose between.

    ``picked_by`` names who pressed, so the approver knows whose day it is:
    an act they took for the team, and the name C's team Slack notice posts
    for the same press where a channel is connected. Only
    members still on the meeting's team are named, and a day nobody on it
    picked is left out. Nothing else of the event or the calendar: no user id,
    no calendar or event id, no title.

    A day is the event's as it was when the line was written: an event moved
    since keeps its old day until somebody presses again.
    """
    meeting = _meeting(session, team_id, meeting_id)
    if meeting is None:
        return _missing(meeting_id)
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    rows = session.execute(
        select(GapAgendaEvent.event_day, User.display_name)
        .join(User, User.id == GapAgendaEvent.user_id)
        .join(
            TeamMember,
            (TeamMember.user_id == GapAgendaEvent.user_id)
            & (TeamMember.team_id == meeting.team_id),
        )
        .where(
            GapAgendaEvent.meeting_id == meeting_id,
            GapAgendaEvent.event_day.is_not(None),
            GapAgendaEvent.event_day >= today,
        )
        .distinct()
    )
    by_day: dict[str, set[str]] = defaultdict(set)
    for day, name in rows:
        if day is not None:
            by_day[day.isoformat()].add(name)
    days = sorted(by_day)
    items = (
        [
            {
                "title": "다음 회의 날짜",
                "days": [{"day": d, "picked_by": sorted(by_day[d])} for d in days],
            }
        ]
        if days
        else []
    )
    return _result(
        summary=(
            f"다음 회의 잡기로 정한 날짜가 {len(days)}개 있습니다."
            if days
            else "다음 회의 잡기로 정한 날짜가 없습니다."
        ),
        items=items,
        evidence=[],
    )


FOLLOWUP_SAID = {
    "past_day": ("the day has passed", "승인한 날짜가 이미 지났습니다."),
    "already_scheduled": (
        "the meeting already has its follow-up event",
        "이 회의의 후속 회의 일정은 이미 잡혀 있습니다.",
    ),
    "not_connected": (
        "the approver has no calendar connected",
        "승인한 사람의 Google 캘린더가 연결되어 있지 않아 일정을 만들지 못했습니다.",
    ),
    "reconnect_required": (
        "the approver's calendar must be connected again",
        "Google 캘린더 연결이 끊겨 일정을 만들지 못했습니다. 다시 연결해 주세요.",
    ),
    "failed": ("the calendar did not take the event", "캘린더에 일정을 만들지 못했습니다."),
}

SLACK_SAID = {
    "posted": " 팀 슬랙 채널에 알렸습니다.",
    "no_slack": " 팀에 슬랙 채널이 연결되어 있지 않아 알리지 않았습니다.",
    "failed": " 팀 슬랙 채널에는 알리지 못했습니다.",
    "refused": " 팀 슬랙 채널에는 알리지 못했습니다.",
    "not_tried": "",
}


def schedule_followup_meeting(
    session: Session,
    team_id: str,
    meeting_id: str,
    due_date: str,
    user_id: str,
    basis: str | None = None,
) -> dict[str, Any]:
    """Put the follow-up meeting a Follow-up proposal asked for on the
    approver's own Google Calendar, invite the meeting's team members who took
    part, and tell the team's Slack channel (``followup_meeting``).

    ``due_date`` (``YYYY-MM-DD``) is the day on the approved card -- the name
    module B's ``add_followup_item`` gave it, which the card reads. The event
    starts then at the meeting's clock time in Korea. Its description lists
    the meeting's open gaps, and the channel's notice says when and what.
    ``basis`` is what Follow-up took the day from, for the card; it is
    accepted and nothing else, as B's write accepts it.

    L2 -- runs only after a person (the team lead, for Follow-up) approves,
    and ``user_id`` is that approver: the agent layer fills it, never a model.
    One event per meeting: a second approval makes nothing and says so. A day
    already past, a calendar not connected or one Google refuses makes
    nothing either, so a later proposal for the meeting can still make it.
    """
    try:
        wanted = date.fromisoformat(due_date)
    except ValueError:
        return _result(
            ok=False,
            reason=f"not a date: {due_date!r}",
            summary="날짜 형식이 아닙니다 (YYYY-MM-DD).",
            items=[],
            evidence=[],
        )
    meeting = _meeting(session, team_id, meeting_id)
    if meeting is None:
        return _missing(meeting_id)
    approver = session.get(User, user_id)
    on_team = session.scalar(
        select(func.count())
        .select_from(TeamMember)
        .where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if approver is None or not on_team:
        return _result(
            ok=False,
            reason=f"{user_id} is not on team {team_id}",
            summary="이 팀의 팀원만 후속 회의를 잡을 수 있습니다.",
            items=[],
            evidence=[],
        )
    done = followup_meeting.schedule(session, meeting, approver, wanted)
    if done.outcome != "scheduled" or done.starts is None:
        reason, summary = FOLLOWUP_SAID[done.outcome]
        return _result(ok=False, reason=reason, summary=summary, items=[], evidence=[])
    agenda = f", 안건 {done.gaps}건" if done.gaps else ""
    return _result(
        summary=(
            f"후속 회의를 {team_notice.when(done.starts)}에 캘린더에 잡고 "
            f"{done.invited}명을 초대했습니다{agenda}.{SLACK_SAID[done.slack]}"
        ),
        items=[],
        evidence=[meeting_id],
    )


TOOLS = [open_gaps, recurring_open_gaps, gaps_by_id, carried_gaps, next_meeting_days]

ACTIONS = [schedule_followup_meeting]
"""C's one write, L2: it invites people and posts to the team channel, so it
waits for a person. Kept out of ``TOOLS``: the registry offers ``TOOLS`` to
models, and the action executor alone runs this."""

L1_ACTIONS: list[Any] = []

RUN_SCOPE = ("team_id",)
"""Parameters the agent fills from the run's authenticated scope, never from a model."""

PERSONAL_ONLY_TOOLS: list[Any] = []
"""C holds no figure about one person that belongs to that person alone."""
