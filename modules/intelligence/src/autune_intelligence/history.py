"""Labeled history for the misalignment predictor: training and calibration input.

**The label.** A meeting is positive when a decision it settled is reversed in
a later meeting within ``MISALIGNMENT_HORIZON_DAYS``: some later meeting's
``ContextLinks.decision_lineage`` carries a ``reversed`` change whose
``previous_meeting_id`` is this meeting. D already produces that; nobody labels
anything by hand. A meeting is labeled only once the horizon has fully passed
— before that, "no reversal yet" is not "no reversal".

A meeting whose horizon contains a later meeting that could not have shown a
reversal is not labeled at all, because "negative" would assert more than
anything looked at. Two things make a later meeting unable to show one, and they
are counted apart in the blind-spot log because they are fixed in different
places:

- Its lineage was measured and came back without B's decisions — D publishes
  without them when B times out.
- E never aggregated it, so nothing was measured at all: the upload is still in
  flight, the payloads are staged, or module A failed. That last one never
  resolves on its own.

A meeting already seen to be reversed stays positive; a blind spot cannot unmake
a reversal that was measured.

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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy import Row
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


def _unvisited_meetings(
    session: Session,
    rows: Sequence[Row[Any]],
    *,
    since: datetime,
    now: datetime,
    horizon: timedelta,
) -> list[MeetingPoint]:
    """Meetings of the same teams that the labeling query above never saw.

    A reversal is only visible where E aggregated the meeting that carried it,
    so a meeting missing from that query is a blind spot in the strongest sense:
    not "lineage was measured and came back empty" but "nothing was measured".
    The meeting before it cannot be called negative on that basis. #445.

    Anything absent counts, rather than only what
    ``aggregated_at IS NOT NULL`` excludes: the query also inner-joins
    ``intel_scores``, so a meeting aggregated without a quality score is just as
    invisible. Defining this as "not in ``rows``" cannot drift from whatever that
    query filters on next.

    Teams are limited to those the query did return — a team with nothing
    aggregated has no label to withhold, so scanning it buys nothing.

    ``created_at`` stands in for a missing ``started_at``, because a meeting with
    no time at all could not be placed inside anyone's horizon and would be
    dropped — putting the bug back for the meetings most likely to be stuck. It
    is **not** the same clock as the ``first_seen_at`` the query above falls back
    to: ``created_at`` is when A created the row, ``first_seen_at`` is when E
    first saw it, and transcription and analysis sit in between. There is no
    choice here — an unvisited meeting has no ``intel_completion`` row, which is
    what makes it unvisited — and it is arguably the better clock, since a
    meeting that was never processed really did happen nearer ``created_at``.

    The bias runs one way, which is worth knowing: placing such a meeting
    *earlier* means ``reversal_labels``' ``m.at < at <= deadline`` stops
    withholding labels for meetings that fall between the two clocks. The gap is
    one analysis delay wide and only affects meetings with no ``started_at``.

    **A meeting that was never held is not a blind spot (#462).** A meeting
    created ahead of time (a briefing's calendar meeting, a "새 회의" nobody
    recorded) keeps ``status='scheduled'`` if nothing ever arrives, and an empty
    calendar slot cannot contain a reversal. An upload still on its way is
    ``scheduled`` too, so time decides: one still ``scheduled`` a horizon after
    its time is taken as never held. A ``failed`` or stuck (``analyzing``)
    meeting did happen, and still withholds.
    """
    teams = {team_id for _, team_id, _, _ in rows}
    if not teams:
        return []
    visited = {completion.meeting_id for completion, _, _, _ in rows}
    at_column = sa.func.coalesce(Meeting.started_at, Meeting.created_at)
    never_held = sa.and_(Meeting.status == "scheduled", at_column < now - horizon)
    return [
        MeetingPoint(meeting_id=mid, team_id=team_id, at=at)
        for mid, team_id, at in session.execute(
            sa.select(Meeting.id, Meeting.team_id, at_column).where(
                Meeting.team_id.in_(teams),
                at_column >= since,
                at_column <= now,
                sa.not_(never_held),
            )
        )
        if mid not in visited
    ]


def _blocked_by(
    withheld: Sequence[MeetingPoint], blind_spots: Sequence[MeetingPoint], horizon: timedelta
) -> int:
    """How many of ``withheld`` have one of these blind spots inside their horizon.

    Mirrors ``reversal_labels``' own test so the attribution cannot disagree with
    the decision it explains.
    """
    by_team: dict[str, list[datetime]] = {}
    for b in blind_spots:
        by_team.setdefault(b.team_id, []).append(b.at)
    return sum(
        1 for m in withheld if any(m.at < at <= m.at + horizon for at in by_team.get(m.team_id, ()))
    )


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

    unmeasured_lineage = list(unmeasured)
    unvisited = _unvisited_meetings(session, rows, since=since, now=now, horizon=horizon)
    unmeasured.extend(unvisited)

    labels = reversal_labels(points, reversals, unmeasured=unmeasured, now=now, horizon=horizon)
    withheld = [p for p in points if p.at + horizon <= now and p.meeting_id not in labels]
    if withheld:
        # Otherwise "0 labeled meetings" reads as "history is too young". The two
        # causes are counted apart because they are fixed in different places: an
        # unmeasured lineage is B not reaching D, an unaggregated meeting is a
        # pipeline that stalled or failed before E ever saw it.
        #
        # All three numbers count *withheld meetings*, not blind spots. A blind
        # spot can withhold several labels or none at all, so counting blind
        # spots would send someone after a stalled meeting that is doing no harm.
        # The two causes can overlap on one meeting, so they need not sum to
        # ``meetings``.
        log.info(
            "intelligence_history_labels_blocked_by_blind_spot",
            meetings=len(withheld),
            blocked_by_unmeasured_lineage=_blocked_by(withheld, unmeasured_lineage, horizon),
            blocked_by_unaggregated=_blocked_by(withheld, unvisited, horizon),
        )
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
