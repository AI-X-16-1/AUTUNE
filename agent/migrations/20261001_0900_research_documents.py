"""research documents: agent_research_documents, agent_research_sources, delete trigger

A document goes when any meeting it quotes goes. A foreign key cascades from one
parent only, so a trigger on agent_research_sources deletes the parent document
when a source row is deleted -- by a meeting's cascade or the retention sweep.

Revision ID: 7c2d4e9a1b30
Revises: 1e51a1e8da29
Create Date: 2026-10-01 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2d4e9a1b30"
down_revision: str | None = "1e51a1e8da29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "agent_research_documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64)),
        sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('proposed', 'approved', 'rejected')", name="ck_agent_research_status"
        ),
    )
    op.create_index("ix_agent_research_documents_team_id", "agent_research_documents", ["team_id"])
    op.create_index(
        "ix_agent_research_documents_meeting_id", "agent_research_documents", ["meeting_id"]
    )
    op.create_table(
        "agent_research_sources",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("meeting_id", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["agent_research_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_agent_research_sources_meeting_id", "agent_research_sources", ["meeting_id"]
    )
    op.execute(
        """
        CREATE FUNCTION agent_research_drop_document() RETURNS trigger AS $$
        BEGIN
            DELETE FROM agent_research_documents WHERE id = OLD.document_id;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_research_sources_drop_document
        AFTER DELETE ON agent_research_sources
        FOR EACH ROW EXECUTE FUNCTION agent_research_drop_document()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER agent_research_sources_drop_document ON agent_research_sources")
    op.execute("DROP FUNCTION agent_research_drop_document()")
    op.drop_table("agent_research_sources")
    op.drop_table("agent_research_documents")
