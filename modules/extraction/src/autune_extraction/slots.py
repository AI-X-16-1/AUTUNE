"""Step 3: who promised it and by when, read from one commitment utterance.

The minimal version (#11). Rules, not a model: the speaker of a commitment is
who made it, and a due date is a Korean date phrase resolved against the day the
meeting was held. Both are arithmetic over one utterance and its meeting, so
this lives beside ``decisions`` rather than in ``pipeline`` (which loads models)
or ``service`` (which holds the session).

What this does not do yet, on purpose:

- **"저희 팀이", "민경님이"** -- an assignee other than the speaker. That needs
  the surrounding utterances (step 2, reference resolution), and guessing it
  from one sentence would put a person's name on a card they never agreed to.
- **A description other than the utterance.** The card quotes what was said,
  as decisions do (``decisions._build``). A rewritten sentence would be wrong
  in a way the reader cannot see.

Every value it cannot settle is left empty and the item stays in *needs
confirmation*, which is the state the board already shows for exactly this
(``ui-spec.md`` S17). Leaving a slot empty costs a person a click; filling it
wrongly costs them noticing.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from autune_core.ids import USER, has_prefix

KST = timezone(timedelta(hours=9), "KST")
"""The day a phrase like "내일" means is the day in Korea. There is no team time
zone to read yet, and Korea keeps no daylight saving, so a fixed offset is
exact rather than an approximation of ``Asia/Seoul``."""

WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
FRIDAY, SUNDAY = 4, 6


@dataclass(frozen=True)
class DueDate:
    """A due date and the words it was read from.

    ``text`` is kept because S18 shows "the raw text the due date was parsed
    from": a reader who sees 9월 18일 next to "다음 주 금요일" can check the
    arithmetic without replaying the meeting. It is a fragment of the masked
    utterance, never anything more.

    ``date`` is ``None`` when the phrase is clear but its day is not -- "금요일"
    in a meeting whose start time nobody recorded, or "2월 30일".
    """

    text: str
    date: date | None


@dataclass(frozen=True)
class Assignee:
    """Who a card is for: an account, or only the label the transcript had."""

    user_id: str | None
    label: str | None


def assignee_of(speaker_id: str | None, speaker: str) -> Assignee:
    """The speaker made the commitment, so the speaker is who it is for.

    An identified speaker is an account and goes in ``assignee_id``. An
    unidentified one -- "Speaker 2" -- keeps only the label, which is what
    ``assignee_label`` is for, and the item waits for a person to say who that
    was.

    Only an id with the ``user_`` prefix is taken as an account.
    ``assignee_id`` is a foreign key to ``users``, and anything else arriving
    in ``speaker_id`` would fail that insert and lose the meeting's items with
    it; treating it as unidentified loses only the link.
    """
    if speaker_id is not None and has_prefix(speaker_id, USER):
        return Assignee(user_id=speaker_id, label=None)
    return Assignee(user_id=None, label=speaker)


def meeting_day(started_at: datetime | None) -> date | None:
    """The day the meeting was held, in Korea. ``None`` if nobody recorded it.

    Relative phrases resolve against this and nothing else. The upload time is
    not the meeting time -- a recording uploaded on Monday of a Friday meeting
    would move every "내일" by three days -- so a meeting with no start time
    gets no relative dates, and keeps the phrase for a person to read.
    """
    if started_at is None:
        return None
    moment = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    return moment.astimezone(KST).date()


# --- the phrases --------------------------------------------------------------

_WEEK = r"(?P<week>이번\s*주|다다음\s*주|다음\s*주|차주|담주)"
_WEEK_OFFSET = {"이번": 0, "다다음": 2, "다음": 1, "차주": 1, "담주": 1}


def _week_offset(word: str) -> int:
    squashed = re.sub(r"\s+", "", word)
    for prefix, offset in _WEEK_OFFSET.items():
        if squashed.startswith(prefix):
            return offset
    raise ValueError(word)  # unreachable: the pattern only matches the keys


def _monday(day: date, weeks: int = 0) -> date:
    return day - timedelta(days=day.weekday()) + timedelta(weeks=weeks)


def _month_end(day: date, months: int = 0) -> date:
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _next(day: date, month: int, dom: int) -> date:
    """This year's month/day, or next year's once it has passed."""
    candidate = date(day.year, month, dom)
    return candidate if candidate >= day else date(day.year + 1, month, dom)


def _day_of_month(day: date, dom: int) -> date:
    """This month's day, or next month's once it has passed."""
    if dom >= day.day:
        return date(day.year, day.month, dom)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, dom)


Resolver = Callable[[re.Match[str], date | None], date | None]


def _needs_day(resolve: Callable[[re.Match[str], date], date]) -> Resolver:
    """Relative phrases have no date without the meeting's day."""
    return lambda match, day: None if day is None else resolve(match, day)


