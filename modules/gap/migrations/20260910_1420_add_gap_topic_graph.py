"""add gap_topics, gap_topic_utterances, gap_topic_edges, gap_participation

This meeting's topic graph, stored as rows and loaded into NetworkX per run
(ADR 0005). The four tables land together because an edge is meaningless
without its endpoints and a participation row is meaningless without its topic
— splitting them would leave a revision in between that no downgrade could
stop at usefully.

``gap_participation.spoke`` is a boolean on purpose. See the model docstring:
a duration or a count here would be a per-person talk-time metric inside a
report the whole team reads (privacy.md section 3).

All four reach deletion through ``meetings.id``. ``depends_on`` pins the core
revision that creates ``meetings``, ``utterances`` and ``participants`` so this
branch cannot run before them on a clean database — see
docs/engineering/migrations.md, "Ordering against the core branch". The rest of
the gap branch chains onto this revision and inherits that ordering.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: a7c3e91b4d20
Revises: 40edab6a5be9
Create Date: 2026-09-10 14:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e91b4d20"
down_revision: str | None = "40edab6a5be9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "d34994600a9a"  # core: shared_entities


def upgrade() -> None:
    op.create_table(
        "gap_topics",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=400), nullable=False),
        sa.Column("centrality", sa.Float(), nullable=False),
        sa.Column("betweenness", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("centrality >= 0 AND centrality <= 1", name="ck_gap_topics_centrality"),
        sa.CheckConstraint(
            "betweenness >= 0 AND betweenness <= 1", name="ck_gap_topics_betweenness"
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gap_topics_meeting_id", "gap_topics", ["meeting_id"])

    op.create_table(
        "gap_topic_utterances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.String(length=64), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["gap_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "utterance_id", name="uq_gap_topic_utterances"),
    )
    op.create_index("ix_gap_topic_utterances_topic_id", "gap_topic_utterances", ["topic_id"])
    op.create_index(
        "ix_gap_topic_utterances_utterance_id", "gap_topic_utterances", ["utterance_id"]
    )

    op.create_table(
        "gap_topic_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meeting_id", sa.String(length=64), nullable=False),
        sa.Column("source_topic_id", sa.String(length=64), nullable=False),
        sa.Column("target_topic_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=100), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.CheckConstraint("weight > 0 AND weight <= 1", name="ck_gap_topic_edges_weight"),
        sa.CheckConstraint(
            "source_topic_id <> target_topic_id", name="ck_gap_topic_edges_no_self_loop"
        ),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_topic_id"], ["gap_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_topic_id"], ["gap_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_topic_id", "target_topic_id", "relation", name="uq_gap_topic_edges"
        ),
    )
    op.create_index("ix_gap_topic_edges_meeting_id", "gap_topic_edges", ["meeting_id"])

    op.create_table(
        "gap_participation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.String(length=64), nullable=False),
        sa.Column("participant_id", sa.String(length=64), nullable=False),
        # Coverage, not volume. A duration or a count belongs to the speaker
        # alone (privacy.md section 3) and this table reaches the whole team.
        sa.Column("spoke", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["gap_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "participant_id", name="uq_gap_participation"),
    )
    op.create_index("ix_gap_participation_topic_id", "gap_participation", ["topic_id"])
    op.create_index("ix_gap_participation_participant_id", "gap_participation", ["participant_id"])


def downgrade() -> None:
    op.drop_table("gap_participation")
    op.drop_table("gap_topic_edges")
    op.drop_table("gap_topic_utterances")
    op.drop_table("gap_topics")
