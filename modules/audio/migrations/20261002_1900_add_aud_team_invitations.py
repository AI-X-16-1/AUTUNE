"""add aud_team_invitations

A pending invitation to a team (#552): the team, the invited address, who
invited, when it lapses, and the SHA-256 of the link's token -- never the
token. Not a membership: nothing reads this table as one.

The address is a third party's, so the row has four ways out besides being
accepted: it lapses (`expires_at`, removed by the retention sweep), the team
goes (`team_id` CASCADE), the inviter's account goes (`invited_by` CASCADE),
or the invited person deletes their own account (module A deletes by address).

Owner: 김민경.

Revision ID: e3f9c15a7b28
Revises: d2e8b04f6a17
Create Date: 2026-10-02 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f9c15a7b28"
down_revision: str | None = "d2e8b04f6a17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aud_team_invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "email", name="uq_aud_team_invitations_team_email"),
        sa.UniqueConstraint("token_hash", name="uq_aud_team_invitations_token_hash"),
    )
    op.create_index("ix_aud_team_invitations_team_id", "aud_team_invitations", ["team_id"])
    op.create_index("ix_aud_team_invitations_invited_by", "aud_team_invitations", ["invited_by"])
    op.create_index("ix_aud_team_invitations_expires_at", "aud_team_invitations", ["expires_at"])
    op.create_index("ix_aud_team_invitations_email", "aud_team_invitations", ["email"])


def downgrade() -> None:
    op.drop_index("ix_aud_team_invitations_email", table_name="aud_team_invitations")
    op.drop_index("ix_aud_team_invitations_expires_at", table_name="aud_team_invitations")
    op.drop_index("ix_aud_team_invitations_invited_by", table_name="aud_team_invitations")
    op.drop_index("ix_aud_team_invitations_team_id", table_name="aud_team_invitations")
    op.drop_table("aud_team_invitations")
