"""Module B as tools an agent can call (#260/#261, docs/architecture/agent-layer.md section 4).

Eight read tools (``TOOLS``) over B's existing reads, and six writes
(``ACTIONS``) over B's existing service calls, so that everything a person does
with B on the board -- read items and decisions, confirm, reassign, re-date,
close, add, review a decision -- can also be asked for in words. No new tables,
no contract change. Each returns a dict in the shape agent-layer.md calls
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
- Synchronous, safe to call twice: every tool only reads. The writes are
  ``ACTIONS``, never in ``TOOLS``: level L2, run by the main agent only after a
  person approves (section 8), each through the same service call and checks
  the board uses. ``RUN_SCOPE`` names the arguments the run fills, not the model.

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
from autune_core import Meeting, TeamMember, User, Utterance, session_scope

from . import service, tasks
from .models import ExtActionItem, ExtDecision
from .schemas import ActionItemCreate, ActionItemRead, ActionItemUpdate, DecisionReviewUpdate

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


def meeting_decisions(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this when asked what a meeting decided, or before writing a meeting's
    outcome anywhere. Do not use it for how a decision changed across meetings --
    that is D's decision thread -- or for who should do what -- that is
    ``meeting_action_items``.

    Returns the decisions a person confirmed, in their wording (at most five),
    and in ``summary`` how many still wait for a verdict. A decision nobody
    confirmed, or one held back because its text carries personal data, is
    counted and never quoted (#261 rule 3).
    """
    if session.get(Meeting, meeting_id) is None:
        return _missing(meeting_id)
    review = service.review_for_meeting(session, meeting_id)
    outbound = service.outbound_for_meeting(session, meeting_id)
    sources = {d.id: d.source_utterance_ids for d in review.decisions}
    pending = sum(d.status == "pending" for d in review.decisions)
    held = sum(b.kind == "decision" for b in outbound.blocked)
    summary = f"확정된 결정 {len(outbound.decisions)}건, 확인 대기 {pending}건."
    if held:
        summary += f" 개인정보가 남아 보낼 수 없는 결정 {held}건."
    return _result(
        summary=summary,
        items=[
            {"title": d.statement, "body": "확정", "score": 1.0, "id": d.id}
            for d in outbound.decisions
        ],
        evidence=[u for d in outbound.decisions[:MAX_ITEMS] for u in sources.get(d.id, [])],
    )


def person_action_items(session: Session, team_id: str, user_id: str) -> dict[str, Any]:
    """Use this when asked what one person has on their plate -- "what is left for
    박지영", "what is overdue for me". Do not use it to compare people -- that is
    ``workload_by_owner`` -- or for a whole team's deadlines -- that is
    ``open_action_items``.

    Returns the person's open confirmed items from the team's meetings, most
    urgent first (at most five), with open and overdue counts in ``summary``.
    Work state only, the same a task board shows; nothing about speech.
    """
    member = session.scalar(
        select(User.display_name)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id, User.id == user_id)
    )
    if member is None:
        return _result(
            ok=False,
            reason=f"{user_id} is not on team {team_id}",
            summary="이 팀의 팀원이 아닙니다.",
            items=[],
            evidence=[],
            confidence=0.0,
        )
    today = date.today()
    meeting_ids = set(session.scalars(select(Meeting.id).where(Meeting.team_id == team_id)))
    mine = [
        i
        for status in _OPEN
        for i in service.list_action_items(session, assignee_id=user_id, status=status)
        if i.meeting_id in meeting_ids
    ]
    ranked = sorted(mine, key=lambda i: _urgency(i, today), reverse=True)
    overdue = sum(_overdue(i, today) for i in mine)
    return _result(
        summary=f"{member}님의 진행 중 액션아이템 {len(mine)}건, 기한 지남 {overdue}건.",
        items=[_item_finding(i, today) for i in ranked],
        evidence=[u for i in ranked[:MAX_ITEMS] for u in i.source_utterance_ids],
    )


def action_item_status(session: Session, team_id: str, action_item_id: str) -> dict[str, Any]:
    """Use this when asked about one action item by id -- who holds it, when it
    is due, whether it reached Notion. Do not use it to find items; the list
    tools return ids.

    Returns the item as one finding. An item still waiting for confirmation is
    reported as waiting, without its text (#261 rule 3).
    """
    row = session.get(ExtActionItem, action_item_id)
    if row is None or _team_of(session, row.meeting_id) != team_id:
        return _not_found("action item", action_item_id)
    if row.status == ActionStatus.NEEDS_CONFIRMATION.value:
        return _result(
            summary="확인 대기 중인 액션아이템입니다. 확정 전이라 내용은 보여주지 않습니다.",
            items=[{"title": "확인 대기", "body": "", "score": 0.8, "id": row.id}],
            evidence=[],
        )
    (read,) = [
        i
        for i in service.list_action_items(session, meeting_id=row.meeting_id)
        if i.id == action_item_id
    ]
    finding = _item_finding(read, date.today())
    synced = [r.system for r in read.sync_refs if r.url]
    finding["body"] += f" · {'·'.join(synced)} 연동됨" if synced else " · 외부 연동 없음"
    return _result(summary="액션아이템 1건.", items=[finding], evidence=read.source_utterance_ids)


