"""PostgreSQL fixtures for Meeting Context Engine integration tests.

Uses the database from infra/docker-compose.yml (the pgvector image). Mirrors
modules/intelligence/tests/integration/conftest.py — ``autune_core.testing``
does not exist yet; when it does, this file should import from it instead.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

import autune_context.models  # noqa: F401  (ctx_ tables)
import autune_core.entities  # noqa: F401  (shared tables: meetings, teams, ...)
from autune_core import get_settings


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "alembic.ini").is_file():
            return parent
    raise RuntimeError("could not locate repo root (infra/alembic.ini not found)")


@pytest.fixture(scope="session")
def db_engine() -> Iterator[sa.Engine]:
    subprocess.run(
        ["uv", "run", "alembic", "-c", "infra/alembic.ini", "upgrade", "heads"],
        check=True,
        cwd=_repo_root(),
    )
    engine = sa.create_engine(get_settings().database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: sa.Engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def team(db_session: Session) -> str:
    from autune_core import Team

    row = Team(name="Test Team")
    db_session.add(row)
    db_session.flush()
    return row.id


@pytest.fixture
def meeting(db_session: Session, team: str) -> str:
    from autune_core import Meeting

    row = Meeting(team_id=team, title="Test Meeting")
    db_session.add(row)
    db_session.flush()
    return row.id
