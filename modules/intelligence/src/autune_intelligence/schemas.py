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


class PredictionRead(BaseModel):
    """One stored prediction for a meeting."""

    model_config = ConfigDict(from_attributes=True)

    meeting_id: str
    kind: str
    horizon_days: int
    probability: float
    model_version: str | None
    updated_at: datetime


class PredictionsRead(BaseModel):
    """The team's latest prediction, or why there is none to show.

    ``reason`` is ``"insufficient_history"`` until the team has four weeks and
    three scored meetings (#27), and ``"no_prediction"`` if the gate is clear
    but nothing has been predicted yet. ``None`` when ``prediction`` is set.
    """

    team_id: str
    prediction: PredictionRead | None
    reason: Literal["insufficient_history", "no_prediction"] | None


class ReportRead(BaseModel):
    """One generated weekly report."""

    model_config = ConfigDict(from_attributes=True)

    team_id: str
    period_start: date
    period_end: date
    body_markdown: str
    metrics_json: dict
    source_meeting_ids: list[str]


class MeetingReportRead(BaseModel):
    """One meeting's report as the dashboard card shows it (10/2).

    ``title`` is the stored header line, ``body`` the rest -- the subagent's
    text and E's footer, as a person edited it if they did.
    """

    meeting_id: str
    title: str
    body: str
    footer: str
    """E's last line: "자동 생성된 리포트입니다 · M/D HH:MM 기준." (#604), or after an
    edit, who edited it."""
    status: Literal["draft", "posted"]
    posted_at: datetime | None
    pending_review: bool
    edited_by_name: str | None
    edited_at: datetime | None
    updated_at: datetime
    """Send back as ``base_updated_at`` so a stale edit is refused, not saved over."""
    in_slack: bool
    """The post reached Slack and its message is known, so a correction can go under it."""
    correction_body: str | None = None
    """The latest correction to a posted report, as its author wrote it."""
    corrected_by_name: str | None = None
    corrected_at: datetime | None = None
    correction_status: Literal["pending", "sending", "sent", "failed"] | None = None
    """"pending" waits for approval (L2, #674); "sending" was approved and is being
    posted; "failed" was approved but not posted within ``CORRECTION_SEND_WINDOW``."""


class MeetingReportEdit(BaseModel):
    """A team member's edit of a draft's body; E keeps its own header line."""

    body: str
    base_updated_at: datetime | None = None
    """``updated_at`` as the editor saw it; a newer save makes this edit a 409."""


class MeetingReportCorrection(BaseModel):
    """A correction to a posted report, written on the dashboard card."""

    body: str


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
    not here — they have their own endpoints (``/heatmap``, ``/predictions``).

    ``action_item_completion_rate`` is done over confirmed across the team's
    meetings held in the last four weeks (``ACTION_COMPLETION_WINDOW``), from
    B's latest ``TeamActionProgress``, and ``overdue_action_items``
    the overdue total from it (#605). Both are ``None`` with
    ``action_progress_as_of`` when that snapshot is missing or stale -- unknown,
    not zero; a fresh one with nothing confirmed has no rate and 0 overdue.
    ``action_item_confirmation_rate`` is what the quality score uses: the share
    of each meeting's items that got confirmed, averaged over scored meetings.
    """

    team_id: str
    meeting_count: int
    average_score: float | None
    average_grade: str | None
    action_item_completion_rate: float | None
    overdue_action_items: int | None
    action_progress_as_of: datetime | None
    action_item_confirmation_rate: float | None
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