def _team_of(session: Session, meeting_id: str) -> str | None:
    return session.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id))


def _not_found(kind: str, ident: str) -> dict[str, Any]:
    # The same answer for an unknown id and another team's: an agent run is
    # scoped to one team, and a difference would say the id exists (#189).
    return _result(
        ok=False,
        reason=f"no {kind} {ident} in this team",
        summary="이 팀에서 찾을 수 없습니다.",
        items=[],
        evidence=[],
        confidence=0.0,
    )


TOOLS = [
    meeting_action_items,
    open_action_items,
    workload_by_owner,
    unresolved_questions,
    review_state,
    meeting_decisions,
    person_action_items,
    action_item_status,
]
"""Collected by the agent layer by iterating modules (invariant 6), never registered by hand."""

RUN_SCOPE = ("team_id",)
"""Arguments the run fills from its authenticated scope, never the model.

Every tool and action here that takes ``team_id`` trusts it to be the team the
run is for; a model that could choose it could read or change another team's
work. The main agent binds these the way LangChain's ``ToolRuntime`` context or
an injected argument does -- hidden from the model's schema -- and routes only
the rest to it (review of #449)."""


# --- actions: B's writes, for the main agent to run after approval ---------------
#
# agent-layer.md section 8, rule 2: the agent never edits an item itself; it asks
# the module that owns it, and B's own service applies the change with the same
# checks and the same after-commit sync the board uses. Every action here is L2:
# the main agent proposes it in plan mode (or behind LangChain's
# HumanInTheLoopMiddleware, ``interrupt_on``), and runs it only when a person
# approves. None is in ``TOOLS``, so a model never calls one directly, and none
# deletes anything (L3 is forbidden). Each owns its transaction, like a board
# request: it commits, then syncs, and a sync failure never undoes the change.


def _acted(summary: str, ident: str) -> dict[str, Any]:
    return _result(
        summary=summary,
        items=[{"title": summary, "body": "", "score": 1.0, "id": ident}],
        evidence=[],
    )


def _refused(reason: str, summary: str) -> dict[str, Any]:
    return _result(ok=False, reason=reason, summary=summary, items=[], evidence=[], confidence=0.0)


def _as_date(value: date | str | None) -> date | None:
    """A model hands a date over as text; ``2026-10-02`` and nothing looser."""
    return date.fromisoformat(value) if isinstance(value, str) else value


def _on_team(session: Session, team_id: str, user_id: str) -> bool:
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        is not None
    )


def _change_item(
    team_id: str, action_item_id: str, payload: ActionItemUpdate
) -> dict[str, Any] | str:
    """Apply ``payload`` to one of the team's items; the new status, or a refusal."""
    with session_scope() as session:
        row = session.get(ExtActionItem, action_item_id)
        if row is None or _team_of(session, row.meeting_id) != team_id:
            return _not_found("action item", action_item_id)
        assignee = payload.assignee_id
        if assignee is not None and not _on_team(session, team_id, assignee):
            # Stricter than the board, which checks only that the user exists:
            # an approved move must land on someone who can see the item.
            return _refused(
                f"{assignee} is not on team {team_id}", "그 사람은 이 팀의 팀원이 아닙니다."
            )
        service.update_action_item(session, row, payload)
        status = row.status
    if status != ActionStatus.NEEDS_CONFIRMATION.value:
        tasks.sync_after_confirmation(action_item_id)
    return status


def confirm_action_item(team_id: str, action_item_id: str) -> dict[str, Any]:
    """Confirm an item waiting for confirmation, making it a to-do. It then goes to
    Notion and its assignee's calendar, as when a person confirms it on the board.

    L2 -- runs only after a person approves. Use when a person asks to accept a
    proposed item. Refused for an item that is already confirmed.
    """
    with session_scope() as session:
        row = session.get(ExtActionItem, action_item_id)
        if row is None or _team_of(session, row.meeting_id) != team_id:
            return _not_found("action item", action_item_id)
        if row.status != ActionStatus.NEEDS_CONFIRMATION.value:
            return _refused("already confirmed", "이미 확정된 액션아이템입니다.")
    result = _change_item(team_id, action_item_id, ActionItemUpdate(status=ActionStatus.TODO))
    return (
        result if isinstance(result, dict) else _acted("액션아이템을 확정했습니다.", action_item_id)
    )


