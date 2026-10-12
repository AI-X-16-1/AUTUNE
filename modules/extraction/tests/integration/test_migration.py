"""Module B's Alembic branch comes off and goes back on, alone.

CI's migration job round-trips every branch together (``downgrade core@base``).
This takes only the ``extraction`` branch down to its base and up again, which
is what proves B's downgrades are real rather than covered by the core branch
dropping ``meetings`` underneath them.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Team, get_settings

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


def test_the_material_settings_revision_comes_off_and_goes_back_on(db_engine: sa.Engine) -> None:
    """The settings revision alone: down to the uploads revision drops only
    ``ext_material_settings``; up again brings it back."""
    root = repo_root()
    before = ext_tables()
    assert "ext_material_settings" in before

    subprocess.run([*ALEMBIC, "downgrade", "5d1a7c3e9b24"], check=True, cwd=root)
    assert ext_tables() == before - {"ext_material_settings"}

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
    assert ext_tables() == before


def test_the_expires_at_revision_gives_an_existing_upload_its_day(db_engine: sa.Engine) -> None:
    """Down to the settings revision the column is gone; up again an upload
    already in the table gets its upload plus its team's window, and a link
    gets nothing."""
    root = repo_root()
    made = datetime(2026, 10, 1, 9, tzinfo=UTC)
    subprocess.run([*ALEMBIC, "downgrade", "3b8f1d6a2c47"], check=True, cwd=root)
    with Session(db_engine) as session:
        team = Team(name="팀", retention_days=30)
        session.add(team)
        session.flush()
        team_id = team.id
        columns = {c["name"] for c in sa.inspect(session.connection()).get_columns("ext_materials")}
        session.execute(
            sa.text(
                "INSERT INTO ext_materials (id, team_id, title, source, drive_file_id, "
                "drive_kind, created_at) VALUES "
                "(:up, :team, '계획', 'upload', NULL, NULL, :made), "
                "(:link, :team, '링크', 'drive_link', :file, 'file', :made)"
            ),
            {
                "up": "mat_mig_up",
                "link": "mat_mig_link",
                "team": team_id,
                "made": made,
                "file": "m" * 20,
            },
        )
        session.commit()
    try:
        assert "expires_at" not in columns

        subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)

        with db_engine.connect() as conn:
            days = dict(
                conn.execute(
                    sa.text("SELECT id, expires_at FROM ext_materials WHERE team_id = :team"),
                    {"team": team_id},
                ).all()
            )
        assert days == {"mat_mig_up": made + timedelta(days=30), "mat_mig_link": None}
    finally:
        subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
        with db_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM teams WHERE id = :team"), {"team": team_id})
