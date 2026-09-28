"""ext_action_item_sources outlives the utterance it pointed at (ADR 0007, #378)

ADR 0007, "Missing attribution is shown, not hidden": if an utterance is
genuinely deleted, the item keeps its statement and says the source is gone.
Until now the link row cascaded away with the utterance, so a model item whose
transcript was deleted came back with an empty source list -- the same shape
as a hand-added item, and S18's drawer said exactly that: "직접 추가한 항목".

The link row now survives with ``utterance_id`` set to NULL. It holds no
content -- only that a quotation existed -- so ``privacy.md``'s "no tombstones
holding content" is not in play; the words themselves still go with the
utterance.

The unique constraint on (action_item_id, utterance_id) stays: PostgreSQL
treats NULLs as distinct, so two deleted sources on one item do not collide.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 4f0b7d9e2c61
Revises: 35285ee67043
Create Date: 2026-09-27 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f0b7d9e2c61"
down_revision: str | None = "35285ee67043"  # extraction: decision_reviews_fk
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The original table (20260909_1400) left this constraint unnamed, so it has
# PostgreSQL's default name.
_OLD_FK = "ext_action_item_sources_utterance_id_fkey"
_NEW_FK = "fk_ext_action_item_sources_utterance_id"


def upgrade() -> None:
    op.drop_constraint(_OLD_FK, "ext_action_item_sources", type_="foreignkey")
    op.alter_column(
        "ext_action_item_sources", "utterance_id", existing_type=sa.String(64), nullable=True
    )
    op.create_foreign_key(
        _NEW_FK,
        "ext_action_item_sources",
        "utterances",
        ["utterance_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # A NULL link cannot satisfy NOT NULL again, and under the old rule it
    # would not exist: the cascade would have taken it with the utterance.
    op.execute("DELETE FROM ext_action_item_sources WHERE utterance_id IS NULL")
    op.drop_constraint(_NEW_FK, "ext_action_item_sources", type_="foreignkey")
    op.alter_column(
        "ext_action_item_sources", "utterance_id", existing_type=sa.String(64), nullable=False
    )
    op.create_foreign_key(
        _OLD_FK,
        "ext_action_item_sources",
        "utterances",
        ["utterance_id"],
        ["id"],
        ondelete="CASCADE",
    )
