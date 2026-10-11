"""ext_decision_sources outlives the utterance it pointed at (#400)

What ``4f0b7d9e2c61`` did for ``ext_action_item_sources`` (ADR 0007, "Missing
attribution is shown, not hidden"), for a decision.

The link row cascaded away with the utterance. Module A's rerun of a meeting
replaces every utterance, and a decision a person added is not rebuilt, so it
came back with an empty source list -- the same shape as a decision added
without pointing at any line. A model's decision whose speaker deleted their
own data lost the link the same way and read "근거 발화 0건".

The link row now survives with ``utterance_id`` set to NULL. It holds no
content and no person: the utterance's id is gone with it, and what stays is
the row's place among the decision's sources and, when one was recorded, two
offsets into a text that no longer exists. The words themselves still go with
the utterance.

The unique constraint on (decision_id, utterance_id) stays: PostgreSQL treats
NULLs as distinct, so two deleted sources on one decision do not collide.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 9c4e7a1d5b28
Revises: 2b9e6d4f8a13
Create Date: 2026-10-08 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4e7a1d5b28"
down_revision: str | None = "2b9e6d4f8a13"  # extraction: source excerpt (#1017)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The original table (20260909_1830) left this constraint unnamed, so it has
# PostgreSQL's default name.
_OLD_FK = "ext_decision_sources_utterance_id_fkey"
_NEW_FK = "fk_ext_decision_sources_utterance_id"


def upgrade() -> None:
    op.drop_constraint(_OLD_FK, "ext_decision_sources", type_="foreignkey")
    op.alter_column(
        "ext_decision_sources", "utterance_id", existing_type=sa.String(64), nullable=True
    )
    op.create_foreign_key(
        _NEW_FK,
        "ext_decision_sources",
        "utterances",
        ["utterance_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # A NULL link cannot satisfy NOT NULL again, and under the old rule it
    # would not exist: the cascade would have taken it with the utterance.
    op.execute("DELETE FROM ext_decision_sources WHERE utterance_id IS NULL")
    op.drop_constraint(_NEW_FK, "ext_decision_sources", type_="foreignkey")
    op.alter_column(
        "ext_decision_sources", "utterance_id", existing_type=sa.String(64), nullable=False
    )
    op.create_foreign_key(
        _OLD_FK,
        "ext_decision_sources",
        "utterances",
        ["utterance_id"],
        ["id"],
        ondelete="CASCADE",
    )
