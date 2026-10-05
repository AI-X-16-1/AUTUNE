"""team_members.pinned_at

When a person pinned this team to the top of their own team list, or NULL.
A person's teams are listed pinned first -- in the order they were pinned --
and then in the order they were joined; the first is the default the screens
take. The column is on the membership because a pin is one person's view of
one team: it goes with the membership and nobody else reads it.

Nullable with no default: applying this pins nothing, so every list keeps the
join order it had.

Revision ID: f2a6b9d4c8e1
Revises: 7c2f9a4e1b85
Create Date: 2026-10-02 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6b9d4c8e1"
down_revision: str | None = "7c2f9a4e1b85"  # core: user_integrations gmail_send (#760)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("team_members", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("team_members", "pinned_at")
