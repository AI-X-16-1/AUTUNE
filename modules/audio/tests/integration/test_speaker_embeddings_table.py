"""The table both kinds of row live in, and the deletion paths privacy.md asks for.

Read `modules/audio/tests/integration/conftest.py` for `db_session`: it runs
`alembic upgrade heads` once per session and wraps each test in a transaction.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_audio.models import EMBEDDING_DIM, AudSpeakerEmbedding
from autune_core import TeamMember, User
from autune_core.entities import Meeting


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


def vector(seed: float = 1.0) -> list[float]:
    return [seed] + [0.0] * (EMBEDDING_DIM - 1)


def test_an_observation_row_cascades_with_its_meeting(db_session: Session, meeting: str) -> None:
    db_session.add(
        AudSpeakerEmbedding(
            meeting_id=meeting,
            speaker_label="화자 2",
            vector=vector(),
            model_version="test/embedder",
        )
    )
    db_session.flush()
    db_session.delete(db_session.get(Meeting, meeting))
    db_session.flush()
    assert db_session.scalars(sa.select(AudSpeakerEmbedding)).all() == []


def test_a_profile_row_survives_its_source_meeting(
    db_session: Session, meeting: str, member: User
) -> None:
    db_session.add(
        AudSpeakerEmbedding(
            user_id=member.id,
            vector=vector(),
            model_version="test/embedder",
            source_meeting_id=meeting,
            source_speaker_label="화자 2",
            confirmed_by=member.id,
        )
    )
    db_session.flush()
    db_session.delete(db_session.get(Meeting, meeting))
    db_session.flush()
    [row] = db_session.scalars(sa.select(AudSpeakerEmbedding)).all()
    assert row.user_id == member.id
    assert row.source_meeting_id is None


def test_a_profile_row_goes_with_the_person(db_session: Session, member: User) -> None:
    db_session.add(
        AudSpeakerEmbedding(user_id=member.id, vector=vector(), model_version="test/embedder")
    )
    db_session.flush()
    # A Core-level DELETE, not `session.delete(session.get(...))`: the ORM
    # session would otherwise walk `User.memberships` (a bidirectional
    # relationship in autune_core.entities with no `passive_deletes`) and try
    # to null out `team_members.user_id`, which is NOT NULL -- a pre-existing
    # issue in packages/core unrelated to this table, outside module A's
    # boundary to fix here. A bulk delete skips that walk and lets Postgres's
    # own `ON DELETE CASCADE` do the work this test is actually about.
    db_session.execute(sa.delete(User).where(User.id == member.id))
    db_session.flush()
    assert db_session.scalars(sa.select(AudSpeakerEmbedding)).all() == []


def test_a_row_is_an_observation_or_a_profile_never_both(
    db_session: Session, meeting: str, member: User
) -> None:
    """The check constraint is what keeps the two shapes from blurring."""
    db_session.add(
        AudSpeakerEmbedding(
            meeting_id=meeting,
            speaker_label="화자 2",
            user_id=member.id,
            vector=vector(),
            model_version="test/embedder",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
