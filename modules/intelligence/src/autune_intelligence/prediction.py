"""Misalignment-prediction features and the display gate (pipeline step 5).

Pure functions over contract values and plain numbers — no database, no model.
The service layer collects one meeting's staged B/C/D payloads and its own
step 2-4 results, turns them into ``MeetingFeatures`` here, and hands those to
whichever ``MisalignmentPredictor`` ``pipeline.registry`` selects.

**What is predicted.** ``misalignment_risk`` over ``MISALIGNMENT_HORIZON_DAYS``:
the probability that a decision this meeting settled is reversed within that
horizon. Reversal is the one misalignment signal an upstream module already
observes across meetings — D records it as ``DecisionChange.change_type ==
"reversed"`` with the earlier meeting on ``previous_meeting_id`` — so it can
serve as the label once history accumulates, without anyone hand-labeling
meetings.

**Every feature is meeting- or role-level.** ``key_stakeholders_absent`` is
read only as "did any change happen with someone absent", never as who; no
feature is a person, and nothing here is persisted. See
docs/architecture/privacy.md section 3.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from autune_contracts import (
    ActionStatus,
    ChangeType,
    ContextLinks,
    ExtractionResult,
    GapReport,
    GapSeverity,
)

from .alignment import PairAgreement

MISALIGNMENT_KIND: Final = "misalignment_risk"
MISALIGNMENT_HORIZON_DAYS: Final = 14
"""The horizon in docs/architecture/contracts.md's ``IntelligenceSnapshot``
example. Two weeks is also roughly one sprint — long enough for a reversal to
surface in the next planning meeting, short enough to act on."""

MIN_HISTORY: Final = timedelta(weeks=4)
MIN_MEETINGS: Final = 3
"""Show a prediction only after four weeks of history **and** three meetings in
it — decided on #27. Four weeks is half the dashboard's eight-week trend window;
the meeting floor stops a monthly team from getting a prediction off one
meeting just because the calendar moved. Internal constants: tune freely."""


@dataclass(frozen=True)
class MeetingFeatures:
    """One meeting, reduced to the numbers a misalignment predictor reads.

    ``None`` means "not measured" (the source was missing, or had nothing to
    measure) — distinct from zero. A predictor must treat it as missing rather
    than as the best or worst value, the same rule ``service._quality_score``
    follows for its components.
    """

    quality_value: float
    decision_count: int | None
    high_gap_count: int | None
    gap_count: int | None
    alignment_min: float | None
    """The weakest role pair's agreement this meeting. The minimum, not the
    mean: one pair pulling apart is the misalignment, and averaging it with
    pairs that agree would hide it."""
    changed_decision_share: float | None
    """Of the decisions D tracked, the share that modified or reversed an
    earlier version — a team already revising itself."""
    reversed_count: int | None
    change_with_stakeholder_absent: bool | None
    ambiguous_agreement_count: int | None
    unconfirmed_action_share: float | None
    missing_source_count: int


def meeting_features(
    *,
    quality_value: float,
    extraction: ExtractionResult | None,
    gap: GapReport | None,
    context: ContextLinks | None,
    alignment: Sequence[PairAgreement],
    missing_source_count: int,
) -> MeetingFeatures:
    lineage = context.decision_lineage if context is not None else []
    tracked = [c for c in lineage if c.change_type != ChangeType.NEW]
    moved = [c for c in tracked if c.change_type in (ChangeType.MODIFIED, ChangeType.REVERSED)]
    actions = extraction.action_items if extraction is not None else []

    return MeetingFeatures(
        quality_value=quality_value,
        decision_count=len(extraction.decisions) if extraction is not None else None,
        high_gap_count=(
            sum(1 for g in gap.gaps if g.severity == GapSeverity.HIGH) if gap is not None else None
        ),
        gap_count=len(gap.gaps) if gap is not None else None,
        alignment_min=min((p.score for p in alignment), default=None),
        changed_decision_share=(len(moved) / len(tracked)) if tracked else None,
        reversed_count=(
            sum(1 for c in lineage if c.change_type == ChangeType.REVERSED)
            if context is not None
            else None
        ),
        change_with_stakeholder_absent=(
            any(c.key_stakeholders_absent for c in moved) if context is not None else None
        ),
        ambiguous_agreement_count=(
            len(extraction.ambiguous_agreements) if extraction is not None else None
        ),
        unconfirmed_action_share=(
            sum(1 for a in actions if a.status == ActionStatus.NEEDS_CONFIRMATION) / len(actions)
            if actions
            else None
        ),
        missing_source_count=missing_source_count,
    )


def prediction_visible(
    first_meeting_at: datetime | None, meeting_count: int, *, now: datetime
) -> bool:
    """#27's gate: four weeks of history and at least three meetings in it.

    Predictions are computed and stored regardless — calibration needs the
    early ones too — and this gates only what a person is shown.
    """
    if first_meeting_at is None:
        return False
    return now - first_meeting_at >= MIN_HISTORY and meeting_count >= MIN_MEETINGS
