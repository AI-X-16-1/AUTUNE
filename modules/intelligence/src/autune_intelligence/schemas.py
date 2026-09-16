"""Internal schemas for module E.

Anything another module needs belongs in ``packages/contracts``, not here.
These are the response bodies for this module's own read API — the per-meeting
score, the team dashboard, the alignment heatmap, and the weekly reports. No
other service parses them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScoreRead(BaseModel):
    """One meeting's quality score and the components behind it.

    ``None`` on a component means "not measured" (the source was missing, or had
    nothing to score), which the dashboard renders differently from a low score.
    """

    model_config = ConfigDict(from_attributes=True)

    meeting_id: str
    team_id: str
    grade: str
    value: float
    decision_density: float | None
    gap_count: int | None
    action_item_completion_rate: float | None
    participation_balance: float | None
    missing_sources: list[str]


class HeatmapCell(BaseModel):
    """One role pair, averaged across the team's scored meetings."""

    role_a: str
    role_b: str
    score: float
    meeting_count: int


class ReportRead(BaseModel):
    """One generated weekly report."""

    model_config = ConfigDict(from_attributes=True)

    team_id: str
    period_start: date
    period_end: date
    body_markdown: str
    metrics_json: dict
    source_meeting_ids: list[str]


class DashboardScoreEntry(BaseModel):
    """One meeting's grade in the dashboard's recent-scores strip."""

    meeting_id: str
    grade: str
    value: float
    created_at: datetime


class DashboardRead(BaseModel):
    """The team dashboard rollup. Empty until meetings have been scored.

    ``meeting_count`` and ``average_score`` are all-time; ``average_grade`` is
    ``_grade_for(average_score)`` so the big A-F letter on S26 and the number
    next to it describe the same population (the client must not derive a
    grade itself — that duplicates the weekly report's thresholds).
    ``recent_scores`` is scoped to the trailing eight weeks (not a meeting
    count) so the frontend's weekly bars never silently span a longer window
    or average a partially-covered week. ``alignment`` and ``predictions`` are
    not here — they have their own endpoints and are not produced yet.
    """

    team_id: str
    meeting_count: int
    average_score: float | None
    average_grade: str | None
    action_item_completion_rate: float | None
    recent_scores: list[DashboardScoreEntry]
    gap_distribution: dict[str, int]


class SpeakingRatioRead(BaseModel):
    """One participant's share of one meeting's measured speech time.

    ``ratio`` is over the speech attributed to consenting participants, and
    ``participant_count`` counts that same set — so ``ratio`` and the even share
    (``1 / participant_count``) are on the same population. Served only to the
    person it describes and never persisted — ``stored`` is always ``False`` and
    says so to the client. See docs/architecture/privacy.md section 3.

    ``ratio`` is ``None`` (and ``reason`` says why) in two cases:

    - ``small_meeting`` — fewer than three participants consented. With two,
      ``1 - ratio`` fixes the other person's share exactly, so the number is
      withheld rather than returned.
    - ``not_measured`` — the requester was in the meeting but did not consent to
      speaker attribution, so their speech is not in the measured set. This is
      distinct from a consenting participant who was simply silent, who gets
      ``0.0``.
    """

    meeting_id: str
    ratio: float | None
    participant_count: int
    reason: Literal["small_meeting", "not_measured"] | None = None
    stored: bool = False
