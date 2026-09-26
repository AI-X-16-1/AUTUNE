"""ext_decision_reviews.decision_id gets a real foreign key to ext_decisions (#297)

build_decisions used to delete and rebuild every model-made decision on a
rerun, so a foreign key from ext_decision_reviews would have taken every
review with it and the column stayed a bare dec_ id, diffed by hand against a
"kept" list to know which reviews to delete.

That id is derived from the meeting and its source utterances (#193), so a
rebuild over unchanged sources gives the same id -- build_decisions now
upserts by that id instead of deleting and reinserting, and the row a review
points at survives a rebuild whenever its sources do. The foreign key can
therefore hold, and ON DELETE CASCADE replaces the hand-rolled diff for the
one case a decision's id really does disappear: its sources changed, or it
was a proposal the model no longer makes.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 35285ee67043
Revises: 8d41c7e2a9f0
Create Date: 2026-09-22 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "35285ee67043"
down_revision: str | None = "8d41c7e2a9f0"  # extraction: external_refs
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A review whose decision has already disappeared under the old delete-
    # and-rebuild rule cannot be added with a foreign key that requires the
    # row it points at to exist. build_decisions never recreated an orphaned
    # review, so any left over point at nothing and predate this migration.
    op.execute(
        "DELETE FROM ext_decision_reviews WHERE decision_id NOT IN (SELECT id FROM ext_decisions)"
    )
    op.create_foreign_key(
        "fk_ext_decision_reviews_decision_id",
        "ext_decision_reviews",
        "ext_decisions",
        ["decision_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ext_decision_reviews_decision_id", "ext_decision_reviews", type_="foreignkey"
    )
