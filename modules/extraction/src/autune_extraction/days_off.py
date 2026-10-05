"""Days and hours on which no digest goes: public holidays, and time a person
is out of office (the user, 2026-10-05).

The morning DM and Monday's digest are about work, so they are held back when
nobody is working -- a public holiday -- and from a person who is away. Three
things say so, and each is read as narrowly as it can be:

**Public holidays come from Google's public calendar of Korea's holidays.**
Its public address is fetched with no credentials: nobody's Google grant is
used and nothing is sent but the request. The days are kept in
``ext_public_holidays``, replaced whole on each read. They are dates of public
record and say nothing about anybody.

**A table in code stands in when the calendar has not been read.** The
``holidays`` package knows Korea's holidays, lunar and substitute days
included, with no network. It is used while there is no read newer than
``FRESH_FOR`` -- a deployment that makes no outbound calls, the hours before
the first read, a calendar Google moved. It cannot know a holiday declared
after the package was released, which is why the calendar comes first.

**A person's own leave is their out-of-office time, and only that.** With a
calendar connected, ``away_now`` asks Google for the out-of-office events that
cover this moment and gets their times back -- no title, no other event
(``CalendarClient.out_of_office``). The answer is used once, to hold one
message back, and is not stored: nothing here writes down that a person was
away. The dates a person sets themselves are ``ext_notification_pauses``.

Due-date reminders read none of this: a deadline is not put off by a holiday.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import holidays as holiday_tables
import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError

from .models import ExtPublicHoliday
from .slots import KST

KOREA_HOLIDAYS_ICS = (
    "https://calendar.google.com/calendar/ical/"
    "ko.south_korea.official%23holiday%40group.v.calendar.google.com/public/basic.ics"
)
"""Google's "대한민국의 휴일", public holidays only (``.official``), at its
public iCalendar address. Checked by hand on 2026-10-05: it answers without
credentials, every entry is described ``공휴일``, and substitute days
("쉬는 날 ...") and election days are in it."""

SOURCE = "google_holiday_calendar"

FRESH_FOR = timedelta(days=14)
"""How long a read of the calendar is trusted. The task reads twice a day, so
this is two weeks of failed reads before the table in code takes over."""

LOOK_AHEAD = timedelta(days=370)
"""A read with no holiday in the coming year is not a calendar of holidays --
an error page, an emptied feed -- and is refused rather than stored."""

OBSERVANCE = "기념일"
"""How Google describes a day that is marked and worked. The ``.official``
calendar should hold none; one that appears is skipped, not taken as a day off."""

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


# --- reading the calendar -----------------------------------------------------------


def _unfolded(text: str) -> list[str]:
    """iCalendar lines, with a continuation (a line that starts with a space or
    a tab) joined to the line it continues (RFC 5545, 3.1)."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_date(line: str) -> date | None:
    """The date of a ``DTSTART``/``DTEND`` line, whether it is ``VALUE=DATE``
    (``20261009``) or a date-time (``20261009T000000Z``)."""
    value = line.partition(":")[2].strip()[:8]
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError:
        return None


def parse_holiday_ics(text: str) -> set[date]:
    """Every day covered by an event of an iCalendar file. An event's end is
    exclusive, as iCalendar's is; one with no end is its start day. A day
    described as an observance is not a day off and is left out."""
    days: set[date] = set()
    start: date | None = None
    end: date | None = None
    observance = False
    inside = False
    for line in _unfolded(text):
        name = line.partition(":")[0].partition(";")[0].upper()
        if line.strip().upper() == "BEGIN:VEVENT":
            inside, start, end, observance = True, None, None, False
        elif line.strip().upper() == "END:VEVENT":
            if inside and start is not None and not observance:
                last = end if end is not None and end > start else start + timedelta(days=1)
                # A holiday is a day or a few; a span of months is not one.
                span = min((last - start).days, 14)
                days.update(start + timedelta(days=n) for n in range(span))
            inside = False
        elif not inside:
            continue
        elif name == "DTSTART":
            start = _ics_date(line)
        elif name == "DTEND":
            end = _ics_date(line)
        elif name == "DESCRIPTION":
            observance = line.partition(":")[2].strip().startswith(OBSERVANCE)
    return days


