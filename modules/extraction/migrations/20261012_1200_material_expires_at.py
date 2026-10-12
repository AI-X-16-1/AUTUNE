"""ext_materials.expires_at: an upload's day is fixed when it is uploaded

An uploaded material (#817) keeps the team's retention window, counted from
the upload. Until now that was worked out each time from ``created_at`` and
the team's window of the day, so a team shortening its window deleted earlier
uploads at the next hourly run without anyone deciding to. The day is now
written on the row at the upload -- that moment plus ``teams.retention_days``
-- and nothing moves it afterwards, as a meeting's ``expires_at`` is fixed
when it is held (the owner, 2026-10-12; docs/architecture/privacy.md,
section 4).

An upload always has the day, whether or not its team keeps uploads until a
member deletes them; a Drive link keeps no text and never has one
(``ck_ext_materials_expires``).

An upload already in the table gets its upload plus its team's window as it
is when this runs -- what the code before this promised it. That reads
``teams.retention_days`` and writes only ``ext_materials``.

The downgrade drops the column; the code before this did not read it.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: c6e1f4a9d2b8
Revises: 3b8f1d6a2c47
Create Date: 2026-10-12 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6e1f4a9d2b8"
down_revision: str | None = "3b8f1d6a2c47"  # extraction: material settings
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_materials", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE ext_materials AS m "
        "SET expires_at = m.created_at + make_interval(days => t.retention_days) "
        "FROM teams AS t WHERE t.id = m.team_id AND m.source = 'upload'"
    )
    op.create_check_constraint(
        "ck_ext_materials_expires",
        "ext_materials",
        "(source = 'drive_link' AND expires_at IS NULL)"
        " OR (source = 'upload' AND expires_at IS NOT NULL)",
    )
    op.create_index("ix_ext_materials_expires_at", "ext_materials", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_ext_materials_expires_at", table_name="ext_materials")
    op.drop_constraint("ck_ext_materials_expires", "ext_materials", type_="check")
    op.drop_column("ext_materials", "expires_at")
