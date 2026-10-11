"""One meeting as a page of minutes, written as the 요약 tab's "회의록 복사" writes it.

**The other rendering is the web's** -- ``apps/web/src/features/actions/minutes.ts``
(``minutesOf`` / ``minutesText``). That one is built in the browser from what the
summary tab holds and put on the clipboard; this one is built here from the same
``service.meeting_summary`` and handed to the assistant (``tools.meeting_minutes``),
so "이 회의 회의록 써 줘" is answered with the page a person would have copied.
The rules are the web's, line for line, and ``tests/unit/test_minutes.py`` holds
the strings ``CopyMinutes.test.tsx`` holds. A change to one is a change to both.

What the page carries, as minutes.ts says it: the meeting's title and the day it
began; a model's overview only where one was written, headed "AI 작성"; the
decisions the summary lists, a pending one marked "(자동 추출)"; the action items
except candidates, each "description — who · due · state", where an item closed
without being finished is "끝내지 않고 닫힘" and one not begun says nothing; every
typed sentence on one line (#1229); and the team's memo, which keeps its lines.
No quotation of an utterance: the lines an item was drawn from are not on it.

**One difference, and it is the screening.** The web copy never leaves the
reader's browser except by the reader's own paste. This page leaves B for a
model, so every line of typed text on it goes through ``find_unmasked``, the
check ``service.outbound_for_meeting`` and ``tools._shown_title`` run on what
leaves: a line that carries personal data is held back and counted in
``held_back``, never sent. The date is Korea's (``slots.KST``), not a browser's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from autune_integrations.privacy import find_unmasked

from .schemas import ActionItemRead, MeetingSummary
from .slots import KST

WRITTEN_BY_MODEL = "AI 작성"
"""minutes.ts ``WRITTEN_BY_MODEL``: the heading over a model's overview."""

UNCONFIRMED_DECISION = "자동 추출"
"""types.ts ``UNCONFIRMED_DECISION``: what follows a decision nobody confirmed."""

CLOSED_UNFINISHED = "끝내지 않고 닫힘"
"""minutes.ts ``CLOSED_UNFINISHED``: an item closed without being finished (#856)."""

COLUMN_LABELS = {
    "needs_confirmation": "확인 필요",
    "todo": "진행 전",
    "in_progress": "진행 중",
    "done": "완료",
}
"""types.ts ``COLUMN_LABELS``: the board's column names."""

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
"""Indexed by ``date.weekday()``, Monday first -- dates.ts's ``WEEKDAYS`` starts on Sunday."""

_DAY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DEADLINE = re.compile(r"기한 ([0-9]{4}-[0-9]{2}-[0-9]{2})\)\Z")
"""statement.ts ``DEADLINE``: the deadline the server appends, at the very end only."""
_BREAK = re.compile(r"\s*\n\s*")


@dataclass
class MinutesPage:
    """The page as text, and what of it was left off."""

    text: str
    decision_ids: list[str] = field(default_factory=list)
    """The decisions on the page, in its order."""
    action_ids: list[str] = field(default_factory=list)
    """The action items on the page, in its order."""
    candidates: int = 0
    """Items left off because the model was not sure they were items at all."""
    held_back: int = 0
    """Lines left off because ``find_unmasked`` found personal data in them."""
    unconfirmed_decisions: int = 0
    waiting_actions: int = 0


def title_of(summary: MeetingSummary) -> str | None:
    """minutes.ts ``titleOf``: the meeting's own title, or the one its rows carry."""
    if summary.meeting_title is not None:
        return summary.meeting_title
    return next(
        (i.meeting_title for i in summary.action_items if i.meeting_title is not None), None
    )


def one_line(text: str) -> str:
    """minutes.ts ``oneLine``: a sentence typed over several lines, on one (#1229)."""
    return _BREAK.sub(" ", text).strip()