def fetch_public_holidays(*, today: date, http: httpx.Client | None = None) -> set[date]:
    """Korea's public holidays as Google's public calendar lists them.

    A plain ``GET`` of a public file: no credentials, no parameters, no body.
    Raises ``TransientIntegrationError`` for an outage and
    ``PermanentIntegrationError`` for a refusal or an answer that is not a
    calendar with a holiday in the coming year -- in every case nothing is
    stored and the last good read stays.
    """
    client = http or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
    try:
        response = client.get(KOREA_HOLIDAYS_ICS)
    except httpx.TimeoutException as exc:
        raise TransientIntegrationError(f"{SOURCE} timed out") from exc
    except httpx.TransportError as exc:
        raise TransientIntegrationError(f"{SOURCE} is unreachable") from exc
    finally:
        if http is None:
            client.close()
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientIntegrationError(f"{SOURCE} returned {response.status_code}")
    if response.status_code != 200:
        raise PermanentIntegrationError(
            f"{SOURCE} answered {response.status_code}", upstream_status=response.status_code
        )
    days = parse_holiday_ics(response.text)
    if not any(today <= day <= today + LOOK_AHEAD for day in days):
        raise PermanentIntegrationError(f"{SOURCE} listed no holiday in the coming year")
    return days


def store_public_holidays(session: Session, days: Iterable[date], *, now: datetime) -> int:
    """Replace what is kept with this read, whole: a day Google took back is
    gone, and every row carries the time of the read it came from."""
    kept = sorted(set(days))
    session.execute(delete(ExtPublicHoliday))
    session.add_all(ExtPublicHoliday(day=day, read_at=now) for day in kept)
    session.flush()
    return len(kept)


# --- asking -------------------------------------------------------------------------


def in_table(day: date) -> bool:
    """Whether the table in code has ``day`` as a public holiday in Korea."""
    return day in holiday_tables.country_holidays("KR", years=day.year)


def is_public_holiday(session: Session, day: date, *, now: datetime) -> bool:
    """Whether ``day`` is a public holiday in Korea: by the calendar when it
    was read within ``FRESH_FOR``, by the table in code otherwise."""
    read_at = session.scalar(select(func.max(ExtPublicHoliday.read_at)))
    if read_at is not None:
        if read_at.tzinfo is None:
            read_at = read_at.replace(tzinfo=UTC)
        if now - read_at <= FRESH_FOR:
            return session.get(ExtPublicHoliday, day) is not None
    return in_table(day)


class LeaveCalendar(Protocol):
    """The one read this module makes of events it did not write."""

    def out_of_office(
        self, calendar_id: str, time_min: datetime, time_max: datetime
    ) -> list[tuple[datetime | date, datetime | date]]: ...


def away_now(calendar: LeaveCalendar, calendar_id: str, *, now: datetime) -> bool:
    """Whether the calendar's owner is marked out of office at ``now``.

    A timed entry counts while it covers the moment; an all-day one on the
    day it covers, in Korea. So a morning off holds the morning DM back and an
    afternoon off does not -- and somebody back at eleven gets theirs then,
    because the task asks again on its next run. Nothing is kept of the
    answer.
    """
    today = now.astimezone(KST).date()
    for start, end in calendar.out_of_office(calendar_id, now, now + timedelta(minutes=1)):
        if isinstance(start, datetime) and isinstance(end, datetime):
            covers = start <= now < end
        elif isinstance(start, datetime) or isinstance(end, datetime):
            covers = False  # one of each is not an entry Google makes
        else:
            covers = start <= today < end
        if covers:
            return True
    return False
