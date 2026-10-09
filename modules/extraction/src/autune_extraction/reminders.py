"""Due-date reminders: the assignee is told, once, the day before and after a miss.

Asked for by the user (2026-10-02). An action item with a due date sat on the
board and nothing said when it was about to lapse; the only people who learned
it had were the ones who opened the board.

**Only the assignee, and only by direct message.** A reminder is about one
person's work and goes to that person's own Slack account, through the bot of
the team that held the meeting. Nobody else is told -- not the team's channel,
not a manager, not whoever made the item -- and nothing here counts or ranks
what a person has missed. An item with no account behind its assignee (a typed
name) has nobody to tell and is skipped.

**Two moments, each once -- and rarely twice.** Once is the claim in
``ext_due_reminders``, written with the send in one transaction. A send Slack
accepted followed by a commit that failed, or a delivery that timed out on
our side, takes the claim back with it, and the next run sends again. That
is the price of never keeping a claim for a message that did not go -- with
one exception: a send the outbound check refused keeps its claim, written
afterwards in its own transaction (``service.settle_refused_due_reminder``),
so the refusal is reported once and not every ten minutes.

**The two moments:**

- ``due_soon``: the day before the due date.
- ``overdue``: the day after it, or up to ``OVERDUE_DAYS`` after -- a window, so
  a worker that was down on the first day still says it, and an item that has
  been late for a month when this first runs is not announced as news.

Nothing on the due date itself: the calendar event the item already has is
that day's reminder. Once is kept by ``ext_due_reminders`` (item, kind, due
date); moving the date makes it a new date, so a new reminder.

**In Korea's daytime.** Days are Korea's days -- the same clock the due date
was read against (``slots.KST``) -- and nothing is sent before ``SEND_FROM`` or
from ``SEND_UNTIL``: a message at midnight that something is due tomorrow is
worse than none.

**What leaves:** the item's description, its due date, its meeting's title and
a link to that meeting's board. No utterance, no other person's name unless
the description itself carries one. Sent through ``SlackClient``, so the
outbound check reads it like every other message.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .slots import KST

DUE_SOON = "due_soon"
OVERDUE = "overdue"
KINDS = (DUE_SOON, OVERDUE)

OVERDUE_DAYS = 3
"""How many days after the due date an ``overdue`` reminder may still go."""

SEND_FROM = time(9, 0)
SEND_UNTIL = time(20, 0)
"""Korea time. Reminders go out from nine in the morning until eight at night."""

_OPENING = {
    DUE_SOON: "내일까지인 액션 아이템이 있습니다.",
    OVERDUE: "기한이 지난 액션 아이템이 있습니다.",
}


def korean_day(now: datetime) -> date:
    """The date in Korea at ``now``."""
    return now.astimezone(KST).date()


_WEEKDAYS = "월화수목금토일"


def written_day(day: date, *, year: int) -> str:
    """``10월 13일 (화)``: a day as the 요약 tab's minutes write one, where the
    notice after a meeting and the work report said ``2026-10-13`` (the user,
    2026-10-09). The year is written only when it is not ``year`` -- the one
    the message is read in."""
    written = f"{day.month}월 {day.day}일 ({_WEEKDAYS[day.weekday()]})"
    return written if day.year == year else f"{day.year}년 {written}"


def sending_hours(now: datetime) -> bool:
    """Whether ``now`` is a time of day a reminder may be sent, in Korea."""
    return SEND_FROM <= now.astimezone(KST).time() < SEND_UNTIL


def kind_for(due: date, today: date) -> str | None:
    """Which reminder an item due on ``due`` is owed on ``today``, if any."""
    days_left = (due - today).days
    if days_left == 1:
        return DUE_SOON
    if -OVERDUE_DAYS <= days_left <= -1:
        return OVERDUE
    return None


def slack_escape(text: str) -> str:
    """Slack's three control characters as entities, so text reads as text.

    A description is what a person typed or a model wrote from speech, and a
    meeting's title is a person's: ``<!channel>`` or ``<https://x|여기>`` in
    either must not go out under the bot's name as a mention or a disguised
    link (review of #751; the same three ``autune_intelligence`` escapes for
    a report body, #642)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_due_reminder(
    kind: str, *, description: str, due_date: date, meeting_title: str | None, board_url: str
) -> str:
    """The message, as plain text: what it is, when it was due, where to act."""
    where = f" · 회의: {slack_escape(meeting_title)}" if meeting_title else ""
    return "\n".join(
        [
            _OPENING[kind],
            f"• {slack_escape(description)}",
            f"기한: {due_date.isoformat()}{where}",
            board_url,
        ]
    )


