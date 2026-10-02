"""one proposed research document per meeting: a partial unique index

The save overwrites a meeting's proposed document in code; two runs saving at
once could both insert. Any duplicate already present keeps its newest row as
the proposal and the older ones become ``rejected``, which no approver sees.

Revision ID: 8d4b1f6e2a53
Revises: 3f8a2c1d9e47
Create Date: 2026-10-03 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d4b1f6e2a53"
down_revision: str | None = "3f8a2c1d9e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agent_research_documents AS d SET status = 'rejected'
        WHERE d.status = 'proposed' AND EXISTS (
            SELECT 1 FROM agent_research_documents AS newer
            WHERE newer.meeting_id = d.meeting_id
              AND newer.status = 'proposed'
              AND (newer.created_at, newer.id) > (d.created_at, d.id)
        )
        """
    )
    op.create_index(
        "uq_agent_research_one_proposed",
        "agent_research_documents",
        ["meeting_id"],
        unique=True,
        postgresql_where=sa.text("status = 'proposed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_research_one_proposed", table_name="agent_research_documents")
