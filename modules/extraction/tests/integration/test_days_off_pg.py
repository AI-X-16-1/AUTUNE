"""Public holidays on PostgreSQL: the table the migration makes, and the
freshness of a read compared as ``timestamptz``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_extraction import days_off

NOW = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
DECLARED = date(2026, 10, 8)  # not in the table in code
HANGUL_DAY = date(2026, 10, 9)


def test_a_fresh_read_answers_and_an_old_one_gives_way_to_the_table(
    db_session: Session,
) -> None:
    days_off.store_public_holidays(db_session, {DECLARED}, now=NOW)

    assert days_off.is_public_holiday(db_session, DECLARED, now=NOW)
    assert not days_off.is_public_holiday(db_session, HANGUL_DAY, now=NOW)

    later = NOW + days_off.FRESH_FOR + timedelta(minutes=1)
    assert not days_off.is_public_holiday(db_session, DECLARED, now=later)
    assert days_off.is_public_holiday(db_session, HANGUL_DAY, now=later)


def test_a_read_replaces_the_last_one_and_a_day_is_kept_once(db_session: Session) -> None:
    days_off.store_public_holidays(db_session, [DECLARED, HANGUL_DAY, HANGUL_DAY], now=NOW)
    days_off.store_public_holidays(db_session, [HANGUL_DAY], now=NOW + timedelta(hours=12))

    rows = db_session.execute(sa.text("SELECT day, read_at FROM ext_public_holidays")).all()
    assert [(day, read_at.astimezone(UTC)) for day, read_at in rows] == [
        (HANGUL_DAY, NOW + timedelta(hours=12))
    ]
