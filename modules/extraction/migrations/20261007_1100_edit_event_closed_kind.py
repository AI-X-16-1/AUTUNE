"""ext_edit_events takes a fourth kind, 'closed': an item closed without being finished

No new table and no new column: the CHECK on ``kind`` gains one value. A
``closed`` row says that an item was closed without being finished, and when --
about the item, and like every row of this table never who (#856; the user,
2026-10-07). It is what lets the morning DM and the work-report draft tell a
close from work a person finished.

Downgrade puts the three-kind CHECK back, so it first removes the ``closed``
rows: they cannot be kept under it, and an item closed that way then reads as
finished again, as it did before this revision.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 7a2d4c9e1b63
Revises: 5c8f2a7d1e94
Create Date: 2026-10-07 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7a2d4c9e1b63"
down_revision: str | None = "5c8f2a7d1e94"  # extraction: meeting notices (#953)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_ext_edit_events_kind"


def upgrade() -> None:
    op.drop_constraint(_NAME, "ext_edit_events", type_="check")
    op.create_check_constraint(
        _NAME, "ext_edit_events", "kind IN ('created','deleted','edited','closed')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ext_edit_events WHERE kind = 'closed'")
    op.drop_constraint(_NAME, "ext_edit_events", type_="check")
    op.create_check_constraint(_NAME, "ext_edit_events", "kind IN ('created','deleted','edited')")
