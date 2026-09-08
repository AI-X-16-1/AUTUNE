"""establish extraction branch

Branch anchor for module extraction. Empty on purpose: it exists so this module has
an independent revision chain and never collides with another module's
down_revision.

Owner: 강민구. Every later revision here chains onto this one and touches
only ext_* tables. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 88a66e7bcaf4
Revises:
Create Date: 2026-09-08 15:34:59.016187
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "88a66e7bcaf4"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("extraction",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
