"""ext_action_item_sources and ext_decision_sources say which part of the utterance was used

A turn over 300 characters is read sentence by sentence and each promise or
decision in it is a row of its own, but the row cited the whole utterance and
the sentence it was made from was not kept: a reader opening the item was
quoted a minute of speech to find one line in (the user, 2026-10-08).

Two nullable integers on each source row: where the part starts and ends in
the utterance's stored text. No text is copied -- the words stay in
``utterances`` alone, and what a person is shown is cut from there when they
ask (``autune_extraction.excerpt``).

Existing rows stay NULL, which reads as the whole utterance, as before. A run
of the meeting fills them (``service.build_action_items``,
``service.build_decisions``).

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 2b9e6d4f8a13
Revises: 7a2d4c9e1b63
Create Date: 2026-10-08 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b9e6d4f8a13"
down_revision: str | None = "7a2d4c9e1b63"  # extraction: edit event kind 'closed' (#856)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("ext_action_item_sources", "ext_decision_sources")
_CHECK = (
    "(excerpt_start IS NULL AND excerpt_end IS NULL) "
    "OR (excerpt_start >= 0 AND excerpt_end > excerpt_start)"
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("excerpt_start", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("excerpt_end", sa.Integer(), nullable=True))
        op.create_check_constraint(f"ck_{table}_excerpt", table, _CHECK)


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_excerpt", table, type_="check")
        op.drop_column(table, "excerpt_end")
        op.drop_column(table, "excerpt_start")