_PHRASES: tuple[tuple[re.Pattern[str], Resolver], ...] = (
    # 2026-10-01, 2026.10.01 -- absolute, so no meeting day is needed.
    (
        re.compile(r"(?<!\d)(?P<y>20\d{2})[-./](?P<m>\d{1,2})[-./](?P<d>\d{1,2})(?!\d)"),
        lambda m, _: date(int(m["y"]), int(m["m"]), int(m["d"])),
    ),
    # 9월 20일
    (
        re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
        _needs_day(lambda m, day: _next(day, int(m["m"]), int(m["d"]))),
    ),
    # 9/20
    (
        re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/])"),
        _needs_day(lambda m, day: _next(day, int(m["m"]), int(m["d"]))),
    ),
    # 다음 달 3일
    (
        re.compile(r"다음\s*달\s*(?P<d>\d{1,2})\s*일"),
        _needs_day(lambda m, day: _month_end(day, 1).replace(day=int(m["d"]))),
    ),
    # 월말, 이번 달 말, 이달 말 / 다음 달 말
    (
        re.compile(r"월말|(?:이번\s*달|이달)\s*말"),
        _needs_day(lambda _, day: _month_end(day)),
    ),
    (
        re.compile(r"다음\s*달\s*말"),
        _needs_day(lambda _, day: _month_end(day, 1)),
    ),
    # 3일 후, 3일 안에 -- before the bare day below, which would read "3일".
    (
        re.compile(r"(?<!\d)(?P<n>\d{1,2})\s*일\s*(?:후|뒤|안에|이내|내로|내에)"),
        _needs_day(lambda m, day: day + timedelta(days=int(m["n"]))),
    ),
    # 15일까지 -- a day of this month, or next month's once it has passed.
    (
        re.compile(r"(?<![\d월/])(?P<d>\d{1,2})\s*일(?=\s*(?:까지|에|전|중|쯤))"),
        _needs_day(lambda m, day: _day_of_month(day, int(m["d"]))),
    ),
    # 2주 뒤, 일주일 안에, 이틀 뒤
    (
        re.compile(r"(?<!\d)(?P<n>\d{1,2})\s*주\s*(?:후|뒤|안에|이내|내로|내에)"),
        _needs_day(lambda m, day: day + timedelta(weeks=int(m["n"]))),
    ),
    (
        re.compile(r"일주일"),
        _needs_day(lambda _, day: day + timedelta(weeks=1)),
    ),
    (
        re.compile(r"이틀"),
        _needs_day(lambda _, day: day + timedelta(days=2)),
    ),
    # 오늘, 내일, 모레, 글피
    (
        re.compile(r"오늘|내일|모레|글피"),
        _needs_day(
            lambda m, day: day + timedelta(days={"오늘": 0, "내일": 1, "모레": 2, "글피": 3}[m[0]])
        ),
    ),
    # 다음 주 화요일
    (
        re.compile(_WEEK + r"\s*(?P<wd>[월화수목금토일])요일"),
        _needs_day(
            lambda m, day: _monday(day, _week_offset(m["week"])) + timedelta(days=WEEKDAYS[m["wd"]])
        ),
    ),
    # 이번 주말, 다음 주말, 주말 -- one 주, so not the week pattern plus 말.
    (
        re.compile(r"(?:(?P<week>이번|다다음|다음)\s*)?주말"),
        _needs_day(
            lambda m, day: (
                _monday(day, _week_offset(m["week"]) if m["week"] else 0) + timedelta(days=SUNDAY)
            )
        ),
    ),
    # 이번 주, 다음 주 -- with no day named, the working week's end.
    (
        re.compile(_WEEK + r"(?!\s*[월화수목금토일]요일)(?!\s*말)"),
        _needs_day(lambda m, day: _monday(day, _week_offset(m["week"])) + timedelta(days=FRIDAY)),
    ),
    # 금요일 -- the next one after the meeting. Said on a Friday, it means the
    # following Friday: "today" has a word of its own.
    (
        re.compile(r"(?P<wd>[월화수목금토일])요일"),
        _needs_day(
            lambda m, day: day + timedelta(days=(WEEKDAYS[m["wd"]] - day.weekday() - 1) % 7 + 1)
        ),
    ),
)


def parse_due(text: str, day: date | None) -> DueDate | None:
    """The first date phrase in ``text``, resolved against the meeting's day.

    ``None`` when the utterance names no date at all. When it names more than
    one -- "다음 주 금요일까지 하고 월요일에 공유" -- the first is taken: the
    rest is usually what happens after the thing promised.

    Where two patterns match at the same place the longer wins, so "다음 주
    금요일" is one phrase and not "다음 주" followed by a weekday.
    """
    found: list[tuple[int, int, re.Match[str], Resolver]] = []
    for pattern, resolve in _PHRASES:
        for match in pattern.finditer(text):
            found.append((match.start(), -(match.end() - match.start()), match, resolve))
    if not found:
        return None

    _, _, match, resolve = min(found, key=lambda entry: (entry[0], entry[1]))
    try:
        resolved = resolve(match, day)
    except ValueError:
        # "2월 30일": the phrase is a date, the day does not exist. Kept as said.
        resolved = None
    return DueDate(text=re.sub(r"\s+", " ", match[0]).strip(), date=resolved)
