"""PostgreSQL fixtures for module B's integration tests.

The unit suite runs on SQLite, which enforces no foreign keys by default and
names no CHECK constraint the way PostgreSQL does. What these tests are for is
exactly what SQLite cannot show: that deleting a meeting takes every ``ext_``
row with it, and that the constraints refuse what they were written to refuse.

Uses ``AUTUNE_DATABASE_URL`` (CI starts PostgreSQL for it). Same shape as module
E's conftest; ``autune_core.testing`` does not exist yet, and when it does both
should import from it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

import autune_core.entities  # noqa: F401  (shared tables: meetings, users, ...)
import autune_extraction.models  # noqa: F401  (ext_ tables)
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