def reassign_action_item(team_id: str, action_item_id: str, assignee_id: str) -> dict[str, Any]:
    """Give an item to another member of the team -- what the Workload subagent
    proposes when one person holds too much.

    L2 -- runs only after a person (the manager, for Workload) approves. The new
    assignee must be on the team.
    """
    result = _change_item(team_id, action_item_id, ActionItemUpdate(assignee_id=assignee_id))
    return result if isinstance(result, dict) else _acted("담당자를 바꿨습니다.", action_item_id)


def set_action_item_due_date(
    team_id: str, action_item_id: str, due_date: date | str | None
) -> dict[str, Any]:
    """Move an item's due date, or clear it with ``None``. The assignee's calendar
    event and the Notion page follow.

    L2 -- runs only after a person approves. ``due_date`` is ``YYYY-MM-DD``.
    """
    try:
        due = _as_date(due_date)
    except ValueError:
        return _refused(f"not a date: {due_date!r}", "날짜 형식이 아닙니다 (YYYY-MM-DD).")
    result = _change_item(team_id, action_item_id, ActionItemUpdate(due_date=due))
    return result if isinstance(result, dict) else _acted("기한을 바꿨습니다.", action_item_id)


def set_action_item_status(team_id: str, action_item_id: str, status: str) -> dict[str, Any]:
    """Move a confirmed item between to-do, in progress and done.

    L2 -- runs only after a person approves. ``status`` is ``todo``,
    ``in_progress`` or ``done``; confirming is ``confirm_action_item``.
    """
    moves = {ActionStatus.TODO, ActionStatus.IN_PROGRESS, ActionStatus.DONE}
    if status not in {s.value for s in moves}:
        return _refused(f"not a status: {status!r}", "todo, in_progress, done 중 하나여야 합니다.")
    result = _change_item(team_id, action_item_id, ActionItemUpdate(status=ActionStatus(status)))
    return result if isinstance(result, dict) else _acted("상태를 바꿨습니다.", action_item_id)


def add_action_item(
    team_id: str,
    meeting_id: str,
    description: str,
    assignee_id: str | None = None,
    due_date: date | str | None = None,
) -> dict[str, Any]:
    """Add an item a meeting missed, as a person would on the board. It starts
    waiting for confirmation, so it reaches nobody until someone confirms it.

    L2 -- runs only after a person approves. ``description`` is stored as given;
    write it from masked text only.
    """
    try:
        payload = ActionItemCreate(
            meeting_id=meeting_id,
            description=description,
            assignee_id=assignee_id,
            due_date=_as_date(due_date),
        )
    except ValueError as exc:
        return _refused(
            f"invalid item: {type(exc).__name__}", "액션아이템 내용이 올바르지 않습니다."
        )
    with session_scope() as session:
        if _team_of(session, meeting_id) != team_id:
            return _not_found("meeting", meeting_id)
        if assignee_id is not None and not _on_team(session, team_id, assignee_id):
            return _refused(
                f"{assignee_id} is not on team {team_id}", "그 사람은 이 팀의 팀원이 아닙니다."
            )
        row = service.create_action_item(session, payload)
        session.flush()
        new_id = row.id
    return _acted("액션아이템을 추가했습니다 (확인 대기).", new_id)


def review_decision(team_id: str, decision_id: str, verdict: str) -> dict[str, Any]:
    """Confirm or reject a decision the model proposed. A confirmed one goes to
    Notion, as when a person confirms it on the review screen.

    L2 -- runs only after a person approves. ``verdict`` is ``confirmed`` or
    ``rejected``.
    """
    if verdict not in ("confirmed", "rejected"):
        return _refused(f"not a verdict: {verdict!r}", "confirmed 또는 rejected여야 합니다.")
    with session_scope() as session:
        decision = session.get(ExtDecision, decision_id)
        if decision is None or _team_of(session, decision.meeting_id) != team_id:
            return _not_found("decision", decision_id)
        service.review_decision(session, decision, DecisionReviewUpdate(status=verdict))  # type: ignore[arg-type]
    if verdict == "confirmed":
        tasks.sync_decision_after_confirmation(decision_id)
    return _acted(
        "결정을 확정했습니다." if verdict == "confirmed" else "결정을 기각했습니다.", decision_id
    )


ACTIONS = [
    confirm_action_item,
    reassign_action_item,
    set_action_item_due_date,
    set_action_item_status,
    add_action_item,
    review_decision,
]
"""B's writes, all L2 (see above). Kept out of ``TOOLS`` on purpose: the registry
offers ``TOOLS`` to models, and the approval executor alone runs these."""
