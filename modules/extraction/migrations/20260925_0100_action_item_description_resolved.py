"""add ext_action_items.description_resolved (#175, #366)

True when description is ReferenceResolver's rewrite rather than the source
utterance verbatim, so S18 can show a reviewer which descriptions are the
speaker's own words and which are a model's paraphrase of them worth a closer
look. Not nullable: every existing row's description is the raw quote it has
always been, so False is a true default, not a placeholder for "unknown".

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: e92331919850
Revises: 35285ee67043
Create Date: 2026-09-25 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e92331919850"
down_revision: str | None = "35285ee67043"  # extraction: decision_reviews_fk
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_action_items",
        sa.Column("description_resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ext_action_items", "description_resolved")
