"""``meeting_day`` -- the day a notice's "N월 N일 회의" names."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from autune_context.dates import KST, meeting_day


def test_an_early_morning_kst_meeting_is_that_day_not_the_day_before() -> None:
    # 2026-09-18 08:00 KST is 2026-09-17 23:00 UTC -- the stand-up case.
    started_at = datetime(2026, 9, 17, 23, 0, tzinfo=UTC)
    assert started_at.date() == date(2026, 9, 17)
    assert meeting_day(started_at) == date(2026, 9, 18)


def test_an_afternoon_kst_meeting_is_unchanged() -> None:
    assert meeting_day(datetime(2026, 9, 18, 5, 0, tzinfo=UTC)) == date(2026, 9, 18)


def test_the_boundary_is_midnight_in_korea() -> None:
    assert meeting_day(datetime(2026, 9, 17, 14, 59, tzinfo=UTC)) == date(2026, 9, 17)
    assert meeting_day(datetime(2026, 9, 17, 15, 0, tzinfo=UTC)) == date(2026, 9, 18)


def test_a_value_already_in_kst_or_another_zone_is_converted_not_read_as_is() -> None:
    assert meeting_day(datetime(2026, 9, 18, 8, 0, tzinfo=KST)) == date(2026, 9, 18)
    us_evening = datetime(2026, 9, 17, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert meeting_day(us_evening) == date(2026, 9, 18)


def test_a_naive_value_is_taken_as_utc() -> None:
    assert meeting_day(datetime(2026, 9, 17, 23, 0)) == date(2026, 9, 18)
