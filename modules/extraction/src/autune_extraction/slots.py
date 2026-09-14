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
from collections.abc import Callable, Collection
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


def assignee_of(speaker_id: str | None, speaker: str, *, known: Collection[str]) -> Assignee:
    """The speaker made the commitment, so the speaker is who it is for.

    An identified speaker is an account and goes in ``assignee_id``. An
    unidentified one -- "Speaker 2" -- keeps only the label, which is what
    ``assignee_label`` is for, and the item waits for a person to say who that
    was.

    ``known`` is the set of ids that exist in ``users``; the caller reads it.
    ``assignee_id`` is a foreign key, so an id that is not there -- a deleted
    account, or a participant id in the wrong field -- would fail the insert
    and lose every item of the meeting with it. Such a speaker is treated as
    unidentified, which loses only the link. The ``user_`` prefix alone did not
    promise that: a well-formed id of a deleted user passed it.
    """
    if speaker_id is not None and has_prefix(speaker_id, USER) and speaker_id in known:
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


RECENT_PAST = timedelta(days=90)
"""How far back a named month/day is read as the past rather than next year."""


def _next(day: date, month: int, dom: int, *, by: str | None) -> date:
    """This year's month/day; next year's only when it is well past and a
    deadline word says it is due.

    A date a few weeks back -- "9월 1일에 공유드렸고" said on the 9th -- is the
    past, and is returned as such so ``parse_due`` skips it. Six months back
    with a deadline word -- "3월 2일까지" said in September -- is next year's.

    Six months back **without** one is still the past. "6월 1일 자료 기준으로
    정리하겠습니다" names the day something happened, and rolling it forward
    would invent a deadline a year out (review of #159). The cost is the rare
    "3월 2일에 드리겠습니다" said in September, which gets no date -- a missing
    date on a draft card rather than a wrong one.
    """
    candidate = date(day.year, month, dom)
    if candidate >= day or by is None or day - candidate <= RECENT_PAST:
        return candidate
    return date(day.year + 1, month, dom)


def _day_of_month(day: date, dom: int) -> date:
    """This month's day, or next month's once it has passed."""
    if dom >= day.day:
        return date(day.year, day.month, dom)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, dom)


Resolver = Callable[[re.Match[str], date | None], date | None]

_BY = r"전까지|까지|전에|내로|내에|안에|이내|중으로|중에|중(?![가-힣])|쯤"
"""Words that make the date before them a deadline: 까지, 안에, 중으로, ..."""

_DEADLINE_WORD = re.compile(rf"\s*(?:{_BY})")
_CLAUSE_END = re.compile(r"[,.?!\n]|(?:고|는데|은데|지만|니까|어서|아서|해서|면서|며)(?=\s|$)")
_NOT_PAST = frozenset("겠있없")
"""Syllables ending in ㅆ that are not the past tense: the future -겠-, 있다, 없다."""


def _said_of_the_past(text: str, end: int, stop: int) -> bool:
    """Whether the clause after a date phrase is in the past tense.

    "6월 1일에 이미 전달드렸는데" and "월요일에 말씀드렸던 거" name the day
    something happened or was talked about, not the day anything is due
    (review of #159). The clause runs from the phrase to the first clause
    ending, comma or next date phrase, and it is past when a syllable carries
    the past tense's final ㅆ (-았/었/였-, 했, 렸) -- other than -겠- and 있/없 --
    or the retrospective -던.

    A deadline word straight after the phrase settles it the other way:
    "금요일까지 지난번에 말씀드렸던 거 드리겠습니다" is due Friday.

    Not read: the past adnominal -(으)ㄴ, "월요일에 말씀드린 거". It is spelled
    like an adjective's present -- "월요일에 필요한 거" -- and a rule that
    skipped both would drop real deadlines to catch past mentions.
    """
    if _DEADLINE_WORD.match(text, end):
        return False
    boundary = _CLAUSE_END.search(text, end, stop)
    clause = text[end : boundary.end() if boundary else stop]
    return "던" in clause or any(
        0 <= ord(ch) - 0xAC00 < 11172 and (ord(ch) - 0xAC00) % 28 == 20 and ch not in _NOT_PAST
        for ch in clause
    )


def _needs_day(resolve: Callable[[re.Match[str], date], date]) -> Resolver:
    """Relative phrases have no date without the meeting's day."""
    return lambda match, day: None if day is None else resolve(match, day)


