"""ext_materials takes an upload; ext_material_chunks, ext_material_alarms

A member may upload a file to the team's 자료 (#817). The file is never
written anywhere: its text is read inside the upload request, masked, and
only the masked text is kept, in pieces (``ext_material_chunks``). A row of
``ext_materials`` is now either a Drive link (#1016) or an upload, told apart
by ``source``; an upload has no Drive columns. A file stopped for a
confidentiality marking leaves one ``ext_material_alarms`` row -- the team,
the time and the kind of marking, and nothing else.

No column names a person. Everything goes with the team (``ON DELETE
CASCADE`` from ``teams``), and a material's pieces go with it. Existing rows
are links and become ``source='drive_link'``.

The downgrade deletes every upload first -- a link row needs both Drive
columns -- and with it every piece of masked text.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 5d1a7c3e9b24
Revises: 9e2b6d4f8a13
Create Date: 2026-10-10 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d1a7c3e9b24"
down_revision: str | None = "9e2b6d4f8a13"  # extraction: a short title beside the sentence
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_materials",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="drive_link"),
    )
    op.alter_column("ext_materials", "drive_file_id", nullable=True)
    op.alter_column("ext_materials", "drive_kind", nullable=True)
    op.create_check_constraint(
        "ck_ext_materials_source",
        "ext_materials",
        "(source = 'drive_link' AND drive_file_id IS NOT NULL AND drive_kind IS NOT NULL)"
        " OR (source = 'upload' AND drive_file_id IS NULL AND drive_kind IS NULL)",
    )

    op.create_table(
        "ext_material_chunks",
        sa.Column("material_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["ext_materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("material_id", "position"),
    )

    op.create_table(
        "ext_material_alarms",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("marking", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "marking IN ('korean_marking','english_marking')",
            name="ck_ext_material_alarms_marking",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ext_material_alarms_team_id", "ext_material_alarms", ["team_id"])
    op.create_index("ix_ext_material_alarms_created_at", "ext_material_alarms", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ext_material_alarms_created_at", table_name="ext_material_alarms")
    op.drop_index("ix_ext_material_alarms_team_id", table_name="ext_material_alarms")
    op.drop_table("ext_material_alarms")
    op.drop_table("ext_material_chunks")
    op.execute("DELETE FROM ext_materials WHERE source = 'upload'")
    op.drop_constraint("ck_ext_materials_source", "ext_materials", type_="check")
    op.alter_column("ext_materials", "drive_kind", nullable=False)
    op.alter_column("ext_materials", "drive_file_id", nullable=False)
    op.drop_column("ext_materials", "source")
