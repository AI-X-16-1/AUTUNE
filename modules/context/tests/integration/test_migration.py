"""The context branch's ctx_ revisions apply from scratch and reverse cleanly."""

from __future__ import annotations

import subprocess
from pathlib import Path

import sqlalchemy as sa

from autune_core import get_settings


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "alembic.ini").is_file():
            return parent
    raise RuntimeError("could not locate repo root (infra/alembic.ini not found)")


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
    root = _repo_root()
    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
    assert _ctx_tables_in_db() == CTX_TABLES

    # Back to the branch anchor, then forward again. Target the base by name
    # rather than a step count, which went stale each time a revision landed.
    subprocess.run([*ALEMBIC, "downgrade", "context@base"], check=True, cwd=root)
    assert _ctx_tables_in_db() == set()

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
    assert _ctx_tables_in_db() == CTX_TABLES