# --- the weekly digest (the user, 2026-10-04) ----------------------------------

DIGEST_WEEKDAYS = 5
"""The week's digest goes on its first working day: Monday, in Korea -- or,
when Monday is a public holiday, the first of Tuesday to Friday that is not
(the user, 2026-10-05: "공휴일이 아닌 업무일에 요약"). A week with no working
day has none."""

DIGEST_MAX_LINES = 10
"""Items a digest lists; the rest are counted, and the board has them all."""


def _no_holidays(_day: date) -> bool:
    return False


def digest_day(week: date, is_holiday: Callable[[date], bool] = _no_holidays) -> date | None:
    """The day the digest of the week starting on Monday ``week`` goes: that
    week's first Monday-to-Friday that is not a public holiday. ``None`` when
    all five are."""
    for offset in range(DIGEST_WEEKDAYS):
        day = week + timedelta(days=offset)
        if not is_holiday(day):
            return day
    return None


def digest_week(now: datetime, is_holiday: Callable[[date], bool] = _no_holidays) -> date | None:
    """The Monday of the week a digest sent at ``now`` is for -- or ``None``
    when ``now`` is not the sending hours, in Korea, of that week's digest day
    (``digest_day``), and nothing goes. The week is named by its Monday
    whichever day its digest goes on, so a week has one digest and not two."""
    if not sending_hours(now):
        return None
    today = korean_day(now)
    week = today - timedelta(days=today.weekday())
    return week if digest_day(week, is_holiday) == today else None


@dataclass(frozen=True)
class DigestLine:
    description: str
    due_date: date | None
    meeting_title: str | None
    idle_days: int | None = None
    """On a line of the morning DM's "그대로" group: whole days since the item
    was last touched. ``None`` everywhere else."""


def build_weekly_digest(lines: Sequence[DigestLine], *, today: date, board_url: str) -> str:
    """The digest as plain text: what is late, what is due this week, the rest
    -- most urgent first, at most ``DIGEST_MAX_LINES``, and where to see all."""
    week_end = today + timedelta(days=6)

    def order(line: DigestLine) -> tuple[int, date]:
        if line.due_date is None:
            return (2, date.max)
        return (0 if line.due_date < today else 1, line.due_date)

    ordered = sorted(lines, key=order)
    out = [f"이번 주 열린 액션 아이템 {len(ordered)}개입니다."]
    for line in ordered[:DIGEST_MAX_LINES]:
        when = ""
        if line.due_date is not None:
            if line.due_date < today:
                when = f" · 기한 지남({line.due_date.isoformat()})"
            elif line.due_date <= week_end:
                when = f" · 이번 주 {line.due_date.isoformat()}"
            else:
                when = f" · 기한 {line.due_date.isoformat()}"
        where = f" · {slack_escape(line.meeting_title)}" if line.meeting_title else ""
        out.append(f"• {slack_escape(line.description)}{when}{where}")
    if len(ordered) > DIGEST_MAX_LINES:
        out.append(f"외 {len(ordered) - DIGEST_MAX_LINES}개")
    out.append(board_url)
    return "\n".join(out)


# --- the morning DM (the user, 2026-10-05) -------------------------------------

DAILY_WEEKDAYS = (1, 2, 3, 4)
"""Tuesday to Friday, in Korea. Monday is the weekly digest's -- the whole
week's open work -- and nobody is sent both; nothing goes on a weekend."""

DAILY_FROM = time(9, 0)
DAILY_UNTIL = time(12, 0)
"""Korea time. A morning message goes in the morning: a worker that was down
until the afternoon does not send "good morning" at four."""

DAILY_MAX_LINES = 5
"""Items listed under each heading; the rest are counted."""

STALLED_AFTER_DAYS = 5
"""An open item nobody has touched for this many days or more is named in its
holder's morning DM with how long it has stood (the user, 2026-10-07: "5일 넘게
변화 없는 일") -- when it is in progress or has no due date; one not started
and not yet due is left in the count. Days on the calendar, weekends included:
a constant, not a setting -- nothing was asked for per team."""

