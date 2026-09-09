"""The five ctx_ revisions apply from scratch and reverse cleanly."""

from __future__ import annotations

import subprocess

import sqlalchemy as sa

from autune_core import get_settings

ALEMBIC = ["uv", "run", "alembic", "-c", "infra/alembic.ini"]
CTX_TABLES = {
    "ctx_embeddings",
    "ctx_topic_links",
    "ctx_decisions",
    "ctx_decision_versions",
    "ctx_meeting_status",
}


def _ctx_tables_in_db() -> set[str]:
    engine = sa.create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'ctx\\_%'"
            )
        )
        return {r[0] for r in rows}


def test_upgrade_creates_then_downgrade_removes_every_ctx_table() -> None:
    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True)
    assert _ctx_tables_in_db() == CTX_TABLES

    # Back to the branch anchor (five revisions down), then forward again.
    subprocess.run([*ALEMBIC, "downgrade", "context@-5"], check=True)
    assert _ctx_tables_in_db() == set()

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True)
    assert _ctx_tables_in_db() == CTX_TABLES
