"""ext_notion_targets: whether a team's databases have the 내용 property

A page's title is becoming the item's short title, with the sentence in a text
property named 내용. A database made from now on has that property; one made
before does not, and Notion refuses a page that names a property its database
lacks -- so a sync must know, per team and per database, before it names it.

``content_asked_at`` is when Notion last answered about the two databases, and
``action_content`` / ``decision_content`` are what it answered. ``NULL`` and
false for every existing row: each team's next sync asks once, adds the
property where it is missing, and records the answer here. Nothing is asked by
this migration.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3f8d1a6c5b27
Revises: 9e2b6d4f8a13
Create Date: 2026-10-09 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f8d1a6c5b27"
down_revision: str | None = "9e2b6d4f8a13"  # extraction: a short title beside the sentence
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ext_notion_targets",
        sa.Column("content_asked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ext_notion_targets",
        sa.Column("action_content", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "ext_notion_targets",
        sa.Column("decision_content", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ext_notion_targets", "decision_content")
    op.drop_column("ext_notion_targets", "action_content")
    op.drop_column("ext_notion_targets", "content_asked_at")