DAILY_LOOKBACK = timedelta(days=7)
"""How far back "since the last one" may reach, for someone whose last morning
DM was long ago or never: a week of changes, not a history."""


def daily_day(now: datetime) -> date | None:
    """The day a morning DM sent at ``now`` is for -- or ``None`` when ``now``
    is not a Tuesday-to-Friday morning in Korea, and nothing goes."""
    local = now.astimezone(KST)
    if not DAILY_FROM <= local.time() < DAILY_UNTIL:
        return None
    return local.date() if local.weekday() in DAILY_WEEKDAYS else None


def previous_morning(day: date) -> datetime:
    """When "yesterday" starts for a morning DM on ``day`` with no earlier one
    to count from: the start of the day before, in Korea."""
    return datetime.combine(day - timedelta(days=1), time.min, tzinfo=KST)


@dataclass(frozen=True)
class DailyDigest:
    """What one person's morning DM says, already chosen and ordered.

    ``done``, ``closed`` and ``taken_on`` are what changed since their last
    morning DM -- ``closed`` is what was closed without being finished, which
    is never in ``done``;
    ``late``, ``due_today``, ``stalled`` and ``in_progress`` are today's work,
    each item in the first of the four it fits; ``others`` counts their
    remaining open items, which the board lists. ``stalled`` is what is in
    progress or undated and has not been touched for ``STALLED_AFTER_DAYS`` or
    more, longest first, each line carrying its ``idle_days``."""

    done: Sequence[DigestLine] = ()
    closed: Sequence[DigestLine] = ()
    taken_on: Sequence[DigestLine] = ()
    late: Sequence[DigestLine] = ()
    due_today: Sequence[DigestLine] = ()
    stalled: Sequence[DigestLine] = ()
    in_progress: Sequence[DigestLine] = ()
    others: int = 0

    @property
    def empty(self) -> bool:
        """Nothing changed and nothing is open: there is no message to send."""
        return not (
            self.done
            or self.closed
            or self.taken_on
            or self.late
            or self.due_today
            or self.stalled
            or self.in_progress
            or self.others
        )


def _daily_lines(label: str, lines: Sequence[DigestLine], *, dated: bool) -> list[str]:
    out: list[str] = []
    for line in lines[:DAILY_MAX_LINES]:
        when = f"({line.due_date.isoformat()})" if dated and line.due_date is not None else ""
        where = f" · {slack_escape(line.meeting_title)}" if line.meeting_title else ""
        out.append(f"• {label}{when}: {slack_escape(line.description)}{where}")
    if len(lines) > DAILY_MAX_LINES:
        out.append(f"• {label} 외 {len(lines) - DAILY_MAX_LINES}개")
    return out


def _stalled_lines(lines: Sequence[DigestLine]) -> list[str]:
    """How long each has stood, in its own words: the label is the count."""
    out: list[str] = []
    for line in lines[:DAILY_MAX_LINES]:
        where = f" · {slack_escape(line.meeting_title)}" if line.meeting_title else ""
        out.append(f"• {line.idle_days}일째 그대로: {slack_escape(line.description)}{where}")
    if len(lines) > DAILY_MAX_LINES:
        out.append(f"• 그대로인 일 외 {len(lines) - DAILY_MAX_LINES}개")
    return out


def build_daily_digest(digest: DailyDigest, *, board_url: str) -> str:
    """The morning DM as plain text: what changed since the last one, then
    today's work -- late first -- then how many other items are open, and
    where to see all of them."""
    out = ["좋은 아침입니다. 지난 진행 상황과 오늘 할 일입니다.", "지난 진행 상황"]
    changed = _daily_lines("완료", digest.done, dated=False)
    changed += _daily_lines("끝내지 않고 닫힘", digest.closed, dated=False)
    changed += _daily_lines("새로 맡음", digest.taken_on, dated=False)
    out += changed or ["• 바뀐 것이 없습니다."]
    out.append("오늘 할 일")
    today = _daily_lines("기한 지남", digest.late, dated=True)
    today += _daily_lines("오늘 기한", digest.due_today, dated=False)
    today += _stalled_lines(digest.stalled)
    today += _daily_lines("진행 중", digest.in_progress, dated=False)
    out += today or ["• 오늘 기한이거나 진행 중인 항목이 없습니다."]
    if digest.others:
        out.append(f"그 밖의 열린 액션 아이템 {digest.others}개")
    out.append(board_url)
    return "\n".join(out)
