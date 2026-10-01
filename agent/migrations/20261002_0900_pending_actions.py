"""pending actions: agent_pending_actions; `report` becomes an approver scope

Plan mode's queue (agent/docs/specs/2026-09-30-plan-mode-design.md section 4).
No text is stored; rows cascade from meetings and teams.

Revision ID: 3f8a2c1d9e47
Revises: 7c2d4e9a1b30
Create Date: 2026-10-02 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3f8a2c1d9e47"
down_revision: str | None = "7c2d4e9a1b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")
_OLD_SCOPES = "scope IN ('research', 'followup', 'workload', 'any')"
_NEW_SCOPES = "scope IN ('research', 'followup', 'workload', 'report', 'any')"


def upgrade() -> None:
    op.create_table(
        "agent_pending_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("meeting_id", sa.String(64)),
        sa.Column("run_id", sa.String(64)),
        sa.Column("subagent", sa.String(32), nullable=False),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reject_reason", sa.String(32)),
        sa.Column("result_ok", sa.Boolean()),
        sa.Column("result_reason", sa.String(128)),
        sa.Column("decided_by", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded', 'failed')",
            name="ck_agent_pending_status",
        ),
        sa.CheckConstraint(
            "reject_reason IS NULL OR reject_reason IN "
            "('wrong_evidence', 'not_now', 'handled_elsewhere', 'other')",
            name="ck_agent_pending_reject_reason",
        ),
    )
    op.create_index("ix_agent_pending_team_status", "agent_pending_actions", ["team_id", "status"])
    op.create_index("ix_agent_pending_actions_meeting_id", "agent_pending_actions", ["meeting_id"])
    op.drop_constraint("ck_agent_approvers_scope", "agent_approvers", type_="check")
    op.create_check_constraint("ck_agent_approvers_scope", "agent_approvers", _NEW_SCOPES)


def downgrade() -> None:
    op.drop_constraint("ck_agent_approvers_scope", "agent_approvers", type_="check")
    op.execute("DELETE FROM agent_approvers WHERE scope = 'report'")
    op.create_check_constraint("ck_agent_approvers_scope", "agent_approvers", _OLD_SCOPES)
    op.drop_table("agent_pending_actions")
