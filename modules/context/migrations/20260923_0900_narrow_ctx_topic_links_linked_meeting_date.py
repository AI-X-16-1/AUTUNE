"""narrow ctx_topic_links.linked_meeting_date to a plain date

``linked_meeting_date`` never carried a time or an instant -- it is a KST
calendar date, encoded as midnight UTC so it would fit a ``timestamptz``
column (see ``service._as_datetime``, removed in the same change as this
migration). Every reader immediately called ``.date()`` on it. Left as
``timestamptz``, a DB session whose timezone is not UTC turns that ``.date()``
call into the wrong calendar day. Storing the date directly removes the
ambiguity instead of documenting around it.

The ``USING`` clause pins the conversion to UTC explicitly, regardless of
the session timezone running the migration, since a naive cast would repeat
the exact bug this migration fixes.
Owner: 문민재.

Revision ID: d0e1f2a3b4c5
Revises: c9d8e0f1a2b3
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d8e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ctx_topic_links "
        "ALTER COLUMN linked_meeting_date TYPE date "
        "USING (linked_meeting_date AT TIME ZONE 'UTC')::date"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ctx_topic_links "
        "ALTER COLUMN linked_meeting_date TYPE timestamptz "
        "USING (linked_meeting_date::timestamp AT TIME ZONE 'UTC')"
    )
