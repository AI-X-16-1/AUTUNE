"""Module B's Alembic branch comes off and goes back on, alone.

CI's migration job round-trips every branch together (``downgrade core@base``).
This takes only the ``extraction`` branch down to its base and up again, which
is what proves B's downgrades are real rather than covered by the core branch
dropping ``meetings`` underneath them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import sqlalchemy as sa

from autune_core import get_settings

ALEMBIC = ["uv", "run", "alembic", "-c", "infra/alembic.ini"]


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "alembic.ini").is_file():
            return parent
    raise RuntimeError("could not locate repo root (infra/alembic.ini not found)")


def ext_tables() -> set[str]:
    engine = sa.create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name LIKE 'ext\\_%'"
                )
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def test_the_extraction_branch_downgrades_to_nothing_and_back(db_engine: sa.Engine) -> None:
    root = repo_root()
    before = ext_tables()
    assert "ext_classifications" in before

    subprocess.run([*ALEMBIC, "downgrade", "extraction@base"], check=True, cwd=root)
    assert ext_tables() == set()

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
    assert ext_tables() == before
