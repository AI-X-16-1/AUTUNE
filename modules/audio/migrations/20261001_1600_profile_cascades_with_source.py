"""a voice profile row goes with the meeting it was confirmed in

`aud_speaker_embeddings.source_meeting_id` was `ON DELETE SET NULL`, so a
profile row outlived the meeting it came from. `assign_speaker` replaces a
row by `(source_meeting_id, source_speaker_label)`, so once that key was
null the row could never be replaced again: a wrong confirmation stayed in
the person's profile mean for good (#363 item 2).

`CASCADE` instead. A profile is the mean of the rows that remain, so a person
who keeps attending keeps a profile built from their recent meetings, and
one row per meeting now lives exactly as long as that meeting's retention
window. Rows already stranded (`source_meeting_id IS NULL`) are left to the
retention sweep's idle-profile rule.

Owner: 김민경.

Revision ID: c7a1d93e5f20
Revises: b41e7c2a9d15
Create Date: 2026-10-01 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7a1d93e5f20"
down_revision: str | None = "b41e7c2a9d15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "aud_speaker_embeddings_source_meeting_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "aud_speaker_embeddings", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "aud_speaker_embeddings",
        "meetings",
        ["source_meeting_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "aud_speaker_embeddings", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "aud_speaker_embeddings",
        "meetings",
        ["source_meeting_id"],
        ["id"],
        ondelete="SET NULL",
    )
