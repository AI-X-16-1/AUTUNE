"""The Meeting Context Engine as tools an agent can call (agent-layer.md section 4).

Three reads over what D's pipeline already computed -- this meeting's topic
links, a decision thread's timeline, and the team's decisions by their current
head -- for the Briefing subagent and anything else that asks "what did we say
about this before". Each returns a dict in the shape agent-layer.md calls
``ToolResult``::

    {"ok", "reason", "summary", "items", "evidence", "confidence", "truncated"}

**Plain functions, no ``autune_agent`` import**, the same shape as A's, B's, C's
and E's ``tools.py``: ADR 0010 puts ``autune_agent`` above every module, so the
registry validates these dicts when it collects them. Only reads, safe to call
twice. D has no ``ACTIONS``: confirming a link is a person's act on S15, not an
agent's.

What holds for all three, and is tested rather than trusted:

- **Same visibility as the read API.** Every read goes through the ``service``
  function the route uses, so a meeting past its retention window is gone here
  the moment it expires, not once the sweep deletes it
  (docs/modules/context.md, "Deletion"). A thread or meeting of another team
  reads as missing, the 404-not-403 rule of ``service.require_readable_*``.
- **``key_stakeholders_absent`` is never returned.** It names who was not in the
  room when a decision changed. Attendance is shared data, but a list of who
  keeps missing which decision is exactly the per-person aggregate the read API
  withholds (docs/modules/context.md, "Privacy notes", #188). No tool here
  reads the column.
- **A quoted predecessor goes when its meeting does.** ``previous_statement`` is
  a verbatim copy of an earlier meeting's decision. It is returned only while
  that meeting is still visible -- the rule ``router.get_decision_thread``
  applies, with one difference: a statement with no predecessor id is withheld
  too, since there is then nothing to say the quoted meeting is still readable.
- **Unconfirmed links are counted, not quoted.** A ``pending`` link is D asking a
  person whether two meetings are the same topic. ``links_for_meeting`` lists
  only settled links (``asserted`` and ``confirmed``) and says how many are
  waiting, so the agent never states a guess as a fact.
- ``evidence`` holds ids only -- a meeting or a thread -- never text.
  ``items`` holds at most five; ``truncated`` says when there were more.
- ``team_id`` comes from the run's scope (``RUN_SCOPE``), never from a model.
  The agent's toolbox also refuses a ``meeting_id`` from another team; the tools
  check it again.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_context import service
from autune_context.dates import meeting_day
from autune_context.models import CtxDecision, CtxMeetingStatus, CtxTopicLink
from autune_context.pipeline.retrieval import visible_meeting_clauses
from autune_contracts import ChangeType
from autune_core import Meeting
from autune_core.errors import NotFoundError

MAX_ITEMS = 5
"""agent-layer.md section 4: a tool ranks and keeps five; the rest stay in D's tables."""

TITLE_CHARS = 200
"""A decision statement is one sentence, but nothing bounds it. Cut so five items
cannot fill a prompt."""

_ID = re.compile(r"[a-z]+_[A-Za-z0-9]+")
"""The shape ``ToolResult.evidence`` accepts. A value that is not one is left
out of ``evidence``: the registry raises on it, which would turn a malformed
legacy id into a failed run."""


def _result(
    *,
    summary: str,
    items: list[dict[str, Any]],
    evidence: list[str],
    ok: bool = True,
    reason: str | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    """``total`` is how many there were before ``items`` was cut to what is shown."""
    evidence = list(dict.fromkeys(value for value in evidence if _ID.fullmatch(value)))
    return {
        "ok": ok,
        "reason": reason,
        "summary": summary,
        "items": items[:MAX_ITEMS],
        "evidence": evidence,
        "confidence": 1.0 if ok else 0.0,
        "truncated": (len(items) if total is None else total) > MAX_ITEMS,
    }


def _missing(kind: str, ident: str) -> dict[str, Any]:
    return _result(
        ok=False,
        reason=f"{kind} {ident} not found",
        summary="그 항목을 찾을 수 없습니다.",
        items=[],
        evidence=[],
    )


def _clip(text: str, limit: int = TITLE_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _meeting(session: Session, team_id: str, meeting_id: str) -> Meeting | None:
    """The meeting if it is this team's and still inside its retention window."""
    return session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, *visible_meeting_clauses(team_id))
    )


def _meeting_days(session: Session, meeting_ids: set[str]) -> dict[str, date]:
    if not meeting_ids:
        return {}
    days: dict[str, date] = {}
    for meeting_id, started_at, created_at in session.execute(
        select(Meeting.id, Meeting.started_at, Meeting.created_at).where(
            Meeting.id.in_(meeting_ids)
        )
    ):
        moment = started_at or created_at
        if moment is not None:
            days[meeting_id] = meeting_day(moment)
    return days


def _link_body(link: CtxTopicLink) -> str:
    if link.linked_meeting_id is None:
        return "연결된 회의는 보존 기간이 지나 삭제되었습니다."
    if link.linked_meeting_date is None:
        return ""
    return f"{link.linked_meeting_date:%Y-%m-%d} 회의에서도 논의됨"


