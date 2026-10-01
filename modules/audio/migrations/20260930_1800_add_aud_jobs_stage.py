"""add stage and stage_progress to aud_jobs

S12 showed speech recognition, diarization and masking all as "진행" for the
whole of a transcription, because the only thing the worker wrote while it ran
was the meeting's status. These two columns are where it now says which step
it is on and how far through that step it is, so the screen can show a
percentage. Nothing here is meeting content: a step name and a fraction.

Owner: 김민경.

Revision ID: b41e7c2a9d15
Revises: 8f2b6d4a1c93
Create Date: 2026-09-30 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b41e7c2a9d15"
down_revision: str | None = "8f2b6d4a1c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("aud_jobs", sa.Column("stage", sa.String(length=16), nullable=True))
    op.add_column("aud_jobs", sa.Column("stage_progress", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_aud_jobs_stage",
        "aud_jobs",
        "stage IS NULL OR stage IN ('decoding','transcribing','diarizing','masking','saving')",
    )
    op.create_check_constraint(
        "ck_aud_jobs_stage_progress",
        "aud_jobs",
        "stage_progress IS NULL OR (stage_progress >= 0 AND stage_progress <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_aud_jobs_stage_progress", "aud_jobs", type_="check")
    op.drop_constraint("ck_aud_jobs_stage", "aud_jobs", type_="check")
    op.drop_column("aud_jobs", "stage_progress")
    op.drop_column("aud_jobs", "stage")
