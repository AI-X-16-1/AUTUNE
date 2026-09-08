"""establish gap branch

Branch anchor for module gap. Empty on purpose: it exists so this module has
an independent revision chain and never collides with another module's
down_revision.

Owner: 박재경. Every later revision here chains onto this one and touches
only gap_* tables. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 40edab6a5be9
Revises:
Create Date: 2026-09-08 15:34:59.189602
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "40edab6a5be9"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("gap",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
