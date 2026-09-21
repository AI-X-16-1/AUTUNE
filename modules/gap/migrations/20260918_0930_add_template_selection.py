"""template attribution on gap_gaps, and the per-meeting template override

Step 6 of the pipeline needs two things the schema did not have: which
checklist raised a gap, and which one a meeting is compared against.

``template_key`` / ``template_version`` / ``template_item_key`` are nullable
because a gap found from the graph alone carries none of them — the same reason
``template_item`` is nullable. ``uq_gap_gaps_template_item`` is what lets a
re-run recognise a gap it already raised and leave its id and its dismissal
alone; NULLs do not collide in PostgreSQL, so graph-only gaps are unconstrained
by it.

``gap_meeting_template`` stores only the exception. A meeting with no row gets
``AUTUNE_GAP_DEFAULT_TEMPLATE``, so changing the default reaches every meeting
that never expressed a preference. There is no ``gap_templates`` table and this
revision does not add one — templates are files in the package, versioned by
git (#22).

Chains onto the gaps revision and inherits its ``depends_on`` on core.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: c9e5ab13d742
Revises: b8d4fa2c5e31
Create Date: 2026-09-18 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e5ab13d742"
down_revision: str | None = "b8d4fa2c5e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gap_gaps", sa.Column("template_key", sa.String(length=100), nullable=True))
    op.add_column("gap_gaps", sa.Column("template_version", sa.String(length=100), nullable=True))
    op.add_column("gap_gaps", sa.Column("template_item_key", sa.String(length=100), nullable=True))
    op.create_unique_constraint(
        "uq_gap_gaps_template_item",
        "gap_gaps",
        ["meeting_id", "template_key", "template_item_key"],
    )

    op.create_table(
        "gap_meeting_template",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("gap_meeting_template")
    op.drop_constraint("uq_gap_gaps_template_item", "gap_gaps", type_="unique")
    op.drop_column("gap_gaps", "template_item_key")
    op.drop_column("gap_gaps", "template_version")
    op.drop_column("gap_gaps", "template_key")
