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
_WEEK_PART = {"초": 1, "초반": 1, "중반": 3}
"""Part of a working week, as the last weekday it covers. The team's reading:
초 is Monday-Tuesday, 중반 Wednesday-Thursday, 말 Friday, and a week with no
part Monday-Friday. A deadline said as a span is due by the span's end, the
same reading 주말 (Sunday) and 월말 already get -- so 말 and the bare week are
both Friday, below."""


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

    **A candidate fix was tried and rejected (#197).** Rolling forward
    whenever "에" follows the date directly and the clause after it carries a
    future marker (-겠-, -ㄹ게요, 드릴) does fix "3월 2일에 드리겠습니다"
    without breaking "6월 1일 자료 기준으로" (no "에" there at all) -- but it
    also turns "6월 1일에 나온 이슈 정리하겠습니다" into a wrong 2027-06-01.
    "나온" is a past adnominal this module has no general way to read as past
    (``_said_of_the_past`` only knows four such verbs, #197's #2, precisely
    because a syllable-level rule cannot tell a verb's past from an
    adjective's present) -- so "에 + future marker" catches real deadlines
    and misdated pasts alike whenever the past marker is a verb outside that
    short list. Confirmed empirically, not just reasoned through; the eval
    set #197 asks for before any rule change here is what would actually
    measure how often each case shows up in real speech.
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


def _period_end(day: date | None, month: int, *, by: str | None, year: int | None) -> date | None:
    """The last day of ``month``: in ``year`` when one was said, otherwise this
    year's -- or next year's under ``_next``'s rule, only when it is well past
    and a deadline word says it is due.

    A month, a half or a quarter is due by its end: "10월까지", "하반기에",
    "3분기 안에" all leave until the period's last day, and a card has to carry
    one date. A named year needs no meeting day; anything else does.
    """
    if year is not None:
        return date(year, month, calendar.monthrange(year, month)[1])
    if day is None:
        return None
    candidate = date(day.year, month, calendar.monthrange(day.year, month)[1])
    if candidate >= day or by is None or day - candidate <= RECENT_PAST:
        return candidate
    return date(day.year + 1, month, calendar.monthrange(day.year + 1, month)[1])


def _named_year(m: re.Match[str], day: date | None) -> int | None:
    """``2027년`` as said, ``내년`` from the meeting's day, otherwise none."""
    if m["y"]:
        return int(m["y"])
    if m["rel"] == "내년":
        return None if day is None else day.year + 1
    if m["rel"] == "올해":
        return None if day is None else day.year
    return None


def _period(month: Callable[[re.Match[str]], int]) -> Resolver:
    """A resolver for a month, half or quarter phrase, by its last day."""

    def resolve(m: re.Match[str], day: date | None) -> date | None:
        if m["rel"] and day is None:
            return None  # 내년/올해 need the meeting's day
        return _period_end(day, month(m), by=m["by"], year=_named_year(m, day))

    return resolve


Resolver = Callable[[re.Match[str], date | None], date | None]

_BY = r"전까지|까지|전에|내로|내에|안에|이내|중으로|중에|중(?![가-힣])|쯤"
"""Words that make the date before them a deadline: 까지, 안에, 중으로, ..."""

_YEAR = r"(?:(?<!\d)(?P<y>20\d{2})\s*년\s*|(?P<rel>올해|내년)\s*)?"
"""An optional year before a month, half or quarter: 2027년, 올해, 내년."""
_PERIOD_BY = rf"(?=\s*(?P<by>{_BY})?)"
_PERIOD_BY_REQUIRED = rf"(?=\s*(?:(?P<by>{_BY})|에|말(?!씀)))"
"""A month or a period is a deadline only when something after it says so."""

_DEADLINE_WORD = re.compile(rf"\s*(?:{_BY})")
_CLAUSE_END = re.compile(r"[,.?!\n]|(?:고|는데|은데|지만|니까|어서|아서|해서|면서|며)(?=\s|$)")
_NOT_PAST = frozenset("겠있없")
"""Syllables ending in ㅆ that are not the past tense: the future -겠-, 있다, 없다."""

_AGREED = re.compile(r"기로|[는할]\s*걸로|도록|자고")
"""What turns a past verb into an agreement about the date: 하기로 했다,
드리는 걸로 했다, 끝내도록 했다, 하자고 했다."""

_PAST_ADNOMINAL_VERBS = re.compile(
    r"(?:말씀드린|공유한|보낸|전달한)(?=\s*(?:거|것|건|걸|게|대로|내용|자료|파일|부분))"
)
"""The past adnominal -(으)ㄴ, but only for these four reporting verbs (#197's
own candidate list), and only before a noun that takes a past mention.

-(으)ㄴ is past on a verb ("말씀드린" = said) and present on an adjective
("필요한" = necessary) -- the same spelling, different tense, and nothing
about the syllable itself says which. A general check would misread every
"필요한 거" as the past and drop a real deadline behind it. Naming the exact
past-adnominal form of a small, closed set of verbs this module already
expects in a commitment ("말씀드리다, 공유하다, 보내다, 전달하다" -- what a
promise names having already been discussed or sent) is precise where a
syllable rule cannot be; it answers nothing about a verb not on this list,
and adding one is a data decision (#197's own eval-set plan), not a pattern
someone noticed.

**What may follow is an allow-list, not a deny-list.** Each form is also the
start of things that are not a reported past: the -ㄴ다 conjugation
("공유한다면", "보낸다고", contracted "보낸대요", formal "보낸답니다", "보낸단다")
and a relative past before a later event ("공유한 후/뒤/다음/이후", "보낸
직후" -- past relative to what follows, so "월요일에 자료 공유한 이후에 피드백
주세요" is still due after Monday). Three review rounds on #333 (lsh2217)
each found another member of that open set, because "not followed by X" can
never list every X. So the form counts only when a noun that receives a past
mention follows it -- "말씀드린 거/대로", "공유한 자료", "보낸 파일", "전달한
내용". Anything else reads exactly as it does without this pattern, so a
phrase outside the list cannot regress; growing the noun list is the same
data question as growing the verb list."""


def _past_syllable(ch: str) -> bool:
    code = ord(ch) - 0xAC00
    return ch == "던" or (0 <= code < 11172 and code % 28 == 20 and ch not in _NOT_PAST)


def _said_of_the_past(text: str, end: int, stop: int) -> bool:
    """Whether the clause after a date phrase is in the past tense.

    "6월 1일에 이미 전달드렸는데" and "월요일에 말씀드렸던 거" name the day
    something happened or was talked about, not the day anything is due
    (review of #159). The clause runs from the phrase to the first clause
    ending, comma or next date phrase, and it is past when a syllable carries
    the past tense's final ㅆ (-았/었/였-, 했, 렸) -- other than -겠- and 있/없 --
    the retrospective -던, or one of ``_PAST_ADNOMINAL_VERBS``' exact forms
    (#197 -- "월요일에 말씀드린 거" is now read as past the same way "월요일에
    말씀드렸던 거" already was; a verb not on that list still is not).

    Two things settle it the other way:

    - A deadline word straight after the phrase: "금요일까지 지난번에
      말씀드렸던 거 드리겠습니다" is due Friday.
    - An agreement marker before the first past marker. In "금요일에
      하기로 했습니다" the 했 dates the agreement, not the work -- Friday is
      the deadline. The classifier reads "-기로 했" as a decision for the same
      reason. Order matters: "월요일에 공유했던 거 하기로 했습니다" and
      "월요일에 말씀드렸던 걸로" put the past verb first, so Monday is still
      what happened (review by mkkim68).
    """
    if _DEADLINE_WORD.match(text, end):
        return False
    boundary = _CLAUSE_END.search(text, end, stop)
    clause = text[end : boundary.end() if boundary else stop]
    syllable = next((i for i, ch in enumerate(clause) if _past_syllable(ch)), None)
    adnominal = _PAST_ADNOMINAL_VERBS.search(clause)
    candidates = [i for i in (syllable, adnominal.start() if adnominal else None) if i is not None]
    if not candidates:
        return False
    past = min(candidates)
    agreed = _AGREED.search(clause)
    return agreed is None or agreed.start() > past


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
    # 이번 달까지, 다음 달 중으로, 다음 달에 -- the month by its last day. Only
    # with a deadline word or 에 after it: "다음 달 일정" is not a deadline.
    (
        re.compile(rf"(?:이번\s*달|이달)(?=\s*(?:{_BY}|에))"),
        _needs_day(lambda _, day: _month_end(day)),
    ),
    (
        re.compile(rf"다음\s*달(?!\s*\d)(?!\s*말)(?=\s*(?:{_BY}|에))"),
        _needs_day(lambda _, day: _month_end(day, 1)),
    ),
    # 10월 말, 2027년 3월말 -- a named month's last day. Not "10월 말씀드린".
    (
        re.compile(_YEAR + r"(?<![\d/])(?P<m>\d{1,2})\s*월\s*말(?!씀)" + _PERIOD_BY),
        _period(lambda m: int(m["m"])),
    ),
    # 10월까지, 11월 중으로, 11월에 -- a named month with no day, by its last
    # day. Only with a deadline word or 에: "5월 자료 기준" names a month, not
    # a deadline. A past month said with 에 resolves to the past and is
    # skipped like any other past phrase (#197, the bare-month misses).
    (
        re.compile(
            _YEAR + r"(?<![\d/])(?P<m>\d{1,2})\s*월(?!\s*\d)(?!\s*말)" + _PERIOD_BY_REQUIRED
        ),
        _period(lambda m: int(m["m"])),
    ),
    # 연말, 연내, 올해 안에, 올해까지 -- this year's last day.
    (
        re.compile(rf"연말|연내|올해\s*(?:말(?!씀)|안|중|내)|올해(?=\s*(?:{_BY}))"),
        _needs_day(lambda _, day: date(day.year, 12, 31)),
    ),
    # 하반기에, 2027년 상반기까지, 내년 상반기 중 -- a half by its last day.
    (
        re.compile(_YEAR + r"(?P<h>상반기|하반기)" + _PERIOD_BY_REQUIRED),
        _period(lambda m: 6 if m["h"] == "상반기" else 12),
    ),
    # 3분기 안에, 내년 1분기까지 -- a quarter by its last day.
    (
        re.compile(_YEAR + r"(?<!\d)(?P<q>[1-4])\s*분기" + _PERIOD_BY_REQUIRED),
        _period(lambda m: int(m["q"]) * 3),
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
    # 다음 주 초, 다음 주초, 이번 주 중반 -- part of a week, by its last day.
    # Before the bare week below, which would read them as Friday. Not 초안
    # (a draft), 초과 or 초기, which start with the same syllable.
    (
        re.compile(_WEEK + r"\s*(?P<part>초반|초(?![안과기대청])|중반)"),
        _needs_day(
            lambda m, day: (
                _monday(day, _week_offset(m["week"])) + timedelta(days=_WEEK_PART[m["part"]])
            )
        ),
    ),
    # 다음 주 말 -- spaced, the working week's end: Friday. Written as one word,
    # 다음 주말 is the weekend and Sunday (above).
    (
        re.compile(_WEEK + r"\s+말(?!씀)"),
        _needs_day(lambda m, day: _monday(day, _week_offset(m["week"])) + timedelta(days=FRIDAY)),
    ),
    # 이번 주, 다음 주 -- with no day named, the working week's end. Not "다음
    # 주 말" (above), but "다음 주 말씀드릴게요" is a week and a verb.
    (
        re.compile(_WEEK + r"(?!\s*[월화수목금토일]요일)(?!\s*말(?!씀))"),
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


_CHOSEN = re.compile(r"\s*(?:으로|로)(?![가-힣])")
"""(으)로 right after a date: the date is what was chosen -- "화요일로
바꾸기로", "10월 20일로 정했습니다" -- not when something is due. Not 로부터 or
로서, which are other words."""
_EVERY_BEFORE = re.compile(r"(?:매주|매달|매월|매일|매년|격주|격월)\s*$")
_EVERY_AFTER = re.compile(r"\s*마다")
"""A day that comes round -- "매주 월요일에", "금요일마다" -- is a schedule."""


_STARTS = re.compile(r"\s*(?:부터|부로)")
"""부터 or 부로 right after a date: when something begins -- "QA는 10월
13일부터 시작하기로", "11월 1일부로 요금제를 바꾸기로". Nothing is due that
day. A range still has its deadline: in "13일부터 17일까지" the 17th is read."""

_STRETCH = r"(?:\d{1,2}\s*[일주]|일주일|이틀)\s*"
_OFFSET = re.compile(_STRETCH + r"(?:후|뒤)")
"""A stretch of time counted from something: "2주 뒤", "3일 후"."""
_WITHIN = re.compile(_STRETCH + r"(?:후|뒤|안에|이내|내로|내에)")
"""The same, or a stretch something is due within: "일주일 안에"."""
_WORD_BEFORE = re.compile(r"([가-힣A-Za-z]+)\s+$")
_PARTICLE_ENDS = tuple("은는이가을를도에서로만와과께터")
_NOT_AN_EVENT = frozenset(
    {"그럼", "그러면", "그리고", "그래서", "다시", "한", "약", "대략", "딱", "아마"}
    | {"일단", "우선", "지금", "오늘", "내일", "이제", "늦어도", "적어도"}
    | {"최소", "최대", "정확히", "대충", "거의"}
)
_FROM_THE_MEETING = frozenset({"지금부터", "오늘부터", "이제부터"})
"""A start that is the meeting itself: "오늘부터 일주일 안에" is counted from
the meeting's day, which is what ``parse_due`` counts from."""
"""Words that can stand right before "2주 뒤" without being what it is counted
from: the stretch is then counted from the meeting, as it is resolved."""

_BARE_WEEKDAY = re.compile(r"[월화수목금토일]요일")
_BY_AFTER_TIME = re.compile(
    rf"\s*(?:(?:오전|오후|아침|점심|저녁|밤|퇴근)\s*(?:\d{{1,2}}\s*시)?\s*)?(?:{_BY})"
)
"""A deadline word after a weekday, a time of day allowed in between: "금요일
오후까지"."""


def _counted_from_something_else(text: str, start: int, end: int) -> bool:
    """Whether the stretch at ``text[start:end]`` is counted from an event named
    right before it -- "베타 시작 2주 뒤에", "배포 3일 후에" -- and not from the
    meeting. ``parse_due`` can only count from the meeting's day, so the date it
    would give is another day altogether.

    The event is taken to be a word with no particle on it standing right
    before the stretch. A word that ends like a particle ("평가 2주 뒤") is
    not seen as one, and the stretch is then read as before.

    Also a stretch counted from a day that is named: "화요일로부터 일주일
    안에" is a week from Tuesday, and that Tuesday -- a start, skipped like any
    other -- is not the meeting's day."""
    before = _WORD_BEFORE.search(text, 0, start)
    if before is None:
        return False
    word = before.group(1)
    if word.endswith("부터"):
        return word not in _FROM_THE_MEETING and bool(_WITHIN.fullmatch(text, start, end))
    if not _OFFSET.fullmatch(text, start, end):
        return False
    return word not in _NOT_AN_EVENT and not word.endswith(_PARTICLE_ENDS)


def _what_was_decided(text: str, start: int, end: int) -> bool:
    """Whether the date phrase at ``text[start:end]`` is the content of a
    decision and not its deadline. Each sign is looked for right at the phrase,
    so a deadline word in between -- "금요일까지로" -- leaves it a deadline.

    - The date chosen (``_CHOSEN``) or a day that repeats (매주, 마다).
    - The day something starts (``_STARTS``).
    - A stretch counted from another event (``_counted_from_something_else``):
      the date would be wrong, not only the word for it.
    - A weekday said alone, with no week and no deadline word: "주간 보고는
      월요일 오전에 하기로" is a standing arrangement, and words cannot tell
      it from a single Friday. So "금요일에 배포하기로 했습니다" loses its
      date too; "금요일까지", "금요일 오후까지" and "다음 주 금요일" keep
      theirs.

    The last three since 2026-10-09 (module B's owner): of six decisions with a
    date after them in one invented run, three had a start, a stretch counted
    from the beta's start, and a weekly report's weekday as their deadline."""
    return bool(
        _CHOSEN.match(text, end)
        or _EVERY_AFTER.match(text, end)
        or _EVERY_BEFORE.search(text, 0, start)
        or _STARTS.match(text, end)
        or _counted_from_something_else(text, start, end)
        or (_BARE_WEEKDAY.fullmatch(text, start, end) and not _BY_AFTER_TIME.match(text, end))
    )


def past_form_at(text: str, index: int) -> bool:
    """Whether the syllable at ``index`` of ``text`` carries the past tense, as
    ``_said_of_the_past`` reads one: a final ㅆ other than 겠, 있 and 없, or 던."""
    return 0 <= index < len(text) and _past_syllable(text[index])


def dates_named(text: str) -> int:
    """How many dates ``text`` names. Two readings of the same words -- "10월
    17일" and the "17일" in it -- are one date."""
    spans = sorted(
        (match.start(), match.end()) for pattern, _ in _PHRASES for match in pattern.finditer(text)
    )
    count, reach = 0, -1
    for start, end in spans:
        if start >= reach:
            count += 1
        reach = max(reach, end)
    return count


def names_chosen_date(text: str) -> bool:
    """Whether ``text`` has a date phrase with (으)로 right after it: the date
    something was set to -- "10월 20일로 확정됐습니다", "금요일로 미뤘습니다".

    Apart from ``parse_due`` because the tense means the opposite here. A past
    verb after a deadline reports what happened; a past verb after "N일로"
    reports the choosing, and the date chosen is still ahead. Nothing is
    resolved: this says a date was named as a choice, not which day it is."""
    return any(
        _CHOSEN.match(text, match.end())
        for pattern, _ in _PHRASES
        for match in pattern.finditer(text)
    )


def parse_due(text: str, day: date | None, *, decided: bool = False) -> DueDate | None:
    """The first date phrase in ``text`` that is a deadline, not the past.

    ``None`` when the utterance names no such date. When it names more than
    one -- "다음 주 금요일까지 하고 월요일에 공유" -- the first is taken: the
    rest is usually what happens after the thing promised.

    **A phrase a deadline word follows wins** (``_BY``: 까지, 전에, 내로, 안에,
    ...). "이번 주 회의록은 제가 다음 주 금요일까지 정리하겠습니다" names the
    minutes by their week and the deadline by its 까지; taking the first phrase
    made it due this Friday (#616). Only when no phrase has a deadline word is
    the first one taken, as before -- "이번 주 회의록 정리하겠습니다" still
    reads as due this week, for a person to correct.

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

    ``decided`` is for the lines of a decision (``decisions._build``): there a
    date can be the thing decided -- "배포 요일은 화요일로 바꾸기로 했습니다",
    "정기 회의는 매주 월요일에 하기로 했습니다" -- and such a phrase is skipped
    like one said of the past (``_what_was_decided``). Until 2026-10-09 the
    first read "(기한 2026-10-13)" on a decision that sets no deadline. A
    promise is read without it: "화요일로 옮기겠습니다" is still a card due
    Tuesday, for a person to correct.
    """
    found: list[tuple[int, int, re.Match[str], Resolver]] = []
    for pattern, resolve in _PHRASES:
        for match in pattern.finditer(text):
            found.append((match.start(), -(match.end() - match.start()), match, resolve))

    taken_until = -1
    fallback: DueDate | None = None
    for start, _, match, resolve in sorted(found, key=lambda entry: (entry[0], entry[1])):
        if start < taken_until:
            continue  # part of a longer phrase already considered
        taken_until = match.end()
        stop = min((other for other, *_ in found if other >= match.end()), default=len(text))
        if _said_of_the_past(text, match.end(), stop):
            continue
        if decided and _what_was_decided(text, match.start(), match.end()):
            continue
        try:
            resolved = resolve(match, day)
        except ValueError:
            # "2월 30일": the phrase is a date, the day does not exist.
            resolved = None
        if resolved is not None and day is not None and resolved < day:
            continue
        due = DueDate(text=re.sub(r"\s+", " ", match[0]).strip(), date=resolved)
        if _DEADLINE_WORD.match(text, match.end()):
            return due
        fallback = fallback or due
    return fallback