def written_day(iso: str, year: int | None) -> str | None:
    """dates.ts ``writtenDay``: ``2026-10-13`` as "10월 13일 화", the year only when
    it is not ``year`` -- or ``None`` when ``iso`` is not such a day.

    A month or day out of range rolls over as JavaScript's ``Date.UTC`` rolls it,
    so the weekday is the one the web would print beside the same digits.
    """
    parts = _DAY.fullmatch(iso)
    if parts is None:
        return None
    y, month, day = (int(p) for p in parts.groups())
    try:
        first = date(y + (month - 1) // 12, (month - 1) % 12 + 1, 1)
        weekday = _WEEKDAYS[(first + timedelta(days=day - 1)).weekday()]
    except (ValueError, OverflowError):
        return None
    written = f"{month}월 {day}일 {weekday}"
    return written if y == year else f"{y}년 {written}"


def shown_statement(statement: str, year: int | None) -> str:
    """statement.ts ``shownStatement``: the deadline that closes a decision's
    sentence, written the page's way; a date said inside it stays as said."""

    def rewrite(match: re.Match[str]) -> str:
        written = written_day(match.group(1), year)
        return f"기한 {written})" if written else match.group(0)

    return _DEADLINE.sub(rewrite, statement)


def _began(summary: MeetingSummary) -> datetime | None:
    at = summary.meeting_started_at
    if at is None:
        return None
    # A naive time is the UTC the server stores.
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(KST)


def _day_line(at: datetime) -> str:
    """minutes.ts ``dayOf``: "2026년 10월 8일 (목)"."""
    return f"{at.year}년 {at.month}월 {at.day}일 ({_WEEKDAYS[at.weekday()]})"


def action_meta(item: ActionItemRead, year: int | None) -> str:
    """minutes.ts ``actionMeta``: "김민경 · 10월 13일 화 · 진행 중"."""
    if item.needs_reassignment:
        who = "재배정 필요"
    elif item.assignee_name is not None:
        who = item.assignee_name
    elif item.assignee_label is not None:
        who = item.assignee_label
    else:
        who = "담당 미지정"
    due = None
    if item.due_date is not None:
        iso = item.due_date.isoformat()
        due = written_day(iso, year) or iso
    # "진행 전" is every item a meeting has just made; said on each line it says
    # nothing. A close is not 완료 (#856).
    if item.closed_unfinished:
        state: str | None = CLOSED_UNFINISHED
    elif item.status == "todo":
        state = None
    else:
        state = COLUMN_LABELS.get(item.status, item.status)
    return " · ".join([who, due or "기한 없음", *([state] if state else [])])


def _clean(text: str) -> bool:
    return not find_unmasked(text)


def minutes_page(summary: MeetingSummary, title: str | None) -> MinutesPage:
    """minutes.ts ``minutesText``, with each line of typed text screened."""
    page = MinutesPage(text="")
    began = _began(summary)
    year = began.year if began else None

    heading = f"회의록 — {title}" if title else "회의록"
    if heading != "회의록" and not _clean(heading):
        heading = "회의록"
        page.held_back += 1
    lines = [heading]
    if began:
        lines.append(_day_line(began))

    generated = summary.generated
    if generated is not None:
        overview = [generated.overview, *(f"- {p}" for p in generated.points)]
        # One paragraph and its points: held back together, so no heading is
        # left over a paragraph that is not there.
        if all(_clean(line) for line in overview):
            lines += ["", f"요약 ({WRITTEN_BY_MODEL})", *overview]
        else:
            page.held_back += 1

    lines += ["", "결정 사항"]
    shown_decisions: list[str] = []
    for decision in summary.decisions:
        unconfirmed = decision.status == "pending"
        line = one_line(shown_statement(decision.statement, year))
        if unconfirmed:
            line += f" ({UNCONFIRMED_DECISION})"
        if not _clean(line):
            page.held_back += 1
            continue
        shown_decisions.append(line)
        page.decision_ids.append(decision.id)
        page.unconfirmed_decisions += unconfirmed
    if not shown_decisions:
        lines.append("없음")
    lines += [f"{n}. {line}" for n, line in enumerate(shown_decisions, start=1)]

    lines += ["", "할 일"]
    shown_actions: list[str] = []
    for item in summary.action_items:
        if item.is_candidate:
            page.candidates += 1
            continue
        line = f"{one_line(item.description)} — {action_meta(item, year)}"
        if not _clean(line):
            page.held_back += 1
            continue
        shown_actions.append(line)
        page.action_ids.append(item.id)
        page.waiting_actions += item.status == "needs_confirmation"
    if not shown_actions:
        lines.append("없음")
    lines += [f"{n}. {line}" for n, line in enumerate(shown_actions, start=1)]

    note = (summary.note or "").strip()
    if note:
        if _clean(note):
            lines += ["", "메모", note]
        else:
            page.held_back += 1

    page.text = "\n".join(lines)
    return page
