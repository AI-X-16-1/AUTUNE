"""PostgreSQL fixtures for Gap Detection integration tests.

Uses the database from infra/docker-compose.yml. Mirrors
modules/context/tests/integration/conftest.py — ``autune_core.testing`` does not
exist yet; when it does, this file should import from it instead.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa

import autune_core.entities  # noqa: F401  (shared tables: meetings, participants, ...)
import autune_gap.models  # noqa: F401  (gap_ tables)
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
