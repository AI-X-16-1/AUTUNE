"""establish agent branch: agent_work_items, agent_runs, agent_approvers

The agent layer's own revision chain (ADR 0010, invariant 7). Owner: 김민경.
Every later revision here chains onto this one and touches only agent_* tables.

``depends_on`` pins the core revision that creates ``teams``, ``users`` and
``meetings``, which all three tables reference. The two that copy meeting
content cascade from ``meetings`` (privacy.md section 7).

Revision ID: 1e51a1e8da29
Revises:
Create Date: 2026-09-29 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1e51a1e8da29"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("agent",)
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities

_NOW = sa.text("now()")
_EMPTY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "agent_work_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("origin_meeting", sa.String(64)),
        sa.Column("origin_module", sa.String(32)),
        sa.Column("source_id", sa.String(64)),
        sa.Column("origin_utterances", postgresql.JSONB(), nullable=False, server_default=_EMPTY),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("due_date", sa.Date()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("external_ref", postgresql.JSONB()),
        sa.Column("last_signal_at", sa.DateTime(timezone=True)),
        sa.Column("escalation_lv", sa.Integer(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["origin_meeting"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "kind IN ('action', 'gap', 'open_question', 'decision', 'risk')",
            name="ck_agent_work_items_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'blocked', 'resolved', 'dropped')",
            name="ck_agent_work_items_status",
        ),
    )
    op.create_index("ix_agent_work_items_team_id", "agent_work_items", ["team_id"])
    op.create_index("ix_agent_work_items_origin_meeting", "agent_work_items", ["origin_meeting"])
    op.create_index("ix_agent_work_items_next_check_at", "agent_work_items", ["next_check_at"])
    # The one JSONB column filtered on (data-model.md allows it for that reason).
    op.create_index(
        "ix_agent_work_items_origin_utterances",
        "agent_work_items",
        ["origin_utterances"],
        postgresql_using="gin",
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("meeting_id", sa.String(64)),
        sa.Column("requested_by", sa.String(64)),
        sa.Column("trigger", postgresql.JSONB(), nullable=False),
        sa.Column("route", sa.String(32)),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default=_EMPTY),
        sa.Column("proposed", postgresql.JSONB(), nullable=False, server_default=_EMPTY),
        sa.Column("decisions", postgresql.JSONB(), nullable=False, server_default=_EMPTY),
        sa.Column("actions", postgresql.JSONB(), nullable=False, server_default=_EMPTY),
        sa.Column("messages", postgresql.JSONB(), nullable=False, server_default=_EMPTY),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("token_cost", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_agent_runs_team_id", "agent_runs", ["team_id"])
    op.create_index("ix_agent_runs_meeting_id", "agent_runs", ["meeting_id"])

    op.create_table(
        "agent_approvers",
        sa.Column("team_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "scope IN ('research', 'followup', 'workload', 'any')",
            name="ck_agent_approvers_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_approvers")
    op.drop_index("ix_agent_runs_meeting_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_team_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agent_work_items_origin_utterances", table_name="agent_work_items")
    op.drop_index("ix_agent_work_items_next_check_at", table_name="agent_work_items")
    op.drop_index("ix_agent_work_items_origin_meeting", table_name="agent_work_items")
    op.drop_index("ix_agent_work_items_team_id", table_name="agent_work_items")
    op.drop_table("agent_work_items")
