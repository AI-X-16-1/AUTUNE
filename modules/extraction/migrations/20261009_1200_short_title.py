"""ext_action_items.title, ext_decisions.title: a short title beside the sentence

The card and the decision row show twenty characters, and until now that was
the stored sentence cut off. A title is a summary of it instead -- twenty
characters or fewer, ended by a noun -- written by a model
(``title_impl=llm``) and kept only when it passed the rules in
``pipeline.title``. The sentence itself is not touched and stays what every
message, tool and module is given.

Nullable, and ``NULL`` for every existing row: they show the sentence cut, as
they did, until the meeting is extracted again. Nothing is backfilled -- a
title needs a model's answer.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9e2b6d4f8a13
Revises: 7a3c5e9d1f42
Create Date: 2026-10-09 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e2b6d4f8a13"
down_revision: str | None = "7a3c5e9d1f42"  # extraction: a meeting too long for a summary
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ext_action_items", sa.Column("title", sa.String(length=40), nullable=True))
    op.add_column("ext_decisions", sa.Column("title", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("ext_decisions", "title")
    op.drop_column("ext_action_items", "title")
