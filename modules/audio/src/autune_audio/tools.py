"""Module A as tools an agent can call (#260/#261, docs/architecture/agent-layer.md section 4).

Five reads over what A already stores, and no writes. A exposes reads and the
transcript, not a re-transcribe: ``process_recording`` deletes the recording in
a ``finally`` (invariant 11), so it is not safe to call twice and is not a tool
(section 4, rule 4).

- ``meeting_overview`` -- the high-level one: what a meeting is, where it is in
  the pipeline, and who spoke.
- ``recent_meetings`` -- which meetings a team has, so the agent can find the
  one a request is about.
- ``find_utterances`` and ``quote_utterances`` -- ``grep`` and ``read`` over one
  meeting's transcript. The agent gets ids first and text only for what a step
  needs (section 10: "a transcript never enters a prompt").
- ``search_team_meetings`` -- the same ``grep`` across the team's recent meetings,
  for "was this said before".

Each returns a plain dict in the ``ToolResult`` shape, which ``autune_agent``
validates when it collects ``TOOLS``. This module may not import
``autune_agent`` (ADR 0010), which is why the shape is built here by hand, the
way modules B and E build theirs.

**Scope is the run's, not the model's.** ``team_id`` and ``meeting_id`` are
filled and checked by the agent's ``Toolbox`` against the team the run was
started for (#449). These functions take them as given.

**Text comes only from speakers who consented.** The same line B and C draw
(``autune_extraction.service.consented_utterance_ids``, #163): an utterance
whose participant did not consent, or has no participant, is not quoted. It is
still counted in ``meeting_overview``, because a count says nothing about what
was said. Everything read here is masked already -- A masks before the first
write (privacy.md section 2) -- so there is no unmasked text to leak.

**No speaking ratio, not even by ordering.** Invariant 11 sends a person's
speaking ratio to that person only, and the agent answers on someone else's
behalf. ``meeting_overview`` lists speakers in the order they first spoke, never
by how long, and reports no durations per speaker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, Participant, User, Utterance

MAX_ITEMS = 5
"""agent-layer.md section 4: a tool ranks and keeps five; the rest stay in A's tables."""

MAX_QUERY_CHARS = 100
"""A search term, not a pasted paragraph."""

MAX_BODY_CHARS = 200
"""One utterance's text in an item. Longer is cut and marked, so a single
monologue cannot fill the prompt budget (section 10)."""

KST = ZoneInfo("Asia/Seoul")

STATUS_LABEL = {
    "scheduled": "예정",
    "recording": "녹음 중",
    "analyzing": "분석 중",
    "awaiting_confirmation": "확인 대기",
    "complete": "분석 완료",
    "delivered": "전달됨",
    "failed": "실패",
}
"""The names the screens use (``apps/web/src/features/transcript/status.ts``)."""


def _result(
    *,
    summary: str,
    items: list[dict[str, Any]],
    evidence: list[str],
    confidence: float = 1.0,
    ok: bool = True,
    reason: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": reason,
        "summary": summary,
        "items": items[:MAX_ITEMS],
        "evidence": evidence,
        "confidence": confidence,
        "truncated": truncated or len(items) > MAX_ITEMS,
    }


def _refused(reason: str, summary: str) -> dict[str, Any]:
    return _result(summary=summary, items=[], evidence=[], ok=False, reason=reason, confidence=0.0)


def _missing() -> dict[str, Any]:
    # The id is not echoed: it is whatever the model wrote.
    return _refused("meeting not found", "그 회의를 찾을 수 없습니다.")


def _when(value: datetime | None) -> str:
    if value is None:
        return "시각 미정"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def _clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_BODY_CHARS else text[: MAX_BODY_CHARS - 1] + "…"


@dataclass(frozen=True)
class _Speaker:
    name: str
    identified: bool
    consented: bool


def _speakers(session: Session, meeting_id: str) -> dict[str, _Speaker]:
    """Participant id -> how to name them. A confirmed person by their display
    name; anyone else by the diarizer's label, which says nothing about who."""
    rows = session.execute(
        sa.select(Participant, User.display_name)
        .outerjoin(User, User.id == Participant.user_id)
        .where(Participant.meeting_id == meeting_id)
    ).all()
    return {
        p.id: _Speaker(
            name=name or p.speaker_label, identified=name is not None, consented=p.consented
        )
        for p, name in rows
    }


def _utterance_item(row: Utterance, speakers: dict[str, _Speaker]) -> dict[str, Any]:
    speaker = speakers.get(row.participant_id or "")
    return {
        "title": f"{_clock(row.start_sec)} {speaker.name if speaker else row.speaker_label}",
        "body": _clip(row.text),
        "score": 0.0,
        "id": row.id,
    }


