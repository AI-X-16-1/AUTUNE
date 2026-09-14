"""PostgreSQL fixtures for Module E integration tests.

Uses the database from infra/docker-compose.yml. `autune_core.testing` does not
exist yet; when it does, this file should import from it instead.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

import autune_core.entities  # noqa: F401  (shared tables: meetings, teams, ...)
import autune_intelligence.models  # noqa: F401  (intel_ tables)
from autune_core import get_settings
from autune_intelligence.config import get_settings as get_intelligence_settings
from autune_intelligence.pipeline import reset_cache


@pytest.fixture(autouse=True)
def _fake_gap_classifier(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No network, no SetFit training, in a suite that runs against real Postgres.

    Mirrors module D's ``_fake_models`` fixture (test_topic_linking.py):
    ``local`` is the config default, but a real model here would mean every
    aggregation test trains SetFit against the seed set.
    """
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL", "fake")
    get_intelligence_settings.cache_clear()
    reset_cache()
    yield
    get_intelligence_settings.cache_clear()
    reset_cache()


def _repo_root() -> Path:
    """Walk up from this file until the directory holding infra/alembic.ini."""
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
