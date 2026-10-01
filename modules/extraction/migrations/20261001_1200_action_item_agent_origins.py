"""ext_action_items.origin: 'chat' and 'followup' beside 'model' and 'user'

An item the agent layer adds is neither the pipeline's draft nor a person's
typing. 'followup' is the Follow-up subagent's "후속 회의 잡기" (#561), and its
open-item read keys on it; 'chat' is an item drafted from an utterance in the
chat. Kept apart from 'user' because edit cost counts a 'user' item as one the
model missed. A rerun of the pipeline replaces only 'model' rows, so both
survive it the way a person's do.

Owner: 강민구. Apply with `alembic upgrade heads` (plural).
See docs/engineering/migrations.md.

Revision ID: 3b8d6f1a9c42
Revises: 7e3f0b5c8d26
Create Date: 2026-10-01 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3b8d6f1a9c42"
down_revision: str | None = "7e3f0b5c8d26"  # extraction: decision_original_and_related
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ck_ext_action_items_origin"


def upgrade() -> None:
    op.drop_constraint(NAME, "ext_action_items", type_="check")
    op.create_check_constraint(
        NAME, "ext_action_items", "origin IN ('model','user','chat','followup')"
    )


def downgrade() -> None:
    # Fails on purpose while a 'chat' or 'followup' row exists: there is no
    # older value that would not misreport who made it.
    op.drop_constraint(NAME, "ext_action_items", type_="check")
    op.create_check_constraint(NAME, "ext_action_items", "origin IN ('model','user')")
