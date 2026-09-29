"""Module B as tools an agent can call (#260/#261, docs/architecture/agent-layer.md section 4).

Five tools over B's existing reads -- no new query paths, no new tables, no
contract change. Each returns a dict in the shape agent-layer.md calls
``ToolResult``::

    {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

**Plain functions, no decorator, no ``autune_agent`` import.** ADR 0010's fourth
import-linter contract puts ``autune_agent`` above every module, so a module that
imported ``autune_agent.tools`` (as section 4's example does) would fail
``lint-imports``. Raised on #261 with this shape as the proposal: the agent wraps
``TOOLS`` when it collects them and validates the dict into its ``ToolResult``;
until then these are callable and tested on their own.

Rules from section 4 that are enforced here rather than trusted to the caller:

- ``items`` holds at most ``MAX_ITEMS``; ``truncated`` says when more existed.
- ``evidence`` holds utterance ids only, never text.
- An expected failure (unknown meeting) is ``ok=False`` with a reason, not an
  exception.
- Synchronous, safe to call twice: every tool only reads.

**Rule 3 of #261 -- B content that leaves goes through B's outbound read.** An
item's text appears in ``items`` only once a person has confirmed it (the same
gate as ``service.outbound_for_meeting``). What is still unconfirmed is counted,
not quoted. The one exception is ``unresolved_questions``, whose whole point is
utterances nobody has confirmed; its docstring says the agent may use them
internally (L0) and must not post them anywhere without a person's review.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus, UtteranceKind
from autune_core import Meeting, TeamMember, User, Utterance

from . import service
from .schemas import ActionItemRead

MAX_ITEMS = 5
"""agent-layer.md section 4: a tool ranks and keeps five; the rest stay in B's tables."""

_OPEN = (ActionStatus.TODO, ActionStatus.IN_PROGRESS)


