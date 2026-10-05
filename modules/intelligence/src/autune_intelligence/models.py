"""Tables owned by module E.

Every table name starts with ``intel_``. Foreign keys may reference shared
entities (``meetings.id``, ``teams.id``) but never another module's tables.
Each table needs a path to deletion by meeting_id or user_id.

See docs/architecture/data-model.md.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
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
    # Mean of Classification.confidence over this pattern's gaps this meeting.
    # NULL on rows written before this column existed (same rows classifier_version
    # == "" identifies) — there is no confidence to recover for those.
    avg_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


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


class IntelMeetingReport(Base, TimestampMixin):
    """One summary report per meeting, composed by the Report subagent.

    agent-layer.md section 3.1: the subagent writes the body, E stores and posts
    it (section 8 rule 2). Keyed by meeting so a meeting is reported once, and
    cascaded from ``meetings`` so deleting a meeting deletes its report -- the
    per-meeting deletion path ``intel_reports`` lacks (#86).
    """

    __tablename__ = "intel_meeting_reports"

    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    slack_channel: Mapped[str | None] = mapped_column(String(64))
    slack_ts: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set when a task claims the report, before it posts: a set value means
    never post again, even if that post failed (at most once)."""
    pending_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    """B had items awaiting a person's review when the report was composed.
    The post then carries a second button to B's review board; the items
    themselves are never quoted (agent-layer.md section 8 rule 3)."""
    draft_id: Mapped[str | None] = mapped_column(String(64))
    """The Report run that wrote this draft. A post approved for one draft id
    is refused once a later run has replaced the draft (review of #508)."""
    edited_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )
    """The team member who last edited the draft on the dashboard, if anyone.
    A per-person record, so it goes with the person; the report stays."""
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_body: Mapped[str | None] = mapped_column(Text)
    """The latest correction a member wrote after the report was posted. It goes
    out as a reply under the post once approved (#674); the post itself is never
    changed."""
    correction_id: Mapped[str | None] = mapped_column(String(64))
    """Names this correction, as ``draft_id`` names a draft: the approval pins it,
    so a correction written after the approval is not posted under it."""
    corrected_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set when a task claims the correction, before it posts: at most once."""
    correction_slack_ts: Mapped[str | None] = mapped_column(String(64))
    correction_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set when an approved correction could not be posted at all -- the team's
    Slack was disconnected after the report went out (#698). It reads as failed
    at once, and the next correction clears it."""
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The person's change (``edited_at``, or ``corrected_at`` after the post)
    that the last announcement covered. A later change not covered by it is
    announced again by the sweep, so a lost enqueue does not lose the approval
    request (#698)."""


class IntelReport(Base, TimestampMixin):
    """One generated weekly report per team per period.

    ``posted_at`` is set when the post to the team's channel is claimed, before
    it is sent, and cleared if sending fails, so a report goes out once
    (#227). ``not_posted`` says why one never will: ``"empty"`` for a week
    with nothing to say on a team that did not ask for those, ``"refused"``
    for a body the outbound check refused (#821 review).
    """

    __tablename__ = "intel_reports"

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_meeting_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_posted: Mapped[str | None] = mapped_column(String(16))


class IntelTeamSettings(Base, TimestampMixin):
    """When a team's weekly report goes out, and whether an empty week does (#227).

    No row means the defaults: Monday 09:00 KST, empty weeks not posted. Any
    member may change it, as any member may change the team's retention;
    ``updated_by`` says who did. Deleted with its team.
    """

    __tablename__ = "intel_team_settings"
    __table_args__ = (
        CheckConstraint(
            "weekly_report_weekday BETWEEN 0 AND 6", name="ck_intel_team_settings_weekday"
        ),
        CheckConstraint("weekly_report_hour BETWEEN 0 AND 23", name="ck_intel_team_settings_hour"),
    )

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    weekly_report_weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    """0 is Monday, as ``date.weekday``."""
    weekly_report_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    """The hour in Korean time (``ACTION_PROGRESS_TODAY_ZONE``)."""
    weekly_report_send_empty: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )


class IntelActionProgress(Base, TimestampMixin):
    """The latest ``TeamActionProgress`` B sent for a team: when it was taken (#605).

    The header exists even when the snapshot listed no meeting, so "fresh and
    empty" (nothing confirmed in the window) stays apart from "never received" and
    from "stale". Deleted with its team.
    """

    __tablename__ = "intel_action_progress"

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntelActionProgressMeeting(Base):
    """One meeting's counts from that snapshot. Counts only -- no assignee, no title.

    Cascaded from ``meetings``, so a deleted meeting's counts go with it rather
    than waiting for the next snapshot (#86), and from ``teams`` like the header.
    Foreign keys point at shared tables only, so the header is not referenced;
    a kept snapshot replaces the team's rows itself. Shown only as team totals
    (the contract's usage rule, #605 review).
    """

    __tablename__ = "intel_action_progress_meetings"
    __table_args__ = (
        CheckConstraint("confirmed >= 1", name="ck_intel_action_progress_confirmed"),
        CheckConstraint("done >= 0 AND done <= confirmed", name="ck_intel_action_progress_done"),
        CheckConstraint(
            "overdue >= 0 AND overdue <= confirmed - done", name="ck_intel_action_progress_overdue"
        ),
    )

    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    confirmed: Mapped[int] = mapped_column(Integer, nullable=False)
    done: Mapped[int] = mapped_column(Integer, nullable=False)
    overdue: Mapped[int] = mapped_column(Integer, nullable=False)
