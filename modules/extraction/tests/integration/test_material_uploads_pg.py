"""An uploaded material's text goes with it and with its team, on PostgreSQL (#817).

What the unit suite cannot show on SQLite: that ``ON DELETE CASCADE`` takes an
upload's pieces of masked text with its row, and every material and alarm row
with the team (#1007's deletion); and that the constraints refuse a row that is
half a link and half an upload, or an alarm of an unknown kind.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Team
from autune_extraction import materials
from autune_extraction.models import ExtMaterial, ExtMaterialAlarm, ExtMaterialChunk

BODY = "3분기 계획\n담당 연락처 010-2345-6789\n" * 60


@pytest.fixture
def team_id(db_session: Session) -> str:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    return team.id


def rows(session: Session, model: type, **where: str) -> int:
    query = sa.select(sa.func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return session.scalar(query) or 0


def upload(session: Session, team_id: str, title: str = "계획") -> ExtMaterial:
    return materials.store_upload(
        session, team_id, title=title, file_name="plan.txt", data=BODY.encode()
    )


def test_deleting_the_row_takes_its_text_by_cascade(db_session: Session, team_id: str) -> None:
    row = upload(db_session, team_id)
    assert rows(db_session, ExtMaterialChunk, material_id=row.id) > 1

    db_session.execute(sa.delete(ExtMaterial).where(ExtMaterial.id == row.id))

    assert rows(db_session, ExtMaterialChunk, material_id=row.id) == 0


def test_a_member_delete_takes_the_text_in_the_same_transaction(
    db_session: Session, team_id: str
) -> None:
    row = upload(db_session, team_id)
    kept = upload(db_session, team_id, title="다른 자료")

    materials.delete_material(db_session, team_id, row.id)

    assert rows(db_session, ExtMaterialChunk, material_id=row.id) == 0
    assert rows(db_session, ExtMaterialChunk, material_id=kept.id) > 0


def test_deleting_the_team_takes_every_material_piece_and_alarm(
    db_session: Session, team_id: str
) -> None:
    row = upload(db_session, team_id)
    materials.register(db_session, team_id, title="링크", link="a" * 20)
    with pytest.raises(materials.ConfidentialFileError):
        materials.store_upload(
            db_session, team_id, title="막힘", file_name="기밀.txt", data=BODY.encode()
        )

    db_session.execute(sa.delete(Team).where(Team.id == team_id))

    assert rows(db_session, ExtMaterial, team_id=team_id) == 0
    assert rows(db_session, ExtMaterialChunk, material_id=row.id) == 0
    assert rows(db_session, ExtMaterialAlarm, team_id=team_id) == 0


@pytest.mark.parametrize(
    ("source", "file_id", "kind"),
    [
        ("upload", "a" * 20, "file"),
        ("upload", None, "file"),
        ("drive_link", None, None),
        ("drive_link", "a" * 20, None),
        ("other", None, None),
    ],
)
def test_a_row_is_wholly_a_link_or_wholly_an_upload(
    db_session: Session, team_id: str, source: str, file_id: str | None, kind: str | None
) -> None:
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            ExtMaterial(
                team_id=team_id, title="x", source=source, drive_file_id=file_id, drive_kind=kind
            )
        )
        db_session.flush()


def test_an_alarm_is_of_a_known_kind(db_session: Session, team_id: str) -> None:
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(ExtMaterialAlarm(team_id=team_id, marking="the word"))
        db_session.flush()


def test_a_link_registered_before_this_revision_is_a_link(
    db_session: Session, team_id: str
) -> None:
    db_session.execute(
        sa.text(
            "INSERT INTO ext_materials (id, team_id, title, drive_file_id, drive_kind, created_at)"
            " VALUES ('mat_old', :team, '옛 링크', :file, 'file', now())"
        ),
        {"team": team_id, "file": "b" * 20},
    )
    assert db_session.scalar(sa.text("SELECT source FROM ext_materials WHERE id = 'mat_old'")) == (
        "drive_link"
    )
