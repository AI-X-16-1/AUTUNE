"""Tables owned by module E.

Every table name starts with ``intel_``. Foreign keys may reference shared
entities (``meetings.id``, ``teams.id``) but never another module's tables.
Each table needs a path to deletion by meeting_id or user_id.

See docs/architecture/data-model.md.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autune_core import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class IntelCompletion(Base, TimestampMixin):
    """Which of B, C, D have reported for a meeting; drives the aggregate timeout."""

    __tablename__ = "intel_completion"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    extraction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gap_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    aggregated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_payload: Mapped[dict | None] = mapped_column(JSONB)
    gap_payload: Mapped[dict | None] = mapped_column(JSONB)
    context_payload: Mapped[dict | None] = mapped_column(JSONB)


class IntelScore(Base, TimestampMixin):
    """One quality score per meeting; re-aggregation replaces it."""

    __tablename__ = "intel_scores"
    __table_args__ = (
        CheckConstraint("grade IN ('A','B','C','D','E','F')", name="ck_intel_scores_grade"),
        CheckConstraint("value >= 0 AND value <= 1", name="ck_intel_scores_value_unit"),
    )

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    grade: Mapped[str] = mapped_column(String(1), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    decision_density: Mapped[float | None] = mapped_column(Float)
    gap_count: Mapped[int | None] = mapped_column(Integer)
    action_item_completion_rate: Mapped[float | None] = mapped_column(Float)
    participation_balance: Mapped[float | None] = mapped_column(Float)
    missing_sources: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")


class IntelGapPattern(Base, TimestampMixin):
    """Gap classifications, one row per pattern type per meeting."""

    __tablename__ = "intel_gap_patterns"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    pattern_type: Mapped[str] = mapped_column(String(100), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_gap_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    # GapClassifier.model_version at classification time — a distribution that
    # cannot be attributed to a model version cannot be compared to the next one.
    classifier_version: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")


class IntelAlignment(Base, TimestampMixin):
    """Role-pair agreement, one row per unordered role pair per meeting."""

    __tablename__ = "intel_alignment"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="ck_intel_alignment_score_unit"),
    )

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    role_a: Mapped[str] = mapped_column(String(50), primary_key=True)
    role_b: Mapped[str] = mapped_column(String(50), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)


class IntelPrediction(Base, TimestampMixin):
    """One point prediction per kind and horizon per meeting."""

    __tablename__ = "intel_predictions"
    __table_args__ = (
        CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_intel_predictions_probability_unit",
        ),
        CheckConstraint("horizon_days > 0", name="ck_intel_predictions_horizon_positive"),
    )

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(50), primary_key=True)
    horizon_days: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64))


class IntelReport(Base, TimestampMixin):
    """One generated weekly report per team per period."""

    __tablename__ = "intel_reports"

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_meeting_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
