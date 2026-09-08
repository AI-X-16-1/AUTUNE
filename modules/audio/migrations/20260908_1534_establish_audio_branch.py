"""establish audio branch

Branch anchor for module audio. Empty on purpose: it exists so this module has
an independent revision chain and never collides with another module's
down_revision.

Owner: 김민경. Every later revision here chains onto this one and touches
only aud_* tables. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 997549fc0e39
Revises:
Create Date: 2026-09-08 15:34:58.849891
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "997549fc0e39"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("audio",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