def _consented(meeting_id: str) -> Any:
    """Utterances of this meeting whose speaker consented. Unknown is not yes."""
    return (
        sa.select(Utterance)
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(Utterance.meeting_id == meeting_id, Participant.consented.is_(True))
    )


def meeting_overview(session: Session, meeting_id: str) -> dict[str, Any]:
    """Use this first when a request is about one meeting: what it was, when, whether
    its transcript is ready, and who spoke. Do not use it to find a meeting --
    that is ``recent_meetings`` -- or to read what was said -- that is
    ``find_utterances``.

    Returns the meeting's title, start, status and length in ``summary``, and its
    speakers in the order they first spoke, at most five. A status other than
    분석 완료, 확인 대기 or 전달됨 means there is no transcript to search yet.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return _missing()
    speakers = _speakers(session, meeting_id)
    total = session.scalar(
        sa.select(sa.func.count()).select_from(Utterance).where(Utterance.meeting_id == meeting_id)
    )
    first_spoke = session.execute(
        sa.select(Utterance.participant_id, sa.func.min(Utterance.start_sec))
        .where(Utterance.meeting_id == meeting_id, Utterance.participant_id.is_not(None))
        .group_by(Utterance.participant_id)
        .order_by(sa.func.min(Utterance.start_sec))
    ).all()
    ordered = [speakers[pid] for pid, _ in first_spoke if pid in speakers]

    parts = [f"「{meeting.title}」", f"{_when(meeting.started_at)} 시작"]
    parts.append(STATUS_LABEL.get(meeting.status, meeting.status))
    if meeting.duration_seconds:
        parts.append(f"{round(meeting.duration_seconds / 60)}분")
    summary = " · ".join(parts) + "."
    if total:
        identified = sum(s.identified for s in ordered)
        consented = sum(s.consented for s in ordered)
        summary += (
            f" 발언 {total}건, 화자 {len(ordered)}명(이름 확인 {identified}명)."
            f" 분석에 동의한 화자 {consented}명."
        )
    else:
        summary += " 저장된 발언이 없습니다."
    items = [
        {
            "title": s.name,
            "body": ("이름 확인됨" if s.identified else "이름 미확인")
            + (" · 분석 동의" if s.consented else " · 동의 없음"),
            "score": 0.0,
        }
        for s in ordered
    ]
    return _result(summary=summary, items=items, evidence=[meeting.id])


def recent_meetings(session: Session, team_id: str, *, days: int = 30) -> dict[str, Any]:
    """Use this to find which meeting a request is about -- "last week's sprint
    review", "the meeting on Tuesday" -- or to see what is coming up. Do not
    use it for what happened in a meeting; pass the ``meeting_id`` it returns
    to ``meeting_overview``.

    Returns the team's meetings from the last ``days`` days and every scheduled
    one ahead, newest first, at most five, each with its ``meeting_id``.
    """
    days = max(1, min(days, 365))
    when = sa.func.coalesce(Meeting.started_at, Meeting.created_at)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        session.scalars(
            sa.select(Meeting)
            .where(Meeting.team_id == team_id, when >= since)
            .order_by(when.desc())
            .limit(MAX_ITEMS + 1)
        )
    )
    if not rows:
        return _result(
            summary=f"최근 {days}일 동안 이 팀의 회의가 없습니다.", items=[], evidence=[]
        )
    items = [
        {
            "title": m.title,
            "body": f"{_when(m.started_at)} · {STATUS_LABEL.get(m.status, m.status)}",
            "score": 0.0,
            "meeting_id": m.id,
            "status": m.status,
        }
        for m in rows
    ]
    shown = rows[:MAX_ITEMS]
    more = " 더 있습니다." if len(rows) > MAX_ITEMS else ""
    return _result(
        summary=f"최근 {days}일과 예정된 회의 {len(shown)}건.{more}",
        items=items,
        evidence=[m.id for m in shown],
    )


def find_utterances(session: Session, meeting_id: str, query: str) -> dict[str, Any]:
    """Use this to check whether and where something was said in one meeting --
    a name, a number, a product, "배포". Do not use it to summarise a meeting;
    module B and C's tools already hold what was decided and what was missed.

    Matches ``query`` as plain text, ignoring case, in utterances from speakers
    who consented. Returns the first five in the order they were said, each with
    its time, speaker and masked text, and the utterance ids as ``evidence``.
    """
    term = query.strip()
    if not term:
        return _refused("empty query", "찾을 말이 비어 있습니다.")
    if len(term) > MAX_QUERY_CHARS:
        return _refused("query too long", f"검색어는 {MAX_QUERY_CHARS}자까지입니다.")
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return _missing()
    matched = _consented(meeting_id).where(Utterance.text.icontains(term, autoescape=True))
    count = session.scalar(sa.select(sa.func.count()).select_from(matched.subquery())) or 0
    if not count:
        return _result(summary="일치하는 발언이 없습니다.", items=[], evidence=[])
    rows = list(session.scalars(matched.order_by(Utterance.start_sec).limit(MAX_ITEMS)))
    speakers = _speakers(session, meeting_id)
    return _result(
        summary=f"일치하는 발언 {count}건." + (" 앞의 다섯 건입니다." if count > MAX_ITEMS else ""),
        items=[_utterance_item(r, speakers) for r in rows],
        evidence=[r.id for r in rows],
        truncated=count > MAX_ITEMS,
    )


def quote_utterances(
    session: Session, meeting_id: str, utterance_ids: Sequence[str]
) -> dict[str, Any]:
    """Use this only when a step needs the exact words -- to quote a decision back
    to the people who made it, or to check an id another tool returned as
    evidence. Do not use it to read a meeting through; that is what the other
    modules' tools are for.

    Returns the text of up to five of ``utterance_ids``, from this meeting only
    and only from speakers who consented, in the order they were said. Ids that
    are not in the meeting or not quotable are counted, not explained.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return _missing()
    wanted = list(dict.fromkeys(utterance_ids))
    asked = wanted[:MAX_ITEMS]
    rows = list(
        session.scalars(
            _consented(meeting_id).where(Utterance.id.in_(asked)).order_by(Utterance.start_sec)
        )
    )
    skipped = len(asked) - len(rows)
    summary = f"발언 {len(rows)}건."
    if skipped:
        summary += f" {skipped}건은 이 회의에 없거나 인용할 수 없는 발언입니다."
    if len(wanted) > MAX_ITEMS:
        summary += f" 한 번에 {MAX_ITEMS}건까지만 가져옵니다."
    speakers = _speakers(session, meeting_id)
    return _result(
        summary=summary,
        items=[_utterance_item(r, speakers) for r in rows],
        evidence=[r.id for r in rows],
        ok=bool(rows),
        reason=None if rows else "no quotable utterance",
        confidence=1.0 if rows else 0.0,
        truncated=len(wanted) > MAX_ITEMS,
    )