def links_for_meeting(session: Session, team_id: str, meeting_id: str) -> dict[str, Any]:
    """Use this to see which earlier meetings discussed what this meeting
    discussed: its topics, each linked to a past meeting, strongest first. Do
    not use it for how a decision changed -- ``decision_thread`` answers that.

    Returns at most five settled links. Each has the topic, the date of the
    earlier meeting and how sure D is. Links still waiting for a person to
    confirm them are counted in the summary and not listed: do not present them
    as established.
    """
    meeting = _meeting(session, team_id, meeting_id)
    if meeting is None:
        return _missing("meeting", meeting_id)
    status = session.get(CtxMeetingStatus, meeting_id)
    if status is None or not status.topic_linking_done:
        return _result(
            ok=False,
            reason=f"meeting {meeting_id} has no topic linking yet",
            summary="이 회의는 아직 주제 연결이 끝나지 않았습니다.",
            items=[],
            evidence=[],
        )

    settled, pending = service.get_topic_links(session, meeting_id)
    waiting = f" 사용자 확인을 기다리는 연결이 {len(pending)}건 있습니다." if pending else ""
    if not settled:
        return _result(
            summary=f"이 회의에 연결된 과거 주제가 없습니다.{waiting}", items=[], evidence=[]
        )

    items = [
        {
            "id": link.id,
            "title": _clip(link.topic_label),
            "body": _link_body(link),
            "score": link.confidence,
            "status": link.status,
            "linked_meeting_id": link.linked_meeting_id,
            "linked_meeting_date": (
                link.linked_meeting_date.isoformat() if link.linked_meeting_date else None
            ),
        }
        for link in settled
    ]
    return _result(
        summary=f"이 회의의 주제 {len(settled)}건이 과거 회의와 연결되어 있습니다.{waiting}",
        items=items,
        evidence=[link.linked_meeting_id for link in settled[:MAX_ITEMS] if link.linked_meeting_id],
    )


def decision_thread(session: Session, team_id: str, thread_id: str) -> dict[str, Any]:
    """Use this to see how one decision moved across meetings: its versions,
    oldest first, each marked unchanged, modified, reversed or new. Do not use
    it to find a decision by topic -- ``list_decisions`` does that and returns
    the thread ids this one takes.

    Returns the thread's five most recent versions, in time order. Each has the
    decision as stated in its meeting, how it changed from the one before, the
    earlier wording while that meeting is still readable, and the meeting's
    date. Nothing about who attended.
    """
    thread = session.get(CtxDecision, thread_id)
    if thread is None or thread.team_id != team_id:
        return _missing("decision thread", thread_id)
    try:
        _, versions, visible_prior = service.get_decision_lineage(session, thread_id)
    except NotFoundError:
        return _missing("decision thread", thread_id)

    shown = versions[-MAX_ITEMS:]
    days = _meeting_days(session, {version.meeting_id for version in shown})
    items = [
        {
            "id": version.id,
            "title": _clip(version.current_statement),
            "body": (
                _clip(f"이전 결정: {version.previous_statement}")
                if version.previous_statement is not None
                and version.previous_meeting_id in visible_prior
                else ""
            ),
            "score": version.confidence,
            "change_type": version.change_type,
            "meeting_id": version.meeting_id,
            "meeting_date": days[version.meeting_id].isoformat()
            if version.meeting_id in days
            else None,
            "thread_id": thread_id,
        }
        for version in shown
    ]
    head = versions[-1]
    return _result(
        summary=(
            f"이 결정은 {len(versions)}개 회의에 걸쳐 있고, 가장 최근 변화는 "
            f"'{head.change_type}'입니다."
        ),
        items=items,
        evidence=[thread_id, *(version.source_decision_id for version in shown)],
        total=len(versions),
    )


def list_decisions(
    session: Session,
    team_id: str,
    topic: str | None = None,
    change_type: str | None = None,
) -> dict[str, Any]:
    """Use this to find the team's decisions: one row per decision thread, as it
    stands now, most recently touched first. Pass ``topic`` to match a word or
    phrase in the decision, and ``change_type`` (unchanged, modified, reversed,
    new) to keep only decisions whose latest change was that. Do not use it for
    the history of one decision -- ``decision_thread`` answers that.

    Returns at most five threads. Each has the current statement, how it last
    changed, and the meeting it was last stated in.
    """
    if change_type is not None and change_type not in {value.value for value in ChangeType}:
        return _result(
            ok=False,
            reason="change_type must be one of unchanged, modified, reversed, new",
            summary="change_type 값이 올바르지 않습니다.",
            items=[],
            evidence=[],
        )
    pairs = service.list_decisions(session, team_id, topic=topic, change_type=change_type)
    if not pairs:
        return _result(summary="조건에 맞는 결정이 없습니다.", items=[], evidence=[])

    shown = pairs[:MAX_ITEMS]
    days = _meeting_days(session, {version.meeting_id for _, version in shown})
    items = [
        {
            "id": thread.id,
            "title": _clip(version.current_statement),
            "body": "",
            "score": version.confidence,
            "change_type": version.change_type,
            "meeting_id": version.meeting_id,
            "meeting_date": days[version.meeting_id].isoformat()
            if version.meeting_id in days
            else None,
        }
        for thread, version in shown
    ]
    return _result(
        summary=f"결정 {len(pairs)}건이 조건에 맞습니다.",
        items=items,
        evidence=[thread.id for thread, _ in shown],
        total=len(pairs),
    )


TOOLS = [links_for_meeting, decision_thread, list_decisions]

RUN_SCOPE = ("team_id",)
"""Parameters the agent fills from the run's authenticated scope, never from a model."""

PERSONAL_ONLY_TOOLS: list[Any] = []
"""D computes no figure about one person that belongs to that person alone.
``key_stakeholders_absent`` is not one -- it is withheld for another reason, see
the module docstring -- and no tool reads it."""
