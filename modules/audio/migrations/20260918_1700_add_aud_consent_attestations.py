"""add aud_consent_attestations

One row per meeting: a team member's statement that everyone in the recording
consented, and who made it and when. The only writer of
``participants.consented = True`` in the repository (#190). Owner: 김민경.

``meeting_id`` is the primary key and cascades from ``meetings`` -- the deletion
path privacy.md section 4 requires. ``attested_by`` is SET NULL on account
deletion: the consent log is kept for audit, the person is not.

``depends_on`` pins the core revision that creates ``meetings`` and ``users``.
The first audio migration to reference a shared entity, and the pin is what
lets ``downgrade core@base`` know this table has to go first -- without it
the sequence CI runs (upgrade heads, downgrade core@base, upgrade heads) dies
on ``DependentObjectsStillExist`` at ``DROP TABLE meetings``. The other three
modules carry the same line on their first such table;
docs/engineering/migrations.md describes the trap.

Revision ID: 3c9d2e1f0a4b
Revises: 997549fc0e39
Create Date: 2026-09-18 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c9d2e1f0a4b"
down_revision: str | None = "997549fc0e39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "aud_consent_attestations",
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("attested_by", sa.String(length=64), nullable=True),
        sa.Column(
            "attested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("meeting_id"),
    )


def downgrade() -> None:
    op.drop_table("aud_consent_attestations")
