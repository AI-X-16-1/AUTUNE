"""live research: agent_live_research, its sources with a delete trigger, notices

A live document goes with its meeting (FK), with any meeting it quotes (the
trigger, as for agent_research_sources), and with a person's speech (the
agent_live speech hook). Live-research spec section 4.

Revision ID: b6e2f4a8c1d7
Revises: a3d7c5e19f08
Create Date: 2026-10-09 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6e2f4a8c1d7"
down_revision: str | None = "a3d7c5e19f08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "agent_live_research",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("meeting_id", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(64)),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("web_sources", postgresql.JSONB(), nullable=False),
        sa.Column("meeting_sources", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("origin IN ('auto', 'manual')", name="ck_agent_live_research_origin"),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')", name="ck_agent_live_research_status"
        ),
    )
    op.create_index("ix_agent_live_research_team_id", "agent_live_research", ["team_id"])
    op.create_index("ix_agent_live_research_meeting_id", "agent_live_research", ["meeting_id"])
    op.create_table(
        "agent_live_research_sources",
        sa.Column("document_id", sa.String(64), primary_key=True),
        sa.Column("meeting_id", sa.String(64), primary_key=True),
        sa.ForeignKeyConstraint(["document_id"], ["agent_live_research.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_agent_live_research_sources_meeting_id", "agent_live_research_sources", ["meeting_id"]
    )
    op.create_table(
        "agent_live_research_notices",
        sa.Column("meeting_id", sa.String(64), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
    )
    op.execute(
        """
        CREATE FUNCTION agent_live_research_drop_document() RETURNS trigger AS $$
        BEGIN
            DELETE FROM agent_live_research WHERE id = OLD.document_id;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_live_research_sources_drop_document
        AFTER DELETE ON agent_live_research_sources
        FOR EACH ROW EXECUTE FUNCTION agent_live_research_drop_document()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER agent_live_research_sources_drop_document ON agent_live_research_sources"
    )
    op.execute("DROP FUNCTION agent_live_research_drop_document()")
    op.drop_table("agent_live_research_notices")
    op.drop_table("agent_live_research_sources")
    op.drop_table("agent_live_research")