_PHRASES: tuple[tuple[re.Pattern[str], Resolver], ...] = (
    # 2026-10-01, 2026.10.01 -- absolute, so no meeting day is needed.
    (
        re.compile(r"(?<!\d)(?P<y>20\d{2})[-./](?P<m>\d{1,2})[-./](?P<d>\d{1,2})(?!\d)"),
        lambda m, _: date(int(m["y"]), int(m["m"]), int(m["d"])),
    ),
    # 9월 20일 -- ``by`` is the deadline word after it, when there is one.
    (
        re.compile(rf"(?<!\d)(?P<m>\d{{1,2}})\s*월\s*(?P<d>\d{{1,2}})\s*일(?=\s*(?P<by>{_BY})?)"),
        _needs_day(lambda m, day: _next(day, int(m["m"]), int(m["d"]), by=m["by"])),
    ),
    # 9/20까지 -- only with a deadline word or 에 after it; "1/3 정도" is a fraction.
    (
        re.compile(
            rf"(?<![\d/])(?P<m>\d{{1,2}})/(?P<d>\d{{1,2}})(?![\d/])(?=\s*(?:(?P<by>{_BY})|에))"
        ),
        _needs_day(lambda m, day: _next(day, int(m["m"]), int(m["d"]), by=m["by"])),
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
    # 15일까지 -- a day of this month, or next month's once it has passed. Not
    # "3일 전": that is three days ago, not the third.
    (
        re.compile(r"(?<![\d월/])(?P<d>\d{1,2})\s*일(?=\s*(?:까지|에|중|쯤))"),
        _needs_day(lambda m, day: _day_of_month(day, int(m["d"]))),
    ),
    # 2주 뒤, 일주일 안에, 이틀 뒤
    (
        re.compile(r"(?<!\d)(?P<n>\d{1,2})\s*주\s*(?:후|뒤|안에|이내|내로|내에)"),
        _needs_day(lambda m, day: day + timedelta(weeks=int(m["n"]))),
    ),
    # Only with a word that makes them a deadline: "일주일에 한 번" is a
    # frequency and "이틀 전" is the past.
    (
        re.compile(r"일주일\s*(?:후|뒤|안에|이내|내로|내에)"),
        _needs_day(lambda _, day: day + timedelta(weeks=1)),
    ),
    (
        re.compile(r"이틀\s*(?:후|뒤|안에|이내|내로|내에)"),
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
    """The first date phrase in ``text`` that is a deadline, not the past.

    ``None`` when the utterance names no such date. When it names more than
    one -- "다음 주 금요일까지 하고 월요일에 공유" -- the first is taken: the
    rest is usually what happens after the thing promised.

    **What is said of the past is skipped**, and the next phrase is tried. A
    phrase is the past in two ways:

    - It resolves before the meeting's day. "이번 주 월요일에 말씀드린" said on
      a Wednesday is what already happened; so is "이번 주" said on a Saturday,
      whose working week has ended. A card due before it was promised reads as
      overdue from the moment it exists.
    - The clause after it is in the past tense (``_said_of_the_past``). "6월
      1일에 이미 전달드렸는데" and "월요일에 말씀드렸던 거" resolve to days
      after the meeting if read forward, but they name when something was sent
      or discussed, not when anything is due (review of #159). A deadline word
      right after the phrase overrides the tense.

    Where two patterns match at the same place the longer wins, so "다음 주
    금요일" is one phrase and not "다음 주" followed by a weekday -- and a
    skipped phrase takes its parts with it, so its "금요일" is not tried alone.

    A phrase with no resolvable day (no meeting day, or "2월 30일") cannot be
    judged past or not, and is returned with its words and no date.
    """
    found: list[tuple[int, int, re.Match[str], Resolver]] = []
    for pattern, resolve in _PHRASES:
        for match in pattern.finditer(text):
            found.append((match.start(), -(match.end() - match.start()), match, resolve))

    taken_until = -1
    for start, _, match, resolve in sorted(found, key=lambda entry: (entry[0], entry[1])):
        if start < taken_until:
            continue  # part of a longer phrase already considered
        taken_until = match.end()
        stop = min((other for other, *_ in found if other >= match.end()), default=len(text))
        if _said_of_the_past(text, match.end(), stop):
            continue
        try:
            resolved = resolve(match, day)
        except ValueError:
            # "2월 30일": the phrase is a date, the day does not exist.
            resolved = None
        if resolved is not None and day is not None and resolved < day:
            continue
        return DueDate(text=re.sub(r"\s+", " ", match[0]).strip(), date=resolved)
    return None
