"""aud_team_invitations.email may be NULL: an invitation link made for no address

A link for no address (#552, the module owner's conditions of 2026-10-06):
it works once, for an hour, for whoever opens it signed in. `email` becomes
nullable, and a partial unique index keeps one such link open for an inviter
and a team -- `(team_id, email)` is silent about rows whose `email` is NULL,
and two requests at once must not leave two.

No row changes: every invitation that exists has an address.

Owner: 김민경.

Revision ID: 8b3e5f1c7a26
Revises: f4a0d26b8c39
Create Date: 2026-10-06 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b3e5f1c7a26"
down_revision: str | None = "f4a0d26b8c39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "aud_team_invitations", "email", existing_type=sa.String(length=320), nullable=True
    )
    op.create_index(
        "uq_aud_team_invitations_open_link",
        "aud_team_invitations",
        ["team_id", "invited_by"],
        unique=True,
        postgresql_where=sa.text("email IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_aud_team_invitations_open_link", table_name="aud_team_invitations")
    # A link made for no address has no place in the older table.
    op.execute("DELETE FROM aud_team_invitations WHERE email IS NULL")
    op.alter_column(
        "aud_team_invitations", "email", existing_type=sa.String(length=320), nullable=False
    )
