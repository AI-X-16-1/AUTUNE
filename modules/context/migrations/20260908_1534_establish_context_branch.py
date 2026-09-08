"""establish context branch

Branch anchor for module context. Empty on purpose: it exists so this module has
an independent revision chain and never collides with another module's
down_revision.

Owner: 문민재. Every later revision here chains onto this one and touches
only ctx_* tables. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: aea84543c9c3
Revises:
Create Date: 2026-09-08 15:34:59.362984
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "aea84543c9c3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("context",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
