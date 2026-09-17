"""add gap_topic_edges.extractor_version

Which relation extractor asserted an edge, recorded the way
``gap_topics.extractor_version`` records which entity extractor found a topic.
Gap precision is compared across versions of whatever built the graph (ADR
0006's shape, applied to C's own metric), and after #32 two different things
build it.

Nullable, and the NULL means something: the edge was not asserted by any
extractor. That is the ``co_occurs`` edge, which no model produced — two topics
shared an utterance and the graph wrote the weakest relation there is. A value
means a rule or, later, an assisted extractor said the two topics stand in that
relation, and it says which version did.

Existing rows are all ``co_occurs`` — nothing else could write an edge before
this revision — so they take the NULL and the column needs no backfill.

Chains onto the gap_gaps revision and inherits the ordering against the core
branch from the topic-graph revision.

Owner: 박재경. Apply with `alembic upgrade heads` (plural).

Revision ID: c1f7b0d94e58
Revises: b8d4fa2c5e31
Create Date: 2026-09-17 10:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f7b0d94e58"
down_revision: str | None = "b8d4fa2c5e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gap_topic_edges",
        sa.Column("extractor_version", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gap_topic_edges", "extractor_version")
