"""Labeled history for the misalignment predictor: training and calibration input.

**The label.** A meeting is positive when a decision it settled is reversed in
a later meeting within ``MISALIGNMENT_HORIZON_DAYS``: some later meeting's
``ContextLinks.decision_lineage`` carries a ``reversed`` change whose
``previous_meeting_id`` is this meeting. D already produces that; nobody labels
anything by hand. A meeting is labeled only once the horizon has fully passed
— before that, "no reversal yet" is not "no reversal".

A meeting whose horizon contains a later meeting with no measured lineage is
not labeled at all: D publishes without B's decisions when B times out, and a
reversal in such a meeting would be invisible, so "negative" would assert more
than the payload can show. A meeting already seen to be reversed stays
positive.

Known blind spot: a decision modified in one later meeting and reversed in the
next names the *modifying* meeting as ``previous_meeting_id``, so the original
meeting stays negative. Counting only one hop keeps the label to what D
asserted directly.

**Where it comes from.** Everything is read back from E's own tables:
``intel_completion``'s staged B/C/D payloads, ``intel_scores``,
``intel_alignment`` and ``intel_predictions``, plus ``meetings`` (read-only)
for when each meeting happened. Features are rebuilt with the same
``prediction.meeting_features`` the live path uses, so training and serving
cannot drift apart. Nothing here is written anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session

from autune_contracts import ChangeType, ContextLinks, ExtractionResult, GapReport
from autune_core import Meeting, get_logger

from .models import IntelAlignment, IntelCompletion, IntelPrediction, IntelScore
from .prediction import (
    EXTRACTION_SOURCE,
    MISALIGNMENT_HORIZON_DAYS,
    MISALIGNMENT_KIND,
    MeetingFeatures,
    meeting_features,
)

log = get_logger(__name__)

TRAINING_WINDOW = timedelta(weeks=12)
"""How far back labeled history is read — #27: train on a trailing window, not
a team's whole history, so fitting cost stays flat as teams age. The upper end
of #27's 8-12 week range, because until real traffic exists the binding
constraint is having enough positives at all."""


@dataclass(frozen=True)
class MeetingPoint:
    meeting_id: str
    team_id: str
    at: datetime


@dataclass(frozen=True)
class Reversal:
    """A later meeting reversed a decision the earlier one settled."""

    earlier_meeting_id: str
    at: datetime


@dataclass(frozen=True)
class LabeledExample:
    meeting_id: str
    team_id: str
    at: datetime
    features: MeetingFeatures
    reversed_within_horizon: bool
    stored_predictions: dict[str, float]
    """``model_version -> probability`` from ``intel_predictions``, for
    calibrating what was actually shown rather than a refit."""


def reversal_labels(
    meetings: Iterable[MeetingPoint],
    reversals: Iterable[Reversal],
    *,
    unmeasured: Iterable[MeetingPoint] = (),
    now: datetime,
    horizon: timedelta = timedelta(days=MISALIGNMENT_HORIZON_DAYS),
) -> dict[str, bool]:
    """``meeting_id -> label`` for every meeting whose label is known.

    A meeting is left out when its horizon has not fully passed, and when a
    meeting of the same team inside its horizon is in ``unmeasured`` — a
    reversal there would have been invisible, so "negative" would be an
    assertion about a payload that could not have carried the evidence. A
    meeting already shown to be positive stays positive: a blind spot elsewhere
    cannot unmake a reversal that was seen.
    """
    reversal_times: dict[str, list[datetime]] = {}
    for r in reversals:
        reversal_times.setdefault(r.earlier_meeting_id, []).append(r.at)
    blind_spots: dict[str, list[datetime]] = {}
    for u in unmeasured:
        blind_spots.setdefault(u.team_id, []).append(u.at)
    labels: dict[str, bool] = {}
    for m in meetings:
        if m.at + horizon > now:
            continue
        deadline = m.at + horizon
        positive = any(m.at < at <= deadline for at in reversal_times.get(m.meeting_id, []))
        if not positive and any(m.at < at <= deadline for at in blind_spots.get(m.team_id, ())):
            continue
        labels[m.meeting_id] = positive
    return labels


def _parse[T: (ExtractionResult, GapReport, ContextLinks)](
    model: type[T], payload: dict | None, meeting_id: str
) -> T | None:
    if payload is None:
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        # A payload staged under an older contract version. Skip the source
        # rather than the meeting; log the id only — payloads hold transcript
        # derived text.
        log.warning("intelligence_history_payload_unreadable", meeting_id=meeting_id)
        return None


def labeled_examples(
    session: Session,
    *,
    now: datetime,
    window: timedelta = TRAINING_WINDOW,
    horizon: timedelta = timedelta(days=MISALIGNMENT_HORIZON_DAYS),
) -> list[LabeledExample]:
    """Every aggregated meeting in ``[now - window - horizon, now - horizon]``, labeled.

    Reversals are read from meetings up to ``now`` so the newest labeled
    meeting still sees its whole horizon.
    """
    since = now - window - horizon
    at_column = sa.func.coalesce(Meeting.started_at, IntelCompletion.first_seen_at)
    rows = session.execute(
        sa.select(IntelCompletion, Meeting.team_id, at_column, IntelScore.value)
        .join(Meeting, Meeting.id == IntelCompletion.meeting_id)
        .join(IntelScore, IntelScore.meeting_id == IntelCompletion.meeting_id)
        .where(IntelCompletion.aggregated_at.is_not(None), at_column >= since)
    ).all()

    points: list[MeetingPoint] = []
    reversals: list[Reversal] = []
    unmeasured: list[MeetingPoint] = []
    parsed: dict[str, tuple[ExtractionResult | None, GapReport | None, ContextLinks | None]] = {}
    for completion, team_id, at, _ in rows:
        mid = completion.meeting_id
        extraction = _parse(ExtractionResult, completion.extraction_payload, mid)
        gap = _parse(GapReport, completion.gap_payload, mid)
        context = _parse(ContextLinks, completion.context_payload, mid)
        parsed[mid] = (extraction, gap, context)
        point = MeetingPoint(meeting_id=mid, team_id=team_id, at=at)
        points.append(point)
        if context is None or EXTRACTION_SOURCE in context.missing_sources:
            # No lineage was measured here, so this meeting cannot testify that
            # an earlier one was never reversed.
            unmeasured.append(point)
        else:
            reversals.extend(
                Reversal(earlier_meeting_id=c.previous_meeting_id, at=at)
                for c in context.decision_lineage
                if c.change_type == ChangeType.REVERSED and c.previous_meeting_id
            )

    labels = reversal_labels(points, reversals, unmeasured=unmeasured, now=now, horizon=horizon)
    blocked = sum(1 for p in points if p.at + horizon <= now and p.meeting_id not in labels)
    if blocked:
        # Otherwise "0 labeled meetings" reads as "history is too young" when it
        # is really B not reaching D.
        log.info("intelligence_history_labels_blocked_by_blind_spot", meetings=blocked)
    if not labels:
        return []

    alignment: dict[str, list[float]] = {}
    for mid, score in session.execute(
        sa.select(IntelAlignment.meeting_id, IntelAlignment.score).where(
            IntelAlignment.meeting_id.in_(labels)
        )
    ):
        alignment.setdefault(mid, []).append(score)
    stored: dict[str, dict[str, float]] = {}
    for mid, version, probability in session.execute(
        sa.select(
            IntelPrediction.meeting_id, IntelPrediction.model_version, IntelPrediction.probability
        ).where(
            IntelPrediction.meeting_id.in_(labels),
            IntelPrediction.kind == MISALIGNMENT_KIND,
            IntelPrediction.horizon_days == horizon.days,
        )
    ):
        stored.setdefault(mid, {})[version or "unknown"] = probability

    examples: list[LabeledExample] = []
    for completion, team_id, at, quality_value in rows:
        mid = completion.meeting_id
        if mid not in labels:
            continue
        extraction, gap, context = parsed[mid]
        missing = sum(1 for p in (extraction, gap, context) if p is None)
        examples.append(
            LabeledExample(
                meeting_id=mid,
                team_id=team_id,
                at=at,
                features=meeting_features(
                    quality_value=quality_value,
                    extraction=extraction,
                    gap=gap,
                    context=context,
                    alignment_scores=alignment.get(mid, []),
                    missing_source_count=missing,
                ),
                reversed_within_horizon=labels[mid],
                stored_predictions=stored.get(mid, {}),
            )
        )
    examples.sort(key=lambda e: (e.at, e.meeting_id))
    return examples
