"""establish intelligence branch

Branch anchor for module intelligence. Empty on purpose: it exists so this module has
an independent revision chain and never collides with another module's
down_revision.

Owner: 이승환. Every later revision here chains onto this one and touches
only intel_* tables. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4e64e50ad2cf
Revises:
Create Date: 2026-09-08 15:34:59.530068
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "4e64e50ad2cf"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("intelligence",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
