"""The intel_ revision applies from scratch and reverses cleanly."""

from __future__ import annotations

import subprocess

import sqlalchemy as sa

from autune_core import get_settings

ALEMBIC = ["uv", "run", "alembic", "-c", "infra/alembic.ini"]
INTEL_TABLES = {
    "intel_completion",
    "intel_scores",
    "intel_gap_patterns",
    "intel_alignment",
    "intel_predictions",
    "intel_reports",
}


def _intel_tables_in_db() -> set[str]:
    engine = sa.create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'intel\\_%'"
            )
        )
        return {r[0] for r in rows}


def test_upgrade_creates_then_downgrade_removes_every_intel_table() -> None:
    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True)
    assert _intel_tables_in_db() == INTEL_TABLES

    subprocess.run([*ALEMBIC, "downgrade", "intelligence@base"], check=True)
    assert _intel_tables_in_db() == set()

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True)
    assert _intel_tables_in_db() == INTEL_TABLES
