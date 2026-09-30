"""PostgreSQL fixtures for the agent layer's integration tests.

The unit suite runs on SQLite, which enforces no foreign keys by default. What
these tests are for is what SQLite cannot show: that deleting a meeting takes
every ``agent_`` row that copied it. Same shape as modules B's and E's
conftests; ``autune_core.testing`` does not exist yet.

Uses ``AUTUNE_DATABASE_URL`` (CI starts PostgreSQL for it). Run it locally
against a throwaway database, not the shared dev one: ``upgrade heads`` writes
this branch's revisions into whatever database it is pointed at.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

import autune_agent.models  # noqa: F401  (agent_ tables)
import autune_core.entities  # noqa: F401  (shared tables: meetings, users, ...)
from autune_core import get_settings


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "alembic.ini").is_file():
            return parent
    raise RuntimeError("could not locate repo root (infra/alembic.ini not found)")


@pytest.fixture(scope="session")
def db_engine() -> Iterator[sa.Engine]:
    subprocess.run(
        ["uv", "run", "alembic", "-c", "infra/alembic.ini", "upgrade", "heads"],
        check=True,
        cwd=repo_root(),
    )
    engine = sa.create_engine(get_settings().database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: sa.Engine) -> Iterator[Session]:
    """A session whose work is rolled back at the end, savepoints included."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
