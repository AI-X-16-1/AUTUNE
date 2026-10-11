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
    DecisionChange,
    ExtractionResult,
    GapReport,
    GapSeverity,
)

EXTRACTION_SOURCE: Final = "extraction"
"""B's name in ``ContextLinks.missing_sources``. When it is there, D published
without B's decisions, so the empty ``decision_lineage`` means "not measured"
rather than "nothing was reversed"."""

MISALIGNMENT_KIND: Final = "misalignment_risk"
MISALIGNMENT_HORIZON_DAYS: Final = 14
"""The horizon in docs/architecture/contracts.md's ``IntelligenceSnapshot``
example. Two weeks is also roughly one sprint — long enough for a reversal to
surface in the next planning meeting, short enough to act on."""

MIN_HISTORY: Final = timedelta(0)
MIN_MEETINGS: Final = 3
"""Show a prediction once the team has three scored meetings.

#27 decided four weeks of history **and** three meetings: four weeks is half the
dashboard's eight-week trend window, and the meeting floor stops a monthly team
from getting a prediction off one meeting. **The four weeks are lifted until the
final presentation** (2026-10-08, on #27): Autune is not in service yet, and a
team that started this month could not show a prediction before it. Restore
``timedelta(weeks=4)`` after the presentation; the meeting floor stays either way.
Internal constants: tune freely."""


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
    """Whether any tracked decision moved while someone was absent — never who.

    ``False`` is weaker than it reads. D can only name an absentee once every
    participant is resolved to a user, so a meeting with any unconfirmed speaker
    yields an empty ``key_stakeholders_absent`` and lands here as ``False``. The
    contract has no field separating "everyone attended" from "attendance
    unknown", so until participant resolution lands this feature is mostly the
    latter. Weight it accordingly.
    """
    ambiguous_agreement_count: int | None
    unconfirmed_action_share: float | None
    missing_source_count: int


def _measured_lineage(context: ContextLinks | None) -> list[DecisionChange] | None:
    """D's decision lineage, or ``None`` when it was never measured.

    D publishes topic links without waiting for B and marks the gap with
    ``"extraction"`` in ``missing_sources``. The empty ``decision_lineage`` that
    comes with it means "B's decisions never arrived", not "nothing was
    reversed" — reading it as zero would feed a false negative into the
    heuristic weights and into every predictor trained on these rows. D
    republishes with ``force`` once B reports late (#310), so a later
    re-aggregation measures the meeting properly.
    """
    if context is None or EXTRACTION_SOURCE in context.missing_sources:
        return None
    return context.decision_lineage


def meeting_features(
    *,
    quality_value: float,
    extraction: ExtractionResult | None,
    gap: GapReport | None,
    context: ContextLinks | None,
    alignment_scores: Sequence[float],
    missing_source_count: int,
) -> MeetingFeatures:
    lineage = _measured_lineage(context)
    tracked = [c for c in (lineage or []) if c.change_type != ChangeType.NEW]
    moved = [c for c in tracked if c.change_type in (ChangeType.MODIFIED, ChangeType.REVERSED)]
    actions = extraction.action_items if extraction is not None else []
    # A meeting C could not read has no gaps to count (#248): unmeasured, not zero.
    gaps = gap.gaps if gap is not None and gap.measured is not False else None

    return MeetingFeatures(
        quality_value=quality_value,
        decision_count=len(extraction.decisions) if extraction is not None else None,
        high_gap_count=(
            sum(1 for g in gaps if g.severity == GapSeverity.HIGH) if gaps is not None else None
        ),
        gap_count=len(gaps) if gaps is not None else None,
        alignment_min=min(alignment_scores, default=None),
        changed_decision_share=(len(moved) / len(tracked)) if tracked else None,
        reversed_count=(
            sum(1 for c in lineage if c.change_type == ChangeType.REVERSED)
            if lineage is not None
            else None
        ),
        change_with_stakeholder_absent=(
            any(c.key_stakeholders_absent for c in moved) if lineage is not None else None
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
    """#27's gate: ``MIN_HISTORY`` of history and at least three meetings in it.

    Predictions are computed and stored regardless — calibration needs the
    early ones too — and this gates only what a person is shown.
    """
    if first_meeting_at is None:
        return False
    return now - first_meeting_at >= MIN_HISTORY and meeting_count >= MIN_MEETINGS
