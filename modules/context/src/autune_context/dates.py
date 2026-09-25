"""The calendar day a meeting happened on, as the people in it would say it.

``Meeting.started_at`` is timezone-aware and comes back in the session
timezone (UTC), so ``started_at.date()`` names the wrong day for every meeting
before 09:00 in Korea -- a stand-up at 08:00 KST reads as the day before.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")
"""The day a notice's "2026년 9월 18일 회의" means is the day in Korea. There is no
team timezone to read yet, and Korea keeps no daylight saving, so a fixed offset
is exact rather than an approximation of ``Asia/Seoul``.

Module B keeps the same constant for the same reason
(``autune_extraction.slots.KST``); modules never import each other, so this is
a copy, not a shared symbol. It belongs in ``packages/core`` once a team
timezone exists."""


def meeting_day(started_at: datetime) -> date:
    """The KST calendar day of ``started_at``; a naive value is taken as UTC."""
    moment = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    return moment.astimezone(KST).date()