def search_team_meetings(
    session: Session,
    team_id: str,
    query: str,
    *,
    days: int = 90,
    exclude_meeting_id: str | None = None,
) -> dict[str, Any]:
    """Use this to check whether something came up in the team's other meetings --
    what was said before about a question raised today. Do not use it for one
    meeting; that is ``find_utterances``.

    Matches ``query`` as plain text, ignoring case, in utterances from speakers
    who consented, across the team's meetings from the last ``days`` days,
    leaving out ``exclude_meeting_id``. Returns five, newest meeting first, each
    with the meeting's date and title, the time, the speaker and masked text.
    """
    term = query.strip()
    if not term:
        return _refused("empty query", "찾을 말이 비어 있습니다.")
    if len(term) > MAX_QUERY_CHARS:
        return _refused("query too long", f"검색어는 {MAX_QUERY_CHARS}자까지입니다.")
    days = max(1, min(days, 365))
    when = sa.func.coalesce(Meeting.started_at, Meeting.created_at)
    since = datetime.now(UTC) - timedelta(days=days)
    matched = (
        sa.select(Utterance, Meeting)
        .join(Participant, Participant.id == Utterance.participant_id)
        .join(Meeting, Meeting.id == Utterance.meeting_id)
        .where(
            Meeting.team_id == team_id,
            when >= since,
            Participant.consented.is_(True),
            Utterance.text.icontains(term, autoescape=True),
        )
    )
    if exclude_meeting_id is not None:
        matched = matched.where(Meeting.id != exclude_meeting_id)
    count = session.scalar(sa.select(sa.func.count()).select_from(matched.subquery())) or 0
    if not count:
        return _result(
            summary="팀의 다른 회의에서 일치하는 발언이 없습니다.", items=[], evidence=[]
        )
    rows = session.execute(
        matched.order_by(when.desc(), Utterance.start_sec).limit(MAX_ITEMS)
    ).all()
    items = []
    for utterance, meeting in rows:
        speakers = _speakers(session, meeting.id)
        item = _utterance_item(utterance, speakers)
        item["title"] = f"{_when(meeting.started_at)[:10]} {meeting.title} · {item['title']}"
        item["meeting_id"] = meeting.id
        items.append(item)
    return _result(
        summary=f"팀의 다른 회의에서 일치하는 발언 {count}건."
        + (" 최근 다섯 건입니다." if count > MAX_ITEMS else ""),
        items=items,
        evidence=[u.id for u, _ in rows],
        truncated=count > MAX_ITEMS,
    )


TOOLS = [
    meeting_overview,
    recent_meetings,
    find_utterances,
    quote_utterances,
    search_team_meetings,
]

PERSONAL_ONLY_TOOLS: list[Any] = []
"""None. Nothing above returns one person's own data; a speaking ratio is never
computed here (see the module docstring)."""
