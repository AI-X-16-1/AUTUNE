"""An upload past its team's window goes with its text, on PostgreSQL (the owner, 2026-10-11).

What the unit suite cannot show on SQLite: that the batch delete takes every
piece of masked text by ``ON DELETE CASCADE`` as well as by its own delete,
that the window compares real ``timestamptz`` values, and that the setting
row goes with its team.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Team
from autune_extraction import materials
from autune_extraction.models import ExtMaterial, ExtMaterialChunk, ExtMaterialSetting

BODY = "3분기 계획\n예산 1200만원\n" * 60
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def team(session: Session, days: int = 90) -> str:
    row = Team(name="팀", retention_days=days)
    session.add(row)
    session.flush()
    return row.id


def upload(session: Session, team_id: str, age: timedelta) -> ExtMaterial:
    row = materials.store_upload(
        session, team_id, title="계획", file_name="plan.txt", data=BODY.encode()
    )
    row.created_at = NOW - age
    session.flush()
    return row


def pieces(session: Session, material_id: str) -> int:
    return (
        session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtMaterialChunk)
            .where(ExtMaterialChunk.material_id == material_id)
        )
        or 0
    )


def test_an_expired_upload_goes_with_every_piece(db_session: Session) -> None:
    ours = team(db_session, days=30)
    old = upload(db_session, ours, timedelta(days=31))
    young = upload(db_session, ours, timedelta(days=29))
    assert pieces(db_session, old.id) > 1

    assert materials.forget_expired_uploads(db_session, now=NOW) == 1

    assert db_session.get(ExtMaterial, old.id) is None
    assert pieces(db_session, old.id) == 0
    assert pieces(db_session, young.id) > 1


def test_a_team_that_keeps_until_deleted_loses_nothing_until_it_switches_back(
    db_session: Session,
) -> None:
    ours = team(db_session)
    theirs = team(db_session)
    kept = upload(db_session, ours, timedelta(days=200))
    other = upload(db_session, theirs, timedelta(days=200))
    materials.set_keep_until_deleted(db_session, ours, True)

    assert materials.forget_expired_uploads(db_session, now=NOW) == 1
    assert db_session.get(ExtMaterial, other.id) is None
    assert db_session.get(ExtMaterial, kept.id) is not None

    materials.set_keep_until_deleted(db_session, ours, False)
    assert materials.forget_expired_uploads(db_session, now=NOW) == 1
    assert db_session.get(ExtMaterial, kept.id) is None


def test_a_link_has_no_window(db_session: Session) -> None:
    ours = team(db_session, days=1)
    row = materials.register(db_session, ours, title="링크", link="c" * 20)
    row.created_at = NOW - timedelta(days=1000)
    db_session.flush()

    assert materials.forget_expired_uploads(db_session, now=NOW) == 0
    assert db_session.get(ExtMaterial, row.id) is not None


def test_the_setting_goes_with_its_team(db_session: Session) -> None:
    ours = team(db_session)
    materials.set_keep_until_deleted(db_session, ours, True)

    db_session.execute(sa.delete(Team).where(Team.id == ours))

    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtMaterialSetting)
            .where(ExtMaterialSetting.team_id == ours)
        )
        == 0
    )
