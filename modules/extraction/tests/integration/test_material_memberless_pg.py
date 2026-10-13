"""The last member's account goes, then the team's materials, on PostgreSQL (#817).

What the unit suite cannot show on SQLite: that deleting the ``users`` row --
the statement module A's ``delete_account`` runs -- takes the membership with
it by ``ON DELETE CASCADE`` and leaves the team, and that the hourly task then
finds that team by the absence of a ``team_members`` row and empties it,
vectors included.

Module A's function is not called: no module imports another (invariant 2).
The test runs the delete that function ends in, against the real foreign keys.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Team, TeamMember, User
from autune_extraction import materials
from autune_extraction.models import (
    ExtMaterial,
    ExtMaterialAlarm,
    ExtMaterialChunk,
    ExtMaterialSetting,
)

BODY = "3분기 계획\n예산 1200만원\n" * 60


def team_of(session: Session, *emails: str) -> tuple[str, list[str]]:
    team = Team(name="팀")
    session.add(team)
    session.flush()
    users = [User(email=email, display_name="팀원") for email in emails]
    session.add_all(users)
    session.flush()
    session.add_all([TeamMember(team_id=team.id, user_id=user.id) for user in users])
    session.flush()
    return team.id, [user.id for user in users]


def shelf(session: Session, team_id: str, mark: str) -> str:
    row = materials.store_upload(
        session, team_id, title="계획", file_name="plan.txt", data=f"{BODY}{mark}".encode()
    ).row
    materials.register(session, team_id, title="링크", link="a" * 20)
    with pytest.raises(materials.ConfidentialFileError):
        materials.store_upload(
            session, team_id, title="요금표", file_name="fees.txt", data=f"대외비\n{BODY}".encode()
        )
    materials.set_keep_until_deleted(session, team_id, True)
    session.flush()
    return row.id


def rows(session: Session, model: type, team_id: str) -> int:
    return (
        session.scalar(
            sa.select(sa.func.count()).select_from(model).where(model.team_id == team_id)  # type: ignore[attr-defined]
        )
        or 0
    )


def pieces(session: Session, material_id: str) -> int:
    return (
        session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtMaterialChunk)
            .where(ExtMaterialChunk.material_id == material_id)
        )
        or 0
    )


def delete_account(session: Session, user_id: str) -> None:
    """The statement ``autune_audio.account.delete_account`` ends in."""
    session.execute(sa.delete(User).where(User.id == user_id))
    session.expire_all()


def test_the_last_members_account_deletion_empties_the_team_at_the_next_run(
    db_session: Session,
) -> None:
    ours, (only,) = team_of(db_session, "only@example.com")
    theirs, (one, _two) = team_of(db_session, "one@example.com", "two@example.com")
    mine = shelf(db_session, ours, "우리")
    other = shelf(db_session, theirs, "남")
    assert pieces(db_session, mine) > 1

    assert materials.forget_memberless_team_materials(db_session) == 0

    delete_account(db_session, only)
    delete_account(db_session, one)
    # The membership went with the account; the team did not.
    assert rows(db_session, TeamMember, ours) == 0
    assert db_session.get(Team, ours) is not None
    assert rows(db_session, ExtMaterial, ours) == 2

    assert materials.forget_memberless_team_materials(db_session) == 1

    for model in (ExtMaterial, ExtMaterialAlarm, ExtMaterialSetting):
        assert rows(db_session, model, ours) == 0
    assert pieces(db_session, mine) == 0
    assert db_session.get(Team, ours) is not None
    # A team that still has one member keeps all of it, kept-until-deleted as it was.
    assert rows(db_session, ExtMaterial, theirs) == 2
    assert rows(db_session, ExtMaterialAlarm, theirs) == 1
    assert rows(db_session, ExtMaterialSetting, theirs) == 1
    assert pieces(db_session, other) > 1


def test_a_second_run_finds_nothing(db_session: Session) -> None:
    ours, (only,) = team_of(db_session, "again@example.com")
    shelf(db_session, ours, "다시")
    delete_account(db_session, only)

    assert materials.forget_memberless_team_materials(db_session) == 1
    assert materials.forget_memberless_team_materials(db_session) == 0