def _result(
    *,
    summary: str,
    items: list[dict[str, Any]],
    evidence: list[str],
    confidence: float = 1.0,
    ok: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    kept = items[:MAX_ITEMS]
    return {
        "ok": ok,
        "reason": reason,
        "summary": summary,
        "items": kept,
        # Ids only, and only for what was kept, so evidence never outgrows items.
        "evidence": list(dict.fromkeys(evidence)),
        "confidence": confidence,
        "truncated": len(items) > MAX_ITEMS,
    }


def _missing(meeting_id: str) -> dict[str, Any]:
    return _result(
        ok=False,
        reason=f"no meeting {meeting_id}",
        summary="회의를 찾을 수 없습니다.",
        items=[],
        evidence=[],
        confidence=0.0,
    )


def _item_finding(item: ActionItemRead, today: date) -> dict[str, Any]:
    who = item.assignee_name or item.assignee_label or "담당 미지정"
    if item.needs_reassignment:
        who = "재배정 필요"
    due = item.due_date.isoformat() if item.due_date else "기한 없음"
    overdue = _overdue(item, today)
    return {
        "title": item.description,
        "body": f"{who} · {due}{' · 기한 지남' if overdue else ''} · {item.status}",
        "score": _urgency(item, today),
        "id": item.id,
        "meeting_id": item.meeting_id,
    }


def _overdue(item: ActionItemRead, today: date) -> bool:
    """Past its date and not done -- a finished item is never late."""
    return (
        item.status != ActionStatus.DONE.value
        and item.due_date is not None
        and item.due_date < today
    )


def _urgency(item: ActionItemRead, today: date) -> float:
    """Reassignment first, then overdue, then due soonest, undated after that,
    done last -- the order a person scanning a board would want the five in."""
    if item.status == ActionStatus.DONE.value:
        return 0.0
    if item.needs_reassignment:
        return 1.0
    if item.due_date is None:
        return 0.1
    days = (item.due_date - today).days
    if days < 0:
        return 0.9
    return max(0.2, 0.8 - 0.05 * days)


def meeting_action_items(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this right after a meeting is processed, or when asked what a meeting
    decided people should do. Do not use it for work across several meetings --
    that is ``open_action_items`` -- or for what still needs a person's review --
    that is ``review_state``.

    Returns the meeting's confirmed action items, most urgent first (at most
    five), and in ``summary`` how many are still waiting for confirmation.
    Unconfirmed items are counted, never quoted.
    """
    if session.get(Meeting, meeting_id) is None:
        return _missing(meeting_id)
    today = date.today()
    confirmed = service.outbound_for_meeting(session, meeting_id).action_items
    waiting = [
        i
        for i in service.list_action_items(session, meeting_id=meeting_id)
        if i.status == ActionStatus.NEEDS_CONFIRMATION.value
    ]
    ranked = sorted(confirmed, key=lambda i: _urgency(i, today), reverse=True)
    reassign = sum(i.needs_reassignment for i in confirmed)
    summary = f"확정된 액션아이템 {len(confirmed)}건, 확인 대기 {len(waiting)}건."
    if reassign:
        summary += f" 담당자가 팀에 없어 재배정이 필요한 항목 {reassign}건."
    return _result(
        summary=summary,
        items=[_item_finding(i, today) for i in ranked],
        evidence=[u for i in ranked[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


def open_action_items(session: Session, team_id: str, *, within_days: int = 7) -> dict[str, Any]:
    """Use this for a morning briefing or when asked what is late, due soon, or
    left without an owner across a team's meetings. Do not use it for one
    meeting's outcome -- that is ``meeting_action_items``.

    Returns the team's open confirmed items (to do or in progress) that are
    overdue, due within ``within_days``, or need reassigning -- most urgent first,
    at most five -- with counts of each group in ``summary``.
    """
    today = date.today()
    horizon = today + timedelta(days=within_days)
    meeting_ids = set(session.scalars(select(Meeting.id).where(Meeting.team_id == team_id)))
    if not meeting_ids:
        return _result(summary="이 팀의 회의가 없습니다.", items=[], evidence=[])
    open_items = [
        i
        for status in _OPEN
        for i in service.list_action_items(session, status=status)
        if i.meeting_id in meeting_ids
    ]
    due = [
        i
        for i in open_items
        if i.needs_reassignment or (i.due_date is not None and i.due_date <= horizon)
    ]
    ranked = sorted(due, key=lambda i: _urgency(i, today), reverse=True)
    overdue = sum(_overdue(i, today) for i in due)
    soon = sum(1 for i in due if i.due_date is not None and today <= i.due_date <= horizon)
    reassign = sum(i.needs_reassignment for i in due)
    return _result(
        summary=(
            f"진행 중인 액션아이템 {len(open_items)}건 중 기한 지남 {overdue}건, "
            f"{within_days}일 안에 기한 {soon}건, 재배정 필요 {reassign}건."
        ),
        items=[_item_finding(i, today) for i in ranked],
        evidence=[u for i in ranked[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


@dataclass(eq=False)
class _Load:
    """One person's confirmed work, counted -- or, with ``user_id`` ``None``, the
    open items nobody on the team holds."""

    user_id: str | None
    name: str
    open: int = 0
    overdue: int = 0
    done: int = 0

    @property
    def weight(self) -> int:
        """Open work, with a late item counted twice: late work is what makes a
        person the one to take something from."""
        return self.open + self.overdue


OVERLOADED_MIN_OPEN = 3
"""A person is "몰림" with at least this many open items *and* at least twice the
team's mean open count, or with two or more overdue. Routing hints for the
Workload subagent (#261 section 3.1), not a measured threshold: the subagent
reads the counts beside them and the manager approves any move."""


def workload_by_owner(session: Session, team_id: str, *, days: int = 30) -> dict[str, Any]:
    """Use this when deciding whether work should move between people -- who holds
    too much open work and who has finished theirs or holds none. Do not use it
    for which items are late or due soon -- that is ``open_action_items``.

    Returns one row per team member: how many confirmed items from the team's
    meetings in the last ``days`` days they hold open, how many of those are
    overdue, and how many they finished. The most loaded come first, then open
    items nobody on the team holds (one "담당 없음" row), then members with
    nothing open. Counts of work only, never speech (#261 section 3.1): nothing
    here says who spoke, how much, or what anyone said. Unconfirmed items are
    not counted -- nobody has agreed yet that they are anyone's work.
    """
    today = date.today()
    cutoff = datetime.now(UTC) - timedelta(days=days)
    meeting_ids = set(
        session.scalars(
            select(Meeting.id).where(
                Meeting.team_id == team_id,
                # An upload with no start time cannot be placed in the window;
                # leaving it out would hide its items from everyone.
                or_(Meeting.started_at.is_(None), Meeting.started_at >= cutoff),
            )
        )
    )
    members: dict[str, _Load] = {
        user_id: _Load(user_id=user_id, name=name)
        for user_id, name in session.execute(
            select(User.id, User.display_name)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
        )
    }
    unowned = _Load(user_id=None, name="담당 없음")
    for status in (*_OPEN, ActionStatus.DONE):
        for i in service.list_action_items(session, status=status):
            if i.meeting_id not in meeting_ids:
                continue
            # A non-member's id is cleared at read time (``needs_reassignment``),
            # so ``None`` here is exactly "nobody on the team holds it".
            load = members.get(i.assignee_id) if i.assignee_id else None
            if status == ActionStatus.DONE:
                if load is not None:
                    load.done += 1
                continue
            load = load or unowned
            load.open += 1
            load.overdue += _overdue(i, today)

    people = list(members.values())
    mean_open = sum(p.open for p in people) / len(people) if people else 0.0
    overloaded = sorted(
        (
            p
            for p in people
            if (p.open >= OVERLOADED_MIN_OPEN and p.open >= 2 * mean_open) or p.overdue >= 2
        ),
        key=lambda p: p.weight,
        reverse=True,
    )
    free = sorted((p for p in people if p.open == 0), key=lambda p: p.done, reverse=True)
    rest = sorted(
        (p for p in people if p not in overloaded and p not in free),
        key=lambda p: p.weight,
        reverse=True,
    )
    # Both ends inside the five, so a team with many loaded people still shows
    # someone who could take work.
    head = overloaded[:3] + ([unowned] if unowned.open else []) + free[:2]
    ordered = head + [p for p in (*overloaded[3:], *free[2:], *rest) if p not in head]
    return _result(
        summary=(
            f"최근 {days}일 회의의 확정 액션아이템 기준, 팀원 {len(people)}명 중 "
            f"몰림 {len(overloaded)}명, 진행 중 0건 {len(free)}명, "
            f"담당 없는 진행 중 항목 {unowned.open}건."
        ),
        items=[_load_finding(p, overloaded, free) for p in ordered],
        # Per-person counts cite no utterance: evidence is for what was said.
        evidence=[],
    )


def _load_finding(
    load: _Load, overloaded: Sequence[_Load], free: Sequence[_Load]
) -> dict[str, Any]:
    tag = " · 몰림" if load in overloaded else " · 여유" if load in free else ""
    body = f"진행 중 {load.open} · 기한 지남 {load.overdue}"
    if load.user_id is not None:
        body += f" · 완료 {load.done}"
    return {
        "title": load.name,
        "body": body + tag,
        "score": float(load.weight),
        "id": load.user_id or "unowned",
    }


def unresolved_questions(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this after a meeting to list what was asked or objected to and may
    need a follow-up -- "what should someone check or look into". Do not use it
    for commitments; those are ``meeting_action_items``.

    Returns open questions and concerns in spoken order (at most five) with the
    masked utterance text. **These are unreviewed model labels: use them inside
    the agent (L0) -- to draft a briefing a person reads first -- and never post
    them to a channel or tracker on your own** (#261 rule 3). Whether a question
    was answered later in the meeting is not known; say "raised", not "open".
    """
    if session.get(Meeting, meeting_id) is None:
        return _missing(meeting_id)
    wanted = {UtteranceKind.OPEN_QUESTION, UtteranceKind.CONCERN}
    rows = [c for c in service.classifications_for_meeting(session, meeting_id) if c.kind in wanted]
    texts: dict[str, str] = {
        uid: text
        for uid, text in session.execute(
            select(Utterance.id, Utterance.text).where(
                Utterance.id.in_([c.utterance_id for c in rows[:MAX_ITEMS]])
            )
        )
    }
    questions = sum(c.kind == UtteranceKind.OPEN_QUESTION for c in rows)
    items = [
        {
            "title": "질문" if c.kind == UtteranceKind.OPEN_QUESTION else "우려",
            "body": texts.get(c.utterance_id, ""),
            "score": c.confidence,
            "id": c.utterance_id,
        }
        for c in rows
    ]
    return _result(
        summary=f"회의에서 나온 질문 {questions}건, 우려 {len(rows) - questions}건 (검토 전).",
        items=items,
        evidence=[c.utterance_id for c in rows[:MAX_ITEMS]],
        confidence=0.5,
    )


def review_state(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this before reporting a meeting's outcome anywhere, or when asked what
    still needs a person. Anything counted here must not be presented as settled.

    Returns counts of decisions awaiting a verdict, action items awaiting
    confirmation and weak agreements whose speaker has not answered, and up to
    five of them by id and kind -- never their text.
    """
    if session.get(Meeting, meeting_id) is None:
        return _missing(meeting_id)
    review = service.review_for_meeting(session, meeting_id)
    pending_decisions = [d for d in review.decisions if d.status == "pending"]
    waiting_items = [
        i for i in review.action_items if i.status == ActionStatus.NEEDS_CONFIRMATION.value
    ]
    unanswered = [a for a in review.ambiguous_agreements if a.outcome in ("not_asked", "pending")]
    items: list[dict[str, Any]] = [
        {"title": "결정 확인 대기", "body": "", "score": 1.0, "id": d.id} for d in pending_decisions
    ]
    items += [
        {"title": "액션아이템 확인 대기", "body": "", "score": 0.8, "id": i.id}
        for i in waiting_items
    ]
    items += [
        {"title": "약한 동의, 답 없음", "body": "", "score": 0.5, "id": a.utterance_id}
        for a in unanswered
    ]
    return _result(
        summary=(
            f"결정 확인 대기 {len(pending_decisions)}건, 액션아이템 확인 대기 "
            f"{len(waiting_items)}건, 답 없는 약한 동의 {len(unanswered)}건."
        ),
        items=items,
        evidence=[u for d in pending_decisions[:MAX_ITEMS] for u in d.source_utterance_ids],
    )


TOOLS = [
    meeting_action_items,
    open_action_items,
    workload_by_owner,
    unresolved_questions,
    review_state,
]
"""Collected by the agent layer by iterating modules (invariant 6), never registered by hand."""
