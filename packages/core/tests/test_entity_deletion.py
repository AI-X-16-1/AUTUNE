"""Deleting a user or a team through the ORM leaves their memberships to the database (#355).

`team_members.user_id` and `.team_id` are NOT NULL with ON DELETE CASCADE.
Without `passive_deletes` the ORM loads the memberships and nulls the column
before its DELETE, which the NOT NULL refuses -- so `session.delete(user)`, the
way anyone would write account deletion or its test, raised.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import Base, Team, TeamMember, User


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:  # type: ignore[no-untyped-def]
        # SQLite enforces foreign keys -- and runs ON DELETE CASCADE -- only when asked.
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[User.__table__, Team.__table__, TeamMember.__table__])
    with sessionmaker(bind=engine)() as session:
        yield session


@pytest.fixture
def member(db: Session) -> tuple[User, Team]:
    user = User(email="leaving@example.com", display_name="떠나는 사람")
    team = Team(name="팀")
    db.add_all([user, team])
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id))
    db.flush()
    return user, team


def test_deleting_a_user_takes_their_memberships(db: Session, member: tuple[User, Team]) -> None:
    user, team = member

    db.delete(user)
    db.flush()

    assert db.scalars(sa.select(TeamMember)).all() == []
    assert db.get(Team, team.id) is not None


def test_deleting_a_team_takes_its_memberships(db: Session, member: tuple[User, Team]) -> None:
    user, team = member

    db.delete(team)
    db.flush()

    assert db.scalars(sa.select(TeamMember)).all() == []
    assert db.get(User, user.id) is not None


def test_a_user_whose_memberships_were_read_can_still_be_deleted(
    db: Session, member: tuple[User, Team]
) -> None:
    """``passive_deletes=True`` only stands aside for a collection that was never
    loaded; once ``user.memberships`` has been read, the ORM nulls the column
    again and the NOT NULL refuses (review of #613). ``"all"`` never touches it."""
    user, _ = member
    assert len(user.memberships) == 1

    db.delete(user)
    db.flush()

    assert db.scalars(sa.select(TeamMember)).all() == []


def test_a_team_whose_members_were_read_can_still_be_deleted(
    db: Session, member: tuple[User, Team]
) -> None:
    _, team = member
    assert len(team.members) == 1

    db.delete(team)
    db.flush()

    assert db.scalars(sa.select(TeamMember)).all() == []
