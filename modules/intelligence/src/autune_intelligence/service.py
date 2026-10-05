"""Business logic for module E: Meeting Intelligence.

Owner: 이승환. See docs/modules/intelligence.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``intel_*`` tables.
Never imports another module.

This file holds the completion-tracking logic. E aggregates B, C and D; any of
them can fail, so aggregation runs when all three have reported or when a
timeout elapses. The Celery glue that enqueues the aggregate task lives in
``tasks.py`` — this module never imports a task.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Final, Literal, NamedTuple

import sqlalchemy as sa
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from autune_contracts import (
    ACTION_PROGRESS_STALE_AFTER,
    ActionItem,
    ActionStatus,
    ContextLinks,
    ExtractionResult,
    GapReport,
    GapSeverity,
    IntelligenceSnapshot,
    Participation,
    Prediction,
    QualityScore,
    TeamActionProgress,
)
from autune_contracts.intelligence import Grade
from autune_core import (
    Meeting,
    Participant,
    Team,
    TeamIntegration,
    TeamMember,
    User,
    Utterance,
    get_logger,
    new_id,
    session_scope,
)
from autune_core.deletion import on_speech_deleted
from autune_core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    PrivacyViolationError,
    ValidationError,
)
from autune_integrations import (
    PermanentIntegrationError,
    SlackApi,
    SlackClient,
    assert_masked,
    assert_personal_delivery,
    check_outbound,
    find_unmasked,
)

from . import forget
from .alignment import meeting_alignment
from .config import get_settings
from .feedback import build_speaking_ratio_dm
from .models import (
    IntelActionProgress,
    IntelActionProgressMeeting,
    IntelAlignment,
    IntelCompletion,
    IntelGapPattern,
    IntelMeetingReport,
    IntelPrediction,
    IntelReport,
    IntelScore,
    IntelTeamSettings,
)
from .pipeline import get_gap_classifier, get_misalignment_predictor
from .pipeline.base import Classification
from .prediction import (
    MISALIGNMENT_HORIZON_DAYS,
    MISALIGNMENT_KIND,
    meeting_features,
    prediction_visible,
)
from .schemas import (
    DashboardRead,
    DashboardScoreEntry,
    HeatmapCell,
    MeetingReportRead,
    PredictionRead,
    PredictionsRead,
    SpeakingRatioRead,
)
from .speaking import SpeakingShare, SpeechSegment, speaker_count_for_gate, speaking_shares

log = get_logger(__name__)

SOURCES: tuple[str, ...] = ("extraction", "gap", "context")
"""The three upstream modules E waits on. Each maps to an ``<source>_at`` column
on ``intel_completion``."""

_COLUMN = {source: f"{source}_at" for source in SOURCES}


def record_completion(session: Session, meeting_id: str, source: str, payload: dict) -> bool:
    """Record that ``source`` has reported for ``meeting_id`` and stash its payload.

    Upserts ``intel_completion``: ``<source>_at`` is set on the first arrival and
    kept on a re-delivery (the countdown anchor must not move); ``<source>_payload``
    takes the latest payload every call. ``first_seen_at`` is stamped once.

    Returns ``True`` when this call created the row — the first of the three
    sources for the meeting, whose caller schedules the timeout countdown.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")

    at_column = _COLUMN[source]
    payload_column = f"{source}_payload"
    now = datetime.now(UTC)
    stmt = (
        pg_insert(IntelCompletion)
        .values(
            meeting_id=meeting_id,
            first_seen_at=now,
            **{at_column: now, payload_column: payload},
        )
        .on_conflict_do_update(
            index_elements=["meeting_id"],
            set_={
                at_column: func.coalesce(getattr(IntelCompletion, at_column), now),
                payload_column: payload,
            },
        )
        .returning(text("(xmax = 0) AS inserted"))
    )
    inserted = bool(session.execute(stmt).scalar_one())
    session.expire_all()
    return inserted


def missing_sources(row: IntelCompletion) -> list[str]:
    """The sources with no arrival timestamp on this completion row, in order."""
    return [source for source in SOURCES if getattr(row, _COLUMN[source]) is None]


def ready_to_aggregate(row: IntelCompletion, *, now: datetime | None = None) -> bool:
    """True when all three sources are in, or the timeout since the first has elapsed."""
    if not missing_sources(row):
        return True
    now = now or datetime.now(UTC)
    timeout = timedelta(seconds=get_settings().aggregate_timeout_seconds)
    return now - row.first_seen_at >= timeout


DECISION_CADENCE_MINUTES: Final = 10.0
"""One decision per this many minutes scores decision_density 1.0.

What counts as "one decision" is B's grouping, not a fixed unit: several
utterances become one ``Decision`` only if they fall inside
``autune_extraction.decisions.DEFAULT_MAX_GAP``. That constant is unvalidated
(#53, ADR 0006), and because the density is capped at 1.0, over-splitting there
reads here as a *better* meeting. Retune this against the same evaluation set,
not independently.
"""
HIGH_GAP_CEILING: Final = 5
"""This many HIGH-severity gaps drives gap_burden to 0.0."""
WEIGHTS: Final = {
    "decision_density": 0.3,
    "gap_burden": 0.3,
    "action_item_completion_rate": 0.2,
    "participation_balance": 0.2,
}
"""First heuristic, fixed by hand — not learned, not validated (#26).

Learning these from user feedback needs a feedback signal that does not exist
yet (no "was this score fair?" UI anywhere), the same gap that keeps C's gap
threshold hand-tuned off dismissals rather than fit. Revisit as a P2 once that
signal exists; until then this is the same kind of P1 heuristic
``GRADE_CUTOFFS`` already is, just without the label.
"""
GRADE_CUTOFFS: Final = ((0.9, "A"), (0.8, "B"), (0.7, "C"), (0.6, "D"), (0.5, "E"))
"""Descending; value below the last cutoff is F. First heuristic — P2 tunes these."""


def _decision_density(decision_count: int, duration_minutes: float) -> float | None:
    """None when B reported no decisions — "not measured", not "scored zero".

    Until the extraction classifier (#10) ships, ``ExtractionResult.decisions``
    is always empty, so a ``0.0`` here would peg 0.3 of every meeting's score to
    zero. Returning ``None`` lets ``_quality_score`` renormalise the weight away.
    The cost: a meeting that genuinely ended with no decisions now scores the
    same as one we could not measure.
    """
    if decision_count == 0:
        return None
    expected = max(1.0, duration_minutes / DECISION_CADENCE_MINUTES)
    return min(1.0, decision_count / expected)


def _gap_burden(high_gap_count: int) -> float:
    return 1.0 - min(1.0, high_gap_count / HIGH_GAP_CEILING)


def _action_item_completion_rate(action_items: list[ActionItem]) -> float | None:
    if not action_items:
        return None
    done = sum(1 for a in action_items if a.status != ActionStatus.NEEDS_CONFIRMATION)
    return done / len(action_items)


def _participation_balance(participation: list[Participation]) -> float | None:
    if not participation:
        return None
    ratios = [len(p.spoke) / max(1, len(p.spoke) + len(p.silent)) for p in participation]
    return sum(ratios) / len(ratios)


def _grade_for(value: float) -> Grade:
    for cutoff, grade in GRADE_CUTOFFS:
        if value >= cutoff:
            return grade  # type: ignore[return-value]
    return "F"


def _quality_score(components: dict[str, float | None]) -> QualityScore:
    present = {k: v for k, v in components.items() if v is not None}
    if not present:
        value = 0.5
    else:
        total_weight = sum(WEIGHTS[k] for k in present)
        value = sum(v * WEIGHTS[k] for k, v in present.items()) / total_weight
    return QualityScore(grade=_grade_for(value), value=value)


def reopen(session: Session, meeting_id: str) -> None:
    """Clear ``aggregated_at`` so a late source triggers a fresh aggregation."""
    row = session.get(IntelCompletion, meeting_id, with_for_update=True)
    if row is not None:
        row.aggregated_at = None


def aggregate_meeting(session: Session, meeting_id: str) -> IntelligenceSnapshot | None:
    """Turn the staged B/C/D payloads into an IntelligenceSnapshot and persist it.

    Returns ``None`` when there is no completion row or it is already aggregated —
    the countdown task and an all-three trigger both call this. A late source
    calls ``reopen`` first.
    """
    row = session.get(IntelCompletion, meeting_id, with_for_update=True)
    if row is None or row.aggregated_at is not None:
        return None

    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return None
    duration_minutes = (meeting.duration_seconds or 0.0) / 60.0

    extraction = (
        ExtractionResult.model_validate(row.extraction_payload)
        if row.extraction_payload is not None
        else None
    )
    gap = GapReport.model_validate(row.gap_payload) if row.gap_payload is not None else None
    context = (
        ContextLinks.model_validate(row.context_payload)
        if row.context_payload is not None
        else None
    )
    missing = missing_sources(row)

    high_gap_count = (
        sum(1 for g in gap.gaps if g.severity == GapSeverity.HIGH) if gap is not None else None
    )
    components: dict[str, float | None] = {
        "decision_density": (
            _decision_density(len(extraction.decisions), duration_minutes)
            if extraction is not None
            else None
        ),
        "gap_burden": _gap_burden(high_gap_count) if high_gap_count is not None else None,
        "action_item_completion_rate": (
            _action_item_completion_rate(extraction.action_items)
            if extraction is not None
            else None
        ),
        "participation_balance": (
            _participation_balance(gap.participation) if gap is not None else None
        ),
    }
    score = _quality_score(components)

    classifications: list[Classification] = []
    classifier_version = ""
    if gap is not None and gap.gaps:
        classifier = get_gap_classifier()
        # Title only, not "{category} {title}": the seed set the local classifier
        # trains on is title-shaped text, and C's category is free text whose
        # vocabulary is not stable across meetings (the whole reason this
        # classifier exists) — prepending it measurably drags classification
        # confidence down on inputs the model never trained on that shape.
        classifications = classifier.classify([g.title for g in gap.gaps])
        classifier_version = classifier.model_version
    pattern_types = [c.pattern_type for c in classifications]
    distribution = dict(Counter(pattern_types))

    # Empty until B's stance producer ships (#10, #168) — every decision's
    # ``stance_by_role`` is ``[]`` until then, so no pair is scored.
    alignment = meeting_alignment(extraction.decisions) if extraction is not None else []

    session.execute(
        pg_insert(IntelScore)
        .values(
            meeting_id=meeting_id,
            team_id=meeting.team_id,
            grade=score.grade,
            value=score.value,
            decision_density=components["decision_density"],
            gap_count=high_gap_count,
            action_item_completion_rate=components["action_item_completion_rate"],
            participation_balance=components["participation_balance"],
            missing_sources=missing,
        )
        .on_conflict_do_update(
            index_elements=["meeting_id"],
            set_={
                "team_id": meeting.team_id,
                "grade": score.grade,
                "value": score.value,
                "decision_density": components["decision_density"],
                "gap_count": high_gap_count,
                "action_item_completion_rate": components["action_item_completion_rate"],
                "participation_balance": components["participation_balance"],
                "missing_sources": missing,
                "updated_at": func.now(),
            },
        )
    )

    session.execute(sa.delete(IntelGapPattern).where(IntelGapPattern.meeting_id == meeting_id))
    if gap is not None:
        ids_by_pattern: dict[str, list[str]] = {}
        confidences_by_pattern: dict[str, list[float]] = {}
        for g, classification in zip(gap.gaps, classifications, strict=True):
            ids_by_pattern.setdefault(classification.pattern_type, []).append(g.id)
            confidences_by_pattern.setdefault(classification.pattern_type, []).append(
                classification.confidence
            )
        avg_confidence_by_pattern = {
            pattern_type: sum(confidences) / len(confidences)
            for pattern_type, confidences in confidences_by_pattern.items()
        }
        for pattern_type, count in distribution.items():
            session.add(
                IntelGapPattern(
                    meeting_id=meeting_id,
                    pattern_type=pattern_type,
                    team_id=meeting.team_id,
                    count=count,
                    source_gap_ids=ids_by_pattern[pattern_type],
                    classifier_version=classifier_version,
                    avg_confidence=avg_confidence_by_pattern[pattern_type],
                )
            )
        if distribution:
            # Counts and a mean confidence per pattern type — no gap content,
            # so this carries no transcript text (privacy.md is not implicated)
            # — the only way to notice from outside a training run that a
            # distribution has quietly collapsed onto "other".
            log.info(
                "intelligence_gap_pattern_distribution",
                meeting_id=meeting_id,
                classifier_version=classifier_version,
                distribution={
                    pattern_type: {
                        "count": count,
                        "avg_confidence": round(avg_confidence_by_pattern[pattern_type], 4),
                    }
                    for pattern_type, count in distribution.items()
                },
            )

    session.execute(sa.delete(IntelAlignment).where(IntelAlignment.meeting_id == meeting_id))
    for pair in alignment:
        session.add(
            IntelAlignment(
                meeting_id=meeting_id,
                role_a=pair.role_a,
                role_b=pair.role_b,
                team_id=meeting.team_id,
                score=pair.score,
            )
        )

    predictor = get_misalignment_predictor()
    features = meeting_features(
        quality_value=score.value,
        extraction=extraction,
        gap=gap,
        context=context,
        alignment_scores=[pair.score for pair in alignment],
        missing_source_count=len(missing),
    )
    (probability,) = predictor.predict([features])
    session.execute(
        pg_insert(IntelPrediction)
        .values(
            meeting_id=meeting_id,
            kind=MISALIGNMENT_KIND,
            horizon_days=MISALIGNMENT_HORIZON_DAYS,
            team_id=meeting.team_id,
            probability=probability,
            model_version=predictor.model_version,
        )
        .on_conflict_do_update(
            index_elements=["meeting_id", "kind", "horizon_days"],
            set_={
                "team_id": meeting.team_id,
                "probability": probability,
                "model_version": predictor.model_version,
                "updated_at": func.now(),
            },
        )
    )
    session.flush()
    # Stored either way — calibration needs the early predictions too — but
    # published only once the team clears #27's history gate.
    predictions = (
        [
            Prediction(
                kind=MISALIGNMENT_KIND,
                horizon_days=MISALIGNMENT_HORIZON_DAYS,
                probability=probability,
            )
        ]
        if _team_prediction_visible(session, meeting.team_id)
        else []
    )

    row.aggregated_at = datetime.now(UTC)
    session.flush()
    session.expire_all()

    return IntelligenceSnapshot(
        meeting_id=meeting_id,
        team_id=meeting.team_id,
        quality_score=score,
        gap_distribution=distribution,
        alignment=[pair.to_contract() for pair in alignment],
        predictions=predictions,
        missing_sources=missing,
    )


# --- Read API -------------------------------------------------------------
#
# Routes in ``router.py`` parse the path parameter and call one of these; the
# query and its shaping live here so they are testable without HTTP. Every
# function reads only ``intel_*`` tables.

_DASHBOARD_TREND_WEEKS: Final = 8
"""The window ``recent_scores`` covers, matching S26's eight-week bar strip.
Date-scoped rather than count-limited, so a busy team's oldest visible week is
never an undercounted partial and a quiet team's bars never silently span more
than eight weeks. Bucketing by week is still the frontend's job."""


def get_score(session: Session, meeting_id: str, *, user_id: str | None = None) -> IntelScore:
    """The meeting's quality score, or a 404 that names no meeting content.

    With ``user_id``, only for a member of the meeting's team: anyone else gets
    the same 404, so whether the meeting exists does not leak. E's agent tools
    call it without one; who may run them is the agent layer's to decide.
    """
    row = session.get(IntelScore, meeting_id)
    if row is None or (
        user_id is not None and not _is_member(session, user_id=user_id, team_id=row.team_id)
    ):
        raise NotFoundError("intelligence score", meeting_id)
    return row


MIN_MEETINGS_PER_HEATMAP_CELL: Final = 3
"""A role pair is shown only once this many meetings scored it.

docs/architecture/privacy.md section 3: a consumer that aggregates stance over
several meetings leaves a cell empty when its sample is too small, rather than
showing a number that identifies the few people behind it. Three matches the
per-role gate on ``RoleStance.identified`` and the meeting floor #27 set for
predictions. The pair is omitted, not returned with a null score, so the
frontend draws it as "no data" the same way it draws a pair never scored.
"""


def get_heatmap(session: Session, team_id: str) -> list[HeatmapCell]:
    """Role-pair alignment for the team, averaged over its scored meetings.

    Pairs scored in fewer than ``MIN_MEETINGS_PER_HEATMAP_CELL`` meetings are
    left out. Every pair is left out until B's stance producer ships (#168).
    """
    rows = session.execute(
        sa.select(
            IntelAlignment.role_a,
            IntelAlignment.role_b,
            func.avg(IntelAlignment.score),
            func.count(),
        )
        .where(IntelAlignment.team_id == team_id)
        .group_by(IntelAlignment.role_a, IntelAlignment.role_b)
        .having(func.count() >= MIN_MEETINGS_PER_HEATMAP_CELL)
        .order_by(IntelAlignment.role_a, IntelAlignment.role_b)
    ).all()
    return [
        HeatmapCell(role_a=role_a, role_b=role_b, score=float(avg), meeting_count=count)
        for role_a, role_b, avg, count in rows
    ]


def _team_prediction_visible(
    session: Session, team_id: str, *, now: datetime | None = None
) -> bool:
    """#27's gate over the team's scored meetings (``intel_scores``)."""
    first_at, count = session.execute(
        sa.select(func.min(IntelScore.created_at), func.count()).where(
            IntelScore.team_id == team_id
        )
    ).one()
    return prediction_visible(first_at, count, now=now or datetime.now(UTC))


_PREDICTION_RECENCY = func.coalesce(Meeting.started_at, IntelPrediction.created_at)
"""Which meeting's prediction is the team's current one.

Not ``IntelPrediction.updated_at``: that is when E last *wrote* the row, and
``aggregate_meeting`` upserts it with a fresh ``updated_at`` every time a late
source reopens a meeting. Ordering by it made a re-aggregated meeting from
months ago the team's "latest" prediction, with a horizon that closed long
before. ``created_at`` is the fallback rather than ``first_seen_at`` because it
is never bumped by the upsert, and a meeting with no ``started_at`` was
aggregated when it arrived."""


def get_predictions(session: Session, team_id: str) -> PredictionsRead:
    """The team's latest misalignment prediction, or why none is shown.

    "Latest" is by when the meeting happened, not when the row was written —
    see ``_PREDICTION_RECENCY``.

    Before #27's gate clears, ``prediction`` is ``None`` and ``reason`` is
    ``"insufficient_history"`` — the stored probabilities exist but are not
    returned, so the gate is enforced here rather than trusted to the client.
    """
    if not _team_prediction_visible(session, team_id):
        return PredictionsRead(team_id=team_id, prediction=None, reason="insufficient_history")
    latest = session.execute(
        sa.select(IntelPrediction)
        .join(Meeting, Meeting.id == IntelPrediction.meeting_id)
        .where(
            IntelPrediction.team_id == team_id,
            IntelPrediction.kind == MISALIGNMENT_KIND,
            IntelPrediction.horizon_days == MISALIGNMENT_HORIZON_DAYS,
        )
        .order_by(_PREDICTION_RECENCY.desc(), IntelPrediction.meeting_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return PredictionsRead(team_id=team_id, prediction=None, reason="no_prediction")
    return PredictionsRead(
        team_id=team_id,
        prediction=PredictionRead.model_validate(latest),
        reason=None,
    )


def list_reports(session: Session, team_id: str) -> list[IntelReport]:
    """The team's generated weekly reports, newest period first."""
    return list(
        session.execute(
            sa.select(IntelReport)
            .where(IntelReport.team_id == team_id)
            .order_by(IntelReport.period_start.desc())
        )
        .scalars()
        .all()
    )


def get_dashboard(session: Session, team_id: str) -> DashboardRead:
    """Team rollup over ``intel_scores`` and ``intel_gap_patterns``.

    A team with nothing scored yet gets an empty rollup, not a 404 — the
    dashboard is a landing surface, not a resource that is missing.
    """
    scores = list(
        session.execute(
            sa.select(IntelScore)
            .where(IntelScore.team_id == team_id)
            .order_by(IntelScore.created_at.desc(), IntelScore.meeting_id.desc())
        )
        .scalars()
        .all()
    )
    values = [s.value for s in scores]
    rates = [
        s.action_item_completion_rate for s in scores if s.action_item_completion_rate is not None
    ]
    average_score = (sum(values) / len(values)) if values else None
    progress = _action_progress_totals(session, team_id, datetime.now(UTC))
    gap_rows = session.execute(
        sa.select(IntelGapPattern.pattern_type, func.sum(IntelGapPattern.count))
        .where(IntelGapPattern.team_id == team_id, IntelGapPattern.classifier_version != "")
        .group_by(IntelGapPattern.pattern_type)
    ).all()
    trend_since = datetime.now(UTC) - timedelta(weeks=_DASHBOARD_TREND_WEEKS)

    return DashboardRead(
        team_id=team_id,
        meeting_count=len(scores),
        average_score=average_score,
        average_grade=_grade_for(average_score) if average_score is not None else None,
        action_item_completion_rate=progress.completion_rate,
        action_completion_meeting_count=progress.completion_meetings,
        overdue_action_items=progress.overdue,
        action_progress_as_of=progress.as_of,
        action_item_confirmation_rate=(sum(rates) / len(rates)) if rates else None,
        recent_scores=[
            DashboardScoreEntry(
                meeting_id=s.meeting_id, grade=s.grade, value=s.value, created_at=s.created_at
            )
            for s in scores
            if s.created_at >= trend_since
        ],
        gap_distribution={pattern: int(total) for pattern, total in gap_rows},
    )


_MAX_GAP_TITLES_PER_PATTERN: Final = 20
"""A team's history can pile up hundreds of gaps behind one pattern; the
hover popup this feeds (`maxWidth: 220`) is not built to scroll that."""


def gap_titles_by_pattern(session: Session, team_id: str) -> dict[str, list[str]]:
    """The **high-severity** gap titles behind each pattern's count in ``get_dashboard``.

    ``IntelGapPattern.source_gap_ids`` names which of C's gaps fed a pattern's
    count, but not their text — C's title never enters an ``intel_*`` table,
    only ``IntelCompletion.gap_payload`` (the raw ``GapReport`` C published,
    kept for aggregation). This joins the two in memory rather than a SQL
    join: neither table has a foreign key to the other by design (E does not
    key off another module's rows), and payload matching only needs id
    equality within one meeting, not a relational join.

    ``source_gap_ids`` carries every severity — it feeds the *count*, and
    filtering it would silently change what ``gap_distribution`` counts. This
    function filters instead, because unlike the count, gap *title text* is
    content C's own rule (`docs/architecture/contracts.md`, module C's
    CLAUDE.md) says only surfaces at `high` severity by default; anything
    below that is a candidate C itself would not show.

    A gap id with no matching title (payload never arrived, or was since
    cleared) is skipped rather than raising — the count in ``gap_distribution``
    still includes it; this is a best-effort explanation of that count, not
    its source of truth. A malformed payload entry missing ``id`` or
    ``title`` is skipped the same way, not a 500. Titles are deduplicated and
    capped per pattern (``_MAX_GAP_TITLES_PER_PATTERN``) — the hover popup
    this feeds is not built to scroll a team's whole history.
    """
    pattern_rows = session.execute(
        sa.select(
            IntelGapPattern.meeting_id, IntelGapPattern.pattern_type, IntelGapPattern.source_gap_ids
        )
        .where(IntelGapPattern.team_id == team_id)
        .order_by(IntelGapPattern.meeting_id, IntelGapPattern.pattern_type)
    ).all()
    if not pattern_rows:
        return {}

    meeting_ids = {row.meeting_id for row in pattern_rows}
    completions = session.execute(
        sa.select(IntelCompletion.meeting_id, IntelCompletion.gap_payload).where(
            IntelCompletion.meeting_id.in_(meeting_ids)
        )
    ).all()
    titles_by_meeting: dict[str, dict[str, str]] = {}
    for meeting_id, gap_payload in completions:
        if not gap_payload:
            continue
        titles_by_meeting[meeting_id] = {
            gap["id"]: gap["title"]
            for gap in gap_payload.get("gaps", [])
            if gap.get("severity") == "high" and gap.get("id") is not None and gap.get("title")
        }

    result: dict[str, list[str]] = {}
    for row in pattern_rows:
        titles = titles_by_meeting.get(row.meeting_id, {})
        bucket = result.setdefault(row.pattern_type, [])
        for gap_id in row.source_gap_ids:
            title = titles.get(gap_id)
            if title is None or title in bucket:
                continue
            if len(bucket) >= _MAX_GAP_TITLES_PER_PATTERN:
                continue
            bucket.append(title)
    return {pattern: titles for pattern, titles in result.items() if titles}


# --- Weekly report (pipeline step 6) ---------------------------------------
#
# A deterministic summary over intel_scores and intel_gap_patterns for one
# team and one period. Delivery (Slack channel, or skipped without one) lives
# in tasks.py; nothing here writes outside intel_reports.


def _report_body_markdown(
    *,
    period_start: date,
    period_end: date,
    meeting_count: int,
    average_value: float | None,
    grade_distribution: dict[str, int],
    gap_distribution: dict[str, int],
    partial_meeting_count: int,
    progress: ActionProgressTotals | None = None,
) -> str:
    """The report's Slack/markdown body — a template, not an LLM.

    ``progress`` is B's action-item counts as the dashboard reads them (#605):
    ``None`` leaves them out (a report for a week that ended before today), and
    totals without ``as_of`` say the counts did not arrive.

    Every value here is already computed in intel_scores/intel_gap_patterns;
    this only arranges them into readable sentences. `../architecture/privacy.md`
    is not implicated (no transcript content passes through this function), but
    an LLM call would still need to go through `autune_integrations`'
    `check_outbound` rather than a module-local client — module D's `LlmClient`
    protocol (`modules/context/src/autune_context/pipeline/base.py`) documents
    why: PR #90 rejected exactly that shortcut. No such client exists yet, so
    this function is the seam where one replaces the template later.
    """
    header = f"*{period_start.isoformat()} ~ {period_end.isoformat()} 주간 리포트*"
    if meeting_count == 0:
        return "\n".join(
            [header, "", "이번 주 분석된 회의가 없습니다.", *_progress_lines(progress)]
        )

    lines = [header, "", f"이번 주 분석된 회의 {meeting_count}건."]
    if partial_meeting_count:
        lines.append(f"이 중 부분 분석 {partial_meeting_count}건.")
    if average_value is not None:
        lines.append(f"평균 품질 점수: {_grade_for(average_value)} ({average_value:.0%})")
    if grade_distribution:
        dist = " · ".join(f"{g} {c}건" for g, c in sorted(grade_distribution.items()))
        lines.append(f"등급 분포: {dist}")
    if gap_distribution:
        top_type, top_count = max(gap_distribution.items(), key=lambda kv: kv[1])
        lines.append(f"가장 잦은 갭 유형: {top_type} ({top_count}건)")
    lines.extend(_progress_lines(progress))
    return "\n".join(lines)


def _progress_lines(progress: ActionProgressTotals | None) -> list[str]:
    """Team totals only, as the dashboard shows them -- never one meeting's."""
    if progress is None:
        return []
    if progress.as_of is None:
        return ["액션 아이템 완료 현황을 받지 못했습니다."]
    counted = progress.as_of.astimezone(_KST)
    if progress.completion_rate is not None:
        lines = [f"액션 아이템 완료율 (최근 4주 회의): {progress.completion_rate:.0%}"]
    elif 0 < (progress.completion_meetings or 0) < ACTION_PROGRESS_MIN_MEETINGS:
        lines = ["확정 항목이 있는 최근 4주 회의가 3건 미만이라 완료율은 싣지 않습니다."]
    else:
        lines = ["최근 4주 회의에서 확정된 액션 아이템이 없습니다."]
    counts = []
    if progress.overdue is not None:
        counts.append(f"기한 지난 항목 {progress.overdue}건")
    if progress.carried_over is not None:
        counts.append(
            f"이월된 항목 {progress.carried_over}건 (이번 주 전 회의에서 아직 끝나지 않은 것)"
        )
    if counts:
        lines.append(" · ".join(counts))
    lines.append(f"액션 아이템 수치는 {counted.month}/{counted.day} {counted:%H:%M} 기준입니다.")
    return lines


def _stated_progress(
    session: Session, team_id: str, period_start: date
) -> ActionProgressTotals | None:
    """The action-item counts the week's stored report stated, or ``None`` when
    it stated none (or there is no report yet)."""
    row = session.get(IntelReport, (team_id, period_start))
    metrics = row.metrics_json if row is not None else {}
    if not metrics.get("action_progress_stated"):
        return None
    as_of = metrics.get("action_progress_as_of")
    return ActionProgressTotals(
        completion_rate=metrics.get("action_item_completion_rate"),
        completion_meetings=metrics.get("action_completion_meeting_count"),
        overdue=metrics.get("overdue_action_items"),
        as_of=datetime.fromisoformat(as_of) if as_of else None,
        carried_over=metrics.get("carried_over_action_items"),
    )


_PROGRESS_REPORTED_WITHIN: Final = timedelta(days=1)
"""B's counts describe today. A report for a week that ended longer ago than
this leaves them out rather than print today's numbers as that week's."""


def generate_weekly_report(
    session: Session, team_id: str, period_start: date, period_end: date
) -> IntelReport:
    """Aggregate this team's scored meetings in ``[period_start, period_end)``
    into one ``intel_reports`` row, upserted by ``(team_id, period_start)``.

    The period is anchored to when E scored a meeting (``IntelScore.created_at``)
    — the same recency signal the dashboard's recent-scores strip already uses.
    E does not track when a meeting itself happened, only when it was analyzed.
    Its dates are Korean dates: a week runs from midnight KST, as the team reads
    it and as B counts "today" (``ACTION_PROGRESS_TODAY_ZONE``).

    Action items come from B's latest counts as the dashboard reads them --
    completion and overdue over the last four weeks' meetings, and what
    meetings held before ``period_start`` left undone -- for a week ending
    within ``_PROGRESS_REPORTED_WITHIN`` of now only (#605); written again
    later, a week keeps the counts it first stated. "Before ``period_start``"
    goes by when a meeting was held, while the week's meetings are those
    *scored* in it, so a meeting held on the eve and scored the next morning
    is in both. The quality score's confirmation rate stays in
    ``metrics_json`` and out of the body.

    Returns a transient ``IntelReport`` carrying the values just written — not
    the tracked row — so the caller (a Celery task, delivering the body to
    Slack) does not need a second query.
    """
    start = datetime.combine(period_start, datetime.min.time(), tzinfo=_KST)
    end = datetime.combine(period_end, datetime.min.time(), tzinfo=_KST)

    scores = list(
        session.execute(
            sa.select(IntelScore).where(
                IntelScore.team_id == team_id,
                IntelScore.created_at >= start,
                IntelScore.created_at < end,
            )
        )
        .scalars()
        .all()
    )
    meeting_ids = [s.meeting_id for s in scores]
    values = [s.value for s in scores]
    rates = [
        s.action_item_completion_rate for s in scores if s.action_item_completion_rate is not None
    ]
    grade_distribution = dict(Counter(s.grade for s in scores))
    partial_meeting_count = sum(1 for s in scores if s.missing_sources)

    gap_distribution: dict[str, int] = {}
    if meeting_ids:
        gap_rows = session.execute(
            sa.select(IntelGapPattern.pattern_type, func.sum(IntelGapPattern.count))
            .where(IntelGapPattern.meeting_id.in_(meeting_ids))
            .group_by(IntelGapPattern.pattern_type)
        ).all()
        gap_distribution = {pattern: int(total) for pattern, total in gap_rows}

    average_value = (sum(values) / len(values)) if values else None
    now = datetime.now(UTC)
    progress = (
        _action_progress_totals(session, team_id, now, carried_before=start)
        if now - end <= _PROGRESS_REPORTED_WITHIN
        # B's counts no longer describe that week: keep what its report said
        # when it was first written, rather than wipe it (#809 review).
        else _stated_progress(session, team_id, period_start)
    )
    shown = progress or ActionProgressTotals()

    body_markdown = _report_body_markdown(
        period_start=period_start,
        period_end=period_end,
        meeting_count=len(scores),
        average_value=average_value,
        grade_distribution=grade_distribution,
        gap_distribution=gap_distribution,
        partial_meeting_count=partial_meeting_count,
        progress=progress,
    )
    metrics_json = {
        "meeting_count": len(scores),
        "average_score": average_value,
        "grade_distribution": grade_distribution,
        "gap_distribution": gap_distribution,
        "action_progress_stated": progress is not None,
        "action_item_completion_rate": shown.completion_rate,
        "action_completion_meeting_count": shown.completion_meetings,
        "overdue_action_items": shown.overdue,
        "carried_over_action_items": shown.carried_over,
        "action_progress_as_of": shown.as_of.isoformat() if shown.as_of else None,
        "action_item_confirmation_rate": (sum(rates) / len(rates)) if rates else None,
        "partial_meeting_count": partial_meeting_count,
    }

    session.execute(
        pg_insert(IntelReport)
        .values(
            team_id=team_id,
            period_start=period_start,
            period_end=period_end,
            body_markdown=body_markdown,
            metrics_json=metrics_json,
            source_meeting_ids=meeting_ids,
        )
        .on_conflict_do_update(
            index_elements=["team_id", "period_start"],
            set_={
                "period_end": period_end,
                "body_markdown": body_markdown,
                "metrics_json": metrics_json,
                "source_meeting_ids": meeting_ids,
                "updated_at": func.now(),
            },
        )
    )
    session.flush()
    return IntelReport(
        team_id=team_id,
        period_start=period_start,
        period_end=period_end,
        body_markdown=body_markdown,
        metrics_json=metrics_json,
        source_meeting_ids=meeting_ids,
    )


# --- When the weekly report goes out (#227) ---------------------------------------
#
# A team picks a weekday and an hour (Korean time); an hourly task writes each
# team's report once that slot has passed and posts it once. Nothing in apps/:
# the task declares its own period (``autune_core.periodic``, #374).

WEEKLY_REPORT_DEFAULT_WEEKDAY: Final = 0
"""Monday, as ``date.weekday`` counts."""
WEEKLY_REPORT_DEFAULT_HOUR: Final = 9
"""09:00 Korean time: ui-spec S27's "Mondays 09:00"."""
WEEKLY_REPORT_CATCH_UP: Final = timedelta(days=1)
"""A slot older than this is not caught up. A worker that was down for a day
does not post last week's report a week late, and a team that moves its day
does not get the slot it just skipped."""
WEEKLY_REPORT_ACTIVE_WITHIN: Final = timedelta(days=91)
"""A team with no meeting created this recently is sent nothing at all -- B's
``ACTION_PROGRESS_WINDOW``, so a report still has counts to state."""


@dataclass(frozen=True)
class WeeklyReportSchedule:
    weekday: int
    hour: int
    send_empty: bool
    updated_by_name: str | None = None
    updated_at: datetime | None = None


def weekly_report_schedule(session: Session, team_id: str) -> WeeklyReportSchedule:
    """The team's setting, or the defaults when it has never changed them."""
    row = session.get(IntelTeamSettings, team_id)
    if row is None:
        return WeeklyReportSchedule(
            weekday=WEEKLY_REPORT_DEFAULT_WEEKDAY,
            hour=WEEKLY_REPORT_DEFAULT_HOUR,
            send_empty=False,
        )
    by = session.get(User, row.updated_by) if row.updated_by else None
    return WeeklyReportSchedule(
        weekday=row.weekly_report_weekday,
        hour=row.weekly_report_hour,
        send_empty=row.weekly_report_send_empty,
        updated_by_name=by.display_name if by is not None else None,
        updated_at=row.updated_at,
    )


def set_weekly_report_schedule(
    session: Session, team_id: str, *, weekday: int, hour: int, send_empty: bool, user_id: str
) -> WeeklyReportSchedule:
    """A member sets when the team's report goes out. Logged with who did it."""
    require_team_member(session, user_id=user_id, team_id=team_id)
    if not 0 <= weekday <= 6:
        raise ValidationError("weekday must be 0 (Monday) to 6 (Sunday)")
    if not 0 <= hour <= 23:
        raise ValidationError("hour must be 0 to 23")
    statement = pg_insert(IntelTeamSettings).values(
        team_id=team_id,
        weekly_report_weekday=weekday,
        weekly_report_hour=hour,
        weekly_report_send_empty=send_empty,
        updated_by=user_id,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[IntelTeamSettings.team_id],
            set_={
                "weekly_report_weekday": weekday,
                "weekly_report_hour": hour,
                "weekly_report_send_empty": send_empty,
                "updated_by": user_id,
                "updated_at": func.now(),
            },
        )
    )
    session.flush()
    session.expire_all()
    log.info(
        "intelligence_weekly_report_schedule_set",
        team_id=team_id,
        by=user_id,
        weekday=weekday,
        hour=hour,
        send_empty=send_empty,
    )
    return weekly_report_schedule(session, team_id)


def latest_weekly_slot(*, weekday: int, hour: int, now: datetime) -> datetime:
    """The last ``weekday`` at ``hour`` (Korean time) at or before ``now``."""
    local = now.astimezone(_KST)
    slot = local.replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(
        days=(local.weekday() - weekday) % 7
    )
    return slot if slot <= local else slot - timedelta(days=7)


def weekly_period(slot: datetime) -> tuple[date, date]:
    """The seven Korean days before the slot's day: ``[start, end)``."""
    end = slot.astimezone(_KST).date()
    return end - timedelta(days=7), end


class DueWeeklyReport(NamedTuple):
    team_id: str
    period_start: date
    period_end: date


def due_weekly_reports(session: Session, now: datetime) -> list[DueWeeklyReport]:
    """Teams whose latest slot passed within ``WEEKLY_REPORT_CATCH_UP`` and whose
    report for that week is not out yet: not written, or written and neither
    posted nor set aside. Only teams with a meeting in
    ``WEEKLY_REPORT_ACTIVE_WITHIN``."""
    active = session.scalars(
        sa.select(Meeting.team_id)
        .where(Meeting.created_at >= now - WEEKLY_REPORT_ACTIVE_WITHIN, _not_expired(now))
        .distinct()
    ).all()
    due = []
    for team_id in sorted(active):
        schedule = weekly_report_schedule(session, team_id)
        slot = latest_weekly_slot(weekday=schedule.weekday, hour=schedule.hour, now=now)
        if now - slot > WEEKLY_REPORT_CATCH_UP:
            continue
        start, end = weekly_period(slot)
        row = session.get(IntelReport, (team_id, start))
        if row is None or (row.posted_at is None and row.not_posted is None):
            due.append(DueWeeklyReport(team_id, start, end))
    return due


def weekly_report_is_empty(metrics: dict[str, Any]) -> bool:
    """Nothing to say: no meeting analysed that week, and no item overdue or
    carried over (or none known)."""
    return (
        not metrics.get("meeting_count")
        and not metrics.get("overdue_action_items")
        and not metrics.get("carried_over_action_items")
    )


def claim_weekly_report_post(
    session: Session, team_id: str, period_start: date, *, now: datetime
) -> IntelReport | None:
    """The report to post, its post claimed; ``None`` when it is out already,
    set aside, or missing. An empty week on a team that did not ask for those
    is set aside (``not_posted="empty"``) and never claimed. The caller
    commits before posting, and gives the claim back if posting fails."""
    row = session.get(IntelReport, (team_id, period_start), with_for_update=True)
    if row is None or row.posted_at is not None or row.not_posted is not None:
        return None
    if (
        weekly_report_is_empty(row.metrics_json)
        and not weekly_report_schedule(session, team_id).send_empty
    ):
        row.not_posted = "empty"
        session.flush()
        log.info(
            "intelligence_weekly_report_empty", team_id=team_id, period_start=str(period_start)
        )
        return None
    row.posted_at = now
    session.flush()
    return row


def release_weekly_report_post(session: Session, team_id: str, period_start: date) -> None:
    """A post that failed is tried again on the next tick."""
    row = session.get(IntelReport, (team_id, period_start), with_for_update=True)
    if row is not None:
        row.posted_at = None
        session.flush()


# --- Meeting report (agent layer, #260/#261) --------------------------------
#
# The Report subagent composes one summary per meeting from B, C, D and E's
# tools (agent-layer.md section 3.1); E stores it and posts it, because
# outbound goes through the module that owns the surface (section 8 rule 2).
# Whether a post needs a person's approval first is the main agent's gate, not
# this code's -- these functions do what they are asked, once.

MEETING_REPORT_MAX_CHARS: Final = 3000
"""Slack's limit for one section block's text. A longer body is refused rather
than cut, because a cut summary reads as a finished one. Counted as Slack
receives it, after ``_slack_escape``: "&" goes out as five characters.

It also has to fit ``autune_integrations.privacy.MAX_OUTBOUND_CHARS`` (4000),
which counts every string in the request: the body once, the title (at most
400) as the preview, and the button's scaffolding. ``post_meeting_report``
therefore never sends the body twice."""

MEETING_REPORT_OPEN_ACTION: Final = "intel_meeting_report_open"
"""The button's ``action_id``. slack.py acknowledges it so Slack shows no error."""

MEETING_REPORT_REVIEW_ACTION: Final = "intel_meeting_report_review"
"""The review button's ``action_id``, acknowledged in slack.py like the other."""


def save_meeting_report(
    session: Session,
    meeting_id: str,
    body_markdown: str,
    *,
    pending_review: bool = False,
    draft_id: str | None = None,
) -> IntelMeetingReport:
    """Store the meeting's report body, replacing an unsent one.

    The body is checked for personal data before it is written, not only when
    it is posted: privacy.md section 2 keeps unmasked text out of every store.
    A report already posted is not replaced -- people have read that version,
    and a silent edit would make the stored copy disagree with what they saw.

    ``draft_id`` is stored as given, ``None`` included: a draft saved without
    one also ends every pending approval of the draft it replaced, since that
    approval names an id the row no longer holds. Intended -- the approver did
    not see this text.

    **The body holds this meeting's content only.** The row is deleted with this
    meeting and nothing else, so a sentence quoted from another meeting -- a past
    decision from D's lineage, a team-wide action item from B -- would outlive
    that meeting's deletion here. The Report subagent links another meeting by
    its title and a link, and does not quote it. Decided on #459 (option (a) of
    the review note), over storing cited meeting ids for a sweep.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    if len(_slack_escape(body_markdown)) > MEETING_REPORT_MAX_CHARS:
        raise ValidationError(
            f"report body exceeds {MEETING_REPORT_MAX_CHARS} characters", field="body_markdown"
        )
    assert_masked(body_markdown, destination="intel_meeting_reports")

    # Locked: a re-publish racing the deliver task's claim waits for it, then sees
    # sent_at and refuses, instead of overwriting a report people have read.
    row = session.get(IntelMeetingReport, meeting_id, with_for_update=True)
    if row is None:
        row = IntelMeetingReport(
            meeting_id=meeting_id,
            team_id=meeting.team_id,
            body_markdown=body_markdown,
            pending_review=pending_review,
            draft_id=draft_id,
        )
        session.add(row)
    elif row.sent_at is not None:
        raise ConflictError("meeting report was already posted", meeting_id=meeting_id)
    else:
        row.body_markdown = body_markdown
        row.pending_review = pending_review
        row.draft_id = draft_id
        # A rerun's draft is the model's text again: a person's edit is
        # overwritten, so their name must not stay on it (#642 review).
        row.edited_by = None
        row.edited_at = None
    session.flush()
    return row


_KST: Final = timezone(timedelta(hours=9))
"""The report's date is the team's calendar date. A fixed offset until a team
timezone setting exists (#227 is undecided)."""

MEETING_REPORT_FOOTER: Final = "자동 생성된 리포트입니다"


def meeting_report_document(
    meeting: Meeting, body_markdown: str, *, now: datetime | None = None
) -> str:
    """Header, the subagent's body, footer -- the text ``save_meeting_report`` stores.

    The header names the meeting unless its title holds personal data, the
    same test ``_report_preview`` makes; stored text is checked by
    ``assert_masked``, so such a title would refuse the whole report.

    The footer says when the draft was written. The body is B's, C's and D's
    state at that moment, and the post goes out only when a person approves
    it, possibly days later: without the time, "확인 대기 9건" would read as
    current after half of them were confirmed. Live numbers are on the
    dashboard, which the post's button opens.
    """
    when = (meeting.started_at or meeting.created_at).astimezone(_KST)
    drafted = (now or datetime.now(UTC)).astimezone(_KST)
    title = meeting.title if meeting.title and not find_unmasked(meeting.title) else "회의 리포트"
    footer = f"{MEETING_REPORT_FOOTER} · {drafted.month}/{drafted.day} {drafted:%H:%M} 기준."
    return f"📋 {title} · {when.month}/{when.day}\n\n{body_markdown}\n\n{footer}"


def _slack_escape(text: str) -> str:
    """Slack's three control characters as entities, so text reads as text.

    A body may now be a person's (#642): "<!channel>" or "<https://x|상세보기>"
    must not go out under the bot's name as a mention or a disguised link. The
    report's own markup -- bullets, emoji, line breaks -- has none of the three.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _meeting_report_blocks(meeting_id: str, body_markdown: str, pending_review: bool) -> list[dict]:
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": _slack_escape(body_markdown)}}
    ]
    base_url = get_settings().web_base_url
    if not base_url:
        return blocks
    meeting_url = f"{base_url.rstrip('/')}/meetings/{meeting_id}"
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "상세보기"},
            "url": meeting_url,
            "action_id": MEETING_REPORT_OPEN_ACTION,
        }
    ]
    if pending_review:
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "확인하러 가기"},
                "url": f"{meeting_url}/actions",
                "action_id": MEETING_REPORT_REVIEW_ACTION,
            }
        )
    blocks.append({"type": "actions", "elements": buttons})
    return blocks


@dataclass(frozen=True)
class ClaimedReport:
    """What ``post_meeting_report`` needs, read while the claim held the row."""

    meeting_id: str
    preview: str
    """Slack's top-level ``text``: the notification preview, never the body."""
    body_markdown: str
    pending_review: bool


def claim_meeting_report(
    session: Session, meeting_id: str, *, draft_id: str | None = None
) -> ClaimedReport | None:
    """Mark the report sent and hand it out, or ``None`` if it was already claimed.

    The claim comes **before** the post and its transaction must commit before
    the post is made: at most once, the rule B's Notion sync (#342) and D's
    brief (#437) follow. The row is locked, so two tasks for the same meeting
    (a retry, a second approval) cannot both see it unsent. A post that fails
    after the claim costs this meeting its report; it never sends a second copy.
    """
    row = session.execute(
        sa.select(IntelMeetingReport)
        .where(IntelMeetingReport.meeting_id == meeting_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("meeting report", meeting_id)
    if row.sent_at is not None:
        return None
    if draft_id is not None and row.draft_id != draft_id:
        # Approved for an earlier draft. The newer one stays unclaimed for its
        # own approval rather than going out under this one.
        raise ConflictError("meeting report draft was replaced", meeting_id=meeting_id)
    title = session.scalar(sa.select(Meeting.title).where(Meeting.id == meeting_id)) or ""
    editor = (
        session.scalar(sa.select(User.display_name).where(User.id == row.edited_by))
        if row.edited_by is not None
        else None
    )
    claimed = ClaimedReport(
        meeting_id=meeting_id,
        preview=_report_preview(title),
        body_markdown=_with_editor(row.body_markdown, editor),
        pending_review=row.pending_review,
    )
    # The same check post_message runs, made before sent_at is set: a refusal
    # here leaves the report unclaimed and retryable (#476 review).
    check_outbound(
        {"text": claimed.preview, "blocks": _report_blocks(claimed)},
        destination="slack",
        addressing=SlackClient.addressing,
    )
    row.sent_at = datetime.now(UTC)
    session.flush()
    return claimed


def _report_blocks(report: ClaimedReport) -> list[dict]:
    return _meeting_report_blocks(report.meeting_id, report.body_markdown, report.pending_review)


def _report_preview(title: str) -> str:
    """The title as the preview, unless it holds personal data.

    Decided here, before the claim commits, because ``check_outbound`` refuses
    the whole post over one string: a title such as ``kim@example.com 1:1``
    (common when a calendar event names the meeting) would otherwise fail the
    post after the claim, and the report could never be claimed again. The body
    was checked when it was saved; the title never was.
    """
    if not title or find_unmasked(title):
        return "회의 리포트"
    return f"{title} 회의 리포트"


def post_meeting_report(slack: SlackApi, channel: str, report: ClaimedReport) -> str:
    """Post a claimed report. Returns Slack's message ts.

    The body goes out once, in the section block. The top-level ``text`` is
    Slack's notification preview (see ``autune_integrations.privacy.strings_in``),
    so it carries the title (``_report_preview``); putting the body there as well sent it twice and
    pushed a body above ~1,940 characters past ``MAX_OUTBOUND_CHARS``.
    """
    return slack.post_message(
        channel,
        report.preview,
        _report_blocks(report),
    )


def record_meeting_report_post(
    session: Session, meeting_id: str, channel: str, slack_ts: str
) -> None:
    """Remember where the report went, so a later edit or thread can find it."""
    row = session.get(IntelMeetingReport, meeting_id)
    if row is None:  # the meeting was deleted while the post was in flight
        return
    row.slack_channel = channel
    row.slack_ts = slack_ts
    session.flush()


# --- Speaking ratio (pipeline step 7) -----------------------------------
#
# Private to the speaker: computed from A's utterances, delivered by DM, never
# written to a table, never returned for anyone else. docs/architecture/privacy.md
# section 3 is binding here.

_MIN_SPEAKERS_FOR_RATIO: Final = 3
"""Below this many people, per ``speaker_count_for_gate``, the ratio is withheld.

The measured shares sum to 1.0, so when only two people's speech is in the
denominator a recipient's ``1 - ratio`` is the other person's share exactly —
the response would *contain* someone else's speaking ratio, which
docs/architecture/privacy.md section 3 forbids. An above/below-baseline band
does not help: with two, the two are mirror images. So the number does not go
out at all — ``/me/speaking-ratio`` answers with ``reason="small_meeting"`` and
no DM is sent.

The gate counts people, not the consenting head count and not participant
rows: a meeting with three consenting participants where one only listened
still splits its speech two ways, and a real speaker split across two
participant rows by diarization counts once *once identified* — one row
resolves to the same ``user_id`` as the other. Before identification a split
cannot be merged, and an unidentified label cannot be told apart from "the
unconfirmed other half of an already-identified speaker" either — so
``speaker_count_for_gate`` counts unidentified shares as zero rather than
guess they are new people. The cost is that a real N-person meeting with a
speaker not yet identified reads as smaller than N until identification
finishes; since this is recomputed on every request, it self-corrects rather
than needing a retry."""


def compute_speaking_shares(session: Session, meeting_id: str) -> list[SpeakingShare]:
    """Each identified participant's share of the meeting's *measured* speech.

    Reads only ``utterances`` and ``participants`` (shared, read-only). The
    denominator is speech attributed to a participant who consented to speaker
    attribution — the same population the even-share baseline
    (``_consented_participant_count``) is taken over, so ``ratio`` and that
    baseline answer the same question. Unattributed speech and speech from a
    non-consenting participant are both left out.
    """
    rows = session.execute(
        sa.select(
            Utterance.participant_id,
            Participant.user_id,
            Utterance.start_sec,
            Utterance.end_sec,
        )
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(
            Utterance.meeting_id == meeting_id,
            Participant.consented.is_(True),
        )
    ).all()
    segments = [
        SpeechSegment(participant_id=pid, user_id=uid, start_sec=start, end_sec=end)
        for pid, uid, start, end in rows
    ]
    return speaking_shares(segments)


def _consented_participant_count(session: Session, meeting_id: str) -> int:
    """How many distinct *people* have consented — not how many participant rows.

    Diarization can split one real speaker into two participant rows that are
    later confirmed to the same ``user_id``; counting rows would inflate the
    even-share baseline's population past ``compute_speaking_shares``'s, which
    counts people. An unidentified participant (``user_id`` still ``None``) has
    no shared identity to collapse onto, so each such row counts as one person,
    same as ``speaking_shares``' own grouping key.
    """
    return (
        session.scalar(
            sa.select(func.count(func.distinct(func.coalesce(Participant.user_id, Participant.id))))
            .select_from(Participant)
            .where(
                Participant.meeting_id == meeting_id,
                Participant.consented.is_(True),
            )
        )
        or 0
    )


def speaking_ratio_for_user(
    session: Session, meeting_id: str, user_id: str
) -> SpeakingRatioRead | None:
    """The user's own share of ``meeting_id``, or ``None`` if they were not in it.

    ``None`` return is only "you were not in this meeting", which the route turns
    into a 404. Otherwise a ``SpeakingRatioRead`` comes back, and its ``ratio``
    may still be ``None``:

    - ``reason="small_meeting"`` — fewer than ``_MIN_SPEAKERS_FOR_RATIO``
      consenting participants actually spoke, so any real number would fix
      another person's.
    - ``reason="not_measured"`` — the requester did not consent to attribution
      on every one of their participant rows, so their speech may not be fully
      in the measured set. Distinct from a consenting participant who was
      simply silent, who gets ``0.0``.
    """
    participant_rows = list(
        session.scalars(
            sa.select(Participant).where(
                Participant.meeting_id == meeting_id,
                Participant.user_id == user_id,
            )
        )
    )
    if not participant_rows:
        return None
    # A split speaker's rows can disagree on consent (confirmed separately);
    # requiring every row to consent, rather than picking one row arbitrarily,
    # makes the answer independent of which row a lookup happens to see.
    fully_consented = all(p.consented for p in participant_rows)

    shares = compute_speaking_shares(session, meeting_id)
    # The baseline is 1 / (consenting participants), silent ones included; the
    # gate counts only the people the ratio is actually split between, with
    # unidentified splits undercounted rather than left to inflate it.
    participant_count = _consented_participant_count(session, meeting_id)

    if speaker_count_for_gate(shares) < _MIN_SPEAKERS_FOR_RATIO:
        return SpeakingRatioRead(
            meeting_id=meeting_id,
            ratio=None,
            participant_count=participant_count,
            reason="small_meeting",
        )
    if not fully_consented:
        return SpeakingRatioRead(
            meeting_id=meeting_id,
            ratio=None,
            participant_count=participant_count,
            reason="not_measured",
        )

    mine = next((s for s in shares if s.user_id == user_id), None)
    return SpeakingRatioRead(
        meeting_id=meeting_id,
        ratio=mine.ratio if mine is not None else 0.0,
        participant_count=participant_count,
        reason=None,
        stored=False,
    )


def _deliver_personal(
    slack: SlackApi, recipient_user_id: str, fallback: str, blocks: list[dict]
) -> None:
    """Send a DM that describes exactly one person, to that person only.

    Takes a single id and uses it for both the guard's subject and the
    recipient, so the two cannot drift apart at a call site — the reason
    ``assert_personal_delivery`` takes them separately (it also refuses a
    channel) is that in other flows they come from different places.
    """
    assert_personal_delivery(
        subject_id=recipient_user_id, recipient_id=recipient_user_id, is_direct=True
    )
    slack.send_dm(recipient_user_id, fallback, blocks)


def send_personal_feedback(session: Session, slack: SlackApi, meeting_id: str) -> int:
    """DM each identified participant their own speaking ratio. Returns the count.

    A speaker with no linked user account cannot be reached and is skipped, and
    so is one Slack refuses permanently (``PermanentIntegrationError``: not
    linked for DMs, or ``ok: false``). A transient failure still raises, so the
    task fails loudly rather than dropping a DM that a later run could send. The
    ratio is withheld entirely — no DM at all — when fewer than
    ``_MIN_SPEAKERS_FOR_RATIO`` people (``speaker_count_for_gate``) spoke, for
    the same reason ``/me/speaking-ratio`` withholds it. The ratio is not
    stored anywhere; this function writes nothing.
    """
    shares = compute_speaking_shares(session, meeting_id)
    gate_count = speaker_count_for_gate(shares)
    if gate_count < _MIN_SPEAKERS_FOR_RATIO:
        log.info(
            "speaking_ratio_feedback_withheld_small_meeting",
            meeting_id=meeting_id,
            speakers=gate_count,
        )
        return 0

    participant_count = _consented_participant_count(session, meeting_id)
    sent = 0
    for share in shares:
        if share.user_id is None:
            log.info(
                "speaking_ratio_recipient_unmapped",
                meeting_id=meeting_id,
                participant_id=share.participant_id,
            )
            continue
        fallback, blocks = build_speaking_ratio_dm(
            ratio=share.ratio, participant_count=participant_count
        )
        try:
            _deliver_personal(slack, share.user_id, fallback, blocks)
        except PermanentIntegrationError as exc:
            # One person Slack cannot reach -- no linked account, or a
            # refusal such as channel_not_found -- must not cost everyone
            # after them their DM (#478 makes both raise). Ids and the error
            # code only: the message can quote the recipient.
            log.info(
                "speaking_ratio_recipient_unreachable",
                meeting_id=meeting_id,
                user_id=share.user_id,
                error=exc.code,
            )
            continue
        sent += 1

    log.info("speaking_ratio_feedback_sent", meeting_id=meeting_id, recipients=sent)
    return sent


# --- action progress (#605) ---------------------------------------------------------
#
# B republishes every team's action-item counts per meeting every ten minutes
# (``TeamActionProgress``). E keeps the latest one for the dashboard's real
# completion rate and the weekly report; the quality score keeps its own
# confirmation rate, computed once from ``ExtractionResult``.


def store_action_progress(session: Session, snapshot: TeamActionProgress) -> bool:
    """Keep ``snapshot`` as its team's action progress if it is newer than the stored one.

    ``False`` when it was not kept: an older snapshot delivered late, or a team
    that no longer exists. The header is one guarded upsert, so a late older
    snapshot leaves the newer one standing and two deliveries for one team
    serialise on its row; when it is kept, the team's meeting rows are replaced
    wholesale. A meeting id that is not one of the team's meetings -- deleted
    since B counted, or another team's -- is not kept, so no row points outside
    the team; nor is one past its retention window, which no one reads any more.
    """
    if session.get(Team, snapshot.team_id) is None:
        log.info("intelligence_action_progress_team_gone", team_id=snapshot.team_id)
        return False
    statement = pg_insert(IntelActionProgress).values(
        team_id=snapshot.team_id, as_of=snapshot.as_of
    )
    kept = session.scalar(
        statement.on_conflict_do_update(
            index_elements=[IntelActionProgress.team_id],
            set_={"as_of": statement.excluded.as_of, "updated_at": func.now()},
            where=IntelActionProgress.as_of < statement.excluded.as_of,
        ).returning(IntelActionProgress.team_id)
    )
    if kept is None:
        log.info("intelligence_action_progress_older_ignored", team_id=snapshot.team_id)
        return False

    session.execute(
        sa.delete(IntelActionProgressMeeting).where(
            IntelActionProgressMeeting.team_id == snapshot.team_id
        )
    )
    named = {m.meeting_id: m for m in snapshot.meetings}
    own = (
        set(
            session.scalars(
                sa.select(Meeting.id).where(
                    Meeting.team_id == snapshot.team_id,
                    Meeting.id.in_(list(named)),
                    _not_expired(datetime.now(UTC)),
                )
            )
        )
        if named
        else set()
    )
    session.add_all(
        IntelActionProgressMeeting(
            team_id=snapshot.team_id,
            meeting_id=meeting_id,
            confirmed=named[meeting_id].confirmed,
            done=named[meeting_id].done,
            overdue=named[meeting_id].overdue,
        )
        for meeting_id in sorted(own)
    )
    session.flush()
    # Counts of rows only: the numbers are per meeting and stay out of logs.
    log.info(
        "intelligence_action_progress_stored",
        team_id=snapshot.team_id,
        meetings=len(own),
        not_kept=len(named) - len(own),
    )
    return True


def _not_expired(now: datetime) -> sa.ColumnElement[bool]:
    """A meeting still inside its retention window. One past ``expires_at`` waits
    for A's sweep, and nothing of it is read or shown until then."""
    return sa.or_(Meeting.expires_at.is_(None), Meeting.expires_at > now)


ACTION_COMPLETION_WINDOW: Final = timedelta(weeks=4)
"""The dashboard's completion rate counts meetings held this recently.

Fixed, not the team's retention: a team keeping 30 days and one keeping 90
read the same span, so their rates compare, and the rate shows how the team is
doing now rather than over a quarter. A meeting leaving the window moves the
rate; the card names the window so that reads as what it is."""


ACTION_PROGRESS_MIN_MEETINGS: Final = 3
"""A total drawn from fewer meetings is not shown. With one or two, the team
total is those meetings' counts, and when every item is one person's it is that
person's completion record -- what the contract's usage rule forbids (#800
review). The heatmap's floor (``MIN_MEETINGS_PER_HEATMAP_CELL``) for the same reason."""


@dataclass(frozen=True)
class ActionProgressTotals:
    """The team's totals from its latest snapshot, or ``None`` throughout when
    that snapshot is missing or older than ``ACTION_PROGRESS_STALE_AFTER``.

    ``completion_rate`` is over the meetings held within
    ``ACTION_COMPLETION_WINDOW`` (``completion_meetings`` of them); ``overdue``
    over every meeting the snapshot listed that has not expired, since an item
    past its due date matters however old its meeting is. Either is ``None``
    when drawn from fewer than ``ACTION_PROGRESS_MIN_MEETINGS`` meetings. With
    no meetings at all there is nothing to identify: no rate (no items is not
    0% done) and 0 overdue. ``carried_over``, asked for by the weekly report
    only, is the confirmed items not done from kept meetings held before a
    cutoff, under the same floor.
    """

    completion_rate: float | None = None
    completion_meetings: int | None = None
    overdue: int | None = None
    as_of: datetime | None = None
    carried_over: int | None = None


def _shown(meetings: int) -> bool:
    return meetings == 0 or meetings >= ACTION_PROGRESS_MIN_MEETINGS


def _action_progress_totals(
    session: Session, team_id: str, now: datetime, *, carried_before: datetime | None = None
) -> ActionProgressTotals:
    """Done over confirmed over the team's meetings held within
    ``ACTION_COMPLETION_WINDOW``, and the overdue count over all of its kept
    meetings -- team totals only, never one meeting's counts (the contract's
    usage rule). A meeting with no ``started_at`` is dated by its creation, as
    A orders meetings.

    With ``carried_before``, also the confirmed items not done from kept
    meetings held before it: what earlier meetings carry into the week."""
    as_of = session.scalar(
        sa.select(IntelActionProgress.as_of).where(IntelActionProgress.team_id == team_id)
    )
    if as_of is None or as_of < now - ACTION_PROGRESS_STALE_AFTER:
        return ActionProgressTotals()
    held = func.coalesce(Meeting.started_at, Meeting.created_at)
    recent = held >= now - ACTION_COMPLETION_WINDOW
    earlier = held < (carried_before or now)
    row = IntelActionProgressMeeting
    meetings, overdue, recent_meetings, confirmed, done, earlier_meetings, carried = (
        session.execute(
            sa.select(
                func.count(),
                func.coalesce(func.sum(row.overdue), 0),
                func.count().filter(recent),
                func.coalesce(func.sum(row.confirmed).filter(recent), 0),
                func.coalesce(func.sum(row.done).filter(recent), 0),
                func.count().filter(earlier),
                func.coalesce(func.sum(row.confirmed - row.done).filter(earlier), 0),
            )
            .join(Meeting, Meeting.id == row.meeting_id)
            .where(row.team_id == team_id, _not_expired(now))
        ).one()
    )
    return ActionProgressTotals(
        completion_rate=(
            done / confirmed
            if confirmed and recent_meetings >= ACTION_PROGRESS_MIN_MEETINGS
            else None
        ),
        completion_meetings=int(recent_meetings),
        overdue=int(overdue) if _shown(meetings) else None,
        as_of=as_of,
        carried_over=(
            int(carried) if carried_before is not None and _shown(earlier_meetings) else None
        ),
    )


# --- the dashboard's meeting-report card (10/2) --------------------------------------
#
# A report is meeting text, so these check that the person asking is on the team,
# as every route of this module now does (#814). Any member may edit a draft until it
# is posted. An edit takes a new ``draft_id``, so an approval given for the
# model's text can never post it. Nothing is posted from the card: the edit is
# announced (``autune.intelligence.meeting_report_changed``) and the Report
# subagent proposes its post again, for a person with the ``report`` scope to
# approve -- the same L2 gate as the model's draft (#642 review, #674). A rerun
# of the Report overwrites an edit (``save_meeting_report``). A posted report
# changes only by a correction posted under it, never in place.

MEETING_REPORTS_SHOWN: Final = 20
"""The card lists this many reports, newest meeting first."""

_EDITED_FOOTER: Final = "자동 생성된 리포트를 팀원이 고쳤습니다."
"""The footer stored after an edit. It does not name the editor: stored text
outlives the account, and a deleted person's name must not (invariant 11, #642
review). ``_with_editor`` adds the name from ``edited_by`` when the report is
read or posted, so it goes when the account does."""

_EDITED_FOOTER_NAMED: Final = "자동 생성된 리포트를 {name}님이 고쳤습니다."


def _editor_footer(name: str | None) -> str:
    """The footer naming ``name``, or the stored one when there is no name or the
    name itself looks like personal data: ``check_outbound`` would refuse the
    whole post over it, and the editor cannot fix it by editing the text."""
    if not name or find_unmasked(name):
        return _EDITED_FOOTER
    return _EDITED_FOOTER_NAMED.format(name=name)


def _with_editor(document: str, name: str | None) -> str:
    """The stored document with its editor's current name in the footer.

    Unchanged for a model's draft, for an editor whose account is gone, and when
    the name would take the text past the cap -- checked at the edit with the
    name the editor had then, so only a later, longer name gets here.
    """
    named = _with_editor_unchecked(document, name)
    if len(_slack_escape(named)) > MEETING_REPORT_MAX_CHARS:
        return document
    return named


def _with_editor_unchecked(document: str, name: str | None) -> str:
    if not document.endswith(_EDITED_FOOTER):
        return document
    return document.removesuffix(_EDITED_FOOTER) + _editor_footer(name)


def split_report_document(document: str) -> tuple[str, str, str]:
    """The stored document as header line (without its mark), body, and footer.

    ``meeting_report_document`` writes "📋 <title> · <date>", a blank line, the
    body, a blank line and a footer starting "자동 생성". The footer is not part
    of what a person edits: E writes it, and after an edit it names the editor.
    """
    header, _, rest = document.partition("\n\n")
    body, sep, footer = rest.rpartition("\n\n")
    if not sep or not footer.startswith("자동 생성"):
        body, footer = rest, ""
    return header.removeprefix("📋").strip(), body, footer


def _is_member(session: Session, *, user_id: str, team_id: str) -> bool:
    return (
        session.scalar(
            sa.select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        is not None
    )


def require_team_member(session: Session, *, user_id: str, team_id: str) -> None:
    """Raise unless ``user_id`` is on ``team_id``. A token says who is asking, not
    which team's reports they may read."""
    if not _is_member(session, user_id=user_id, team_id=team_id):
        raise PermissionDeniedError("not a member of this team")


def _report_for_member(session: Session, meeting_id: str, user_id: str) -> IntelMeetingReport:
    """The report, locked, for a member of its team. Anyone else gets the same
    not-found as for a meeting that does not exist, so an id's existence does
    not leak (``tools._not_found`` does the same)."""
    row = session.get(IntelMeetingReport, meeting_id, with_for_update=True)
    if row is None or not _is_member(session, user_id=user_id, team_id=row.team_id):
        raise NotFoundError("meeting report", meeting_id)
    return row


def _report_read(
    row: IntelMeetingReport, editor: str | None, corrector: str | None = None
) -> MeetingReportRead:
    title, body, footer = split_report_document(_with_editor(row.body_markdown, editor))
    draft = row.sent_at is None
    return MeetingReportRead(
        meeting_id=row.meeting_id,
        title=title,
        body=body,
        footer=footer,
        status="draft" if draft else "posted",
        posted_at=row.sent_at,
        pending_review=row.pending_review,
        edited_by_name=editor,
        edited_at=row.edited_at,
        updated_at=row.updated_at,
        in_slack=row.slack_channel is not None and row.slack_ts is not None,
        correction_body=row.correction_body,
        corrected_by_name=corrector if row.correction_body is not None else None,
        corrected_at=row.corrected_at,
        correction_status=_correction_status(row, datetime.now(UTC)),
    )


def list_meeting_reports(
    session: Session, team_id: str, *, user_id: str
) -> list[MeetingReportRead]:
    """The team's latest reports, newest meeting first, for one of its members."""
    require_team_member(session, user_id=user_id, team_id=team_id)
    held = func.coalesce(Meeting.started_at, Meeting.created_at)
    editor_user = aliased(User)
    corrector_user = aliased(User)
    rows = session.execute(
        sa.select(IntelMeetingReport, editor_user.display_name, corrector_user.display_name)
        .join(Meeting, Meeting.id == IntelMeetingReport.meeting_id)
        .outerjoin(editor_user, editor_user.id == IntelMeetingReport.edited_by)
        .outerjoin(corrector_user, corrector_user.id == IntelMeetingReport.corrected_by)
        .where(IntelMeetingReport.team_id == team_id)
        .order_by(held.desc(), IntelMeetingReport.meeting_id.desc())
        .limit(MEETING_REPORTS_SHOWN)
    ).all()
    return [_report_read(row, editor, corrector) for row, editor, corrector in rows]


def edit_meeting_report(
    session: Session,
    meeting_id: str,
    body: str,
    *,
    user_id: str,
    base_updated_at: datetime | None = None,
) -> MeetingReportRead:
    """Replace a draft's body with a team member's text and record who did it.

    E keeps the header line and writes a footer saying a person edited it; the
    name is added when it is read or posted (``_with_editor``), never stored. The
    draft takes a new ``draft_id``: an approval queued for the model's text --
    even one already approved and waiting for the worker -- then posts nothing.
    The caller commits, then announces the change (``enqueue``), and the Report
    subagent proposes this draft's post for approval. Refused once
    posted (people have read that version), and when ``base_updated_at`` shows
    someone saved a newer version since the editor opened it. The text passes
    the same length cap and personal-data check as a model's draft; a refusal
    names categories, never the text. The "this meeting only" rule (#459) is an
    instruction to the subagent; a person's text is not checked against it.
    """
    row = _report_for_member(session, meeting_id, user_id)
    if row.sent_at is not None:
        raise ConflictError("meeting report was already posted", meeting_id=meeting_id)
    if base_updated_at is not None and base_updated_at != row.updated_at:
        raise ConflictError("meeting report changed since it was opened", meeting_id=meeting_id)
    if not body.strip():
        raise ValidationError("report body is empty", field="body")
    if _same_text(body, split_report_document(row.body_markdown)[1]):
        # Saved as it was: nothing to approve again, and a new proposal would
        # notify the approvers a second time for the same text (#642 review).
        raise ValidationError("report body is unchanged", field="body")
    editor = session.scalar(sa.select(User.display_name).where(User.id == user_id))
    header = row.body_markdown.partition("\n\n")[0]
    document = f"{header}\n\n{body}\n\n{_EDITED_FOOTER}"
    # Counted as it will go out: escaped, with the editor's name in the footer.
    if len(_slack_escape(_with_editor_unchecked(document, editor))) > MEETING_REPORT_MAX_CHARS:
        raise ValidationError(f"report exceeds {MEETING_REPORT_MAX_CHARS} characters", field="body")
    try:
        assert_masked(document, destination="intel_meeting_reports")
    except PrivacyViolationError as exc:
        # A person typed it, so it is theirs to correct: refuse with the
        # categories, as the subagent's draft action does, never the text.
        categories = ", ".join(exc.details.get("categories", []))
        raise ValidationError(
            f"report still holds personal data: {categories}", field="body"
        ) from exc
    now = datetime.now(UTC)
    row.body_markdown = document
    row.edited_by = user_id
    row.edited_at = now
    row.draft_id = new_id("rdr")
    # Set here, not by the column's onupdate: Postgres' now() is the
    # transaction's start, and two saves in one transaction must still differ.
    row.updated_at = now
    session.flush()
    return _report_read(row, editor)


@dataclass(frozen=True)
class AwaitingPost:
    """What waits for a person to approve its post, by the id the approval pins."""

    kind: Literal["draft", "correction"]
    id: str
    """A ``draft_id`` or a ``correction_id``."""


def _same_text(a: str, b: str) -> bool:
    """Equal once trailing spaces on each line and blank edges are ignored: a save
    that changes nothing a reader sees asks no approver again (#705 review).
    Line breaks still count -- they change how the post reads."""

    def seen(text: str) -> str:
        return "\n".join(line.rstrip() for line in text.strip().splitlines())

    return seen(a) == seen(b)


def meeting_report_awaiting_approval(session: Session, meeting_id: str) -> AwaitingPost | None:
    """The meeting's draft before it is posted, its unsent correction after; or ``None``.

    What the Report subagent proposes to post when a person's change is
    announced. Read now, not the one the announcement was about: a later change
    or a rerun replaced it, and its own proposal supersedes this one in the
    queue anyway. A draft and a correction never wait at once -- a correction
    is accepted only once the report is posted, and a posted report has no
    draft left to approve.
    """
    row = session.get(IntelMeetingReport, meeting_id)
    if row is None:
        return None
    if row.sent_at is None:
        return AwaitingPost("draft", row.draft_id) if row.draft_id is not None else None
    if _correction_waits(row) and row.correction_id is not None:
        return AwaitingPost("correction", row.correction_id)
    return None


def _correction_waits(row: IntelMeetingReport) -> bool:
    return (
        row.correction_id is not None
        and row.correction_sent_at is None
        and row.correction_failed_at is None
    )


def meeting_report_posted(session: Session, meeting_id: str) -> bool:
    """Whether the meeting's report was claimed for posting -- the channel has it."""
    return (
        session.scalar(
            sa.select(IntelMeetingReport.sent_at).where(IntelMeetingReport.meeting_id == meeting_id)
        )
        is not None
    )


# --- announcing a person's change (#698) ---------------------------------------------
#
# The card's routes commit a change and then queue its announcement. Queueing can
# fail after the commit, and a lost announcement would leave the change with no
# approval request and no way to ask again (an unchanged save is refused). So the
# announcement records which change it covered, and a sweep announces any change
# no announcement covered.

ANNOUNCE_RETRY_AFTER: Final = timedelta(minutes=2)
"""A change younger than this is left to its own announcement, already queued."""

ANNOUNCE_SWEEP_LIMIT: Final = 50


def _changed_at(row: IntelMeetingReport) -> datetime | None:
    """When a person last changed what waits for approval; ``None`` if nothing of
    theirs waits -- a model's draft is proposed by the Report's own run."""
    if row.sent_at is None:
        return row.edited_at
    if _correction_waits(row):
        return row.corrected_at
    return None


@dataclass(frozen=True)
class ClaimedAnnouncement:
    """The change an announcement is about to cover, and what was covered before."""

    changed_at: datetime
    previous: datetime | None


def claim_meeting_report_announcement(
    session: Session, meeting_id: str
) -> ClaimedAnnouncement | None:
    """Take the waiting change for one announcement, or ``None`` if there is none.

    ``None`` too when an announcement already covered it: the route's task and
    the sweep, or a redelivered task, can both reach the same change, and each
    announcement reaches the approvers by DM (#705 review). Under the row lock,
    and committed before the publish, as a delivery claim is.
    """
    row = session.get(IntelMeetingReport, meeting_id, with_for_update=True)
    if row is None:
        return None
    changed_at = _changed_at(row)
    if changed_at is None or (row.announced_at is not None and row.announced_at >= changed_at):
        return None
    claimed = ClaimedAnnouncement(changed_at=changed_at, previous=row.announced_at)
    row.announced_at = changed_at
    session.flush()
    return claimed


def release_meeting_report_announcement(
    session: Session, meeting_id: str, claimed: ClaimedAnnouncement
) -> None:
    """Undo a claim whose publish failed, so the sweep announces the change later.
    Left alone if another announcement has moved on since."""
    row = session.get(IntelMeetingReport, meeting_id, with_for_update=True)
    if row is not None and row.announced_at == claimed.changed_at:
        row.announced_at = claimed.previous
        session.flush()


def meeting_reports_unannounced(session: Session, *, now: datetime) -> list[str]:
    """Meetings whose report holds a person's change no announcement covered.

    Older than ``ANNOUNCE_RETRY_AFTER``, so a change still on its way through
    the queue is not announced twice. At most ``ANNOUNCE_SWEEP_LIMIT`` a run.
    """
    r = IntelMeetingReport
    cutoff = now - ANNOUNCE_RETRY_AFTER
    edited_draft = sa.and_(
        r.sent_at.is_(None),
        r.edited_at.is_not(None),
        r.edited_at <= cutoff,
        sa.or_(r.announced_at.is_(None), r.announced_at < r.edited_at),
    )
    waiting_correction = sa.and_(
        r.sent_at.is_not(None),
        r.correction_id.is_not(None),
        r.correction_sent_at.is_(None),
        r.correction_failed_at.is_(None),
        r.corrected_at <= cutoff,
        sa.or_(r.announced_at.is_(None), r.announced_at < r.corrected_at),
    )
    return list(
        session.scalars(
            sa.select(r.meeting_id)
            .where(sa.or_(edited_draft, waiting_correction))
            .order_by(r.updated_at)
            .limit(ANNOUNCE_SWEEP_LIMIT)
        )
    )


# --- a correction to a posted report (10/2) -------------------------------------------
#
# A posted report is never changed in place: people have read it. A member writes
# a correction on the dashboard card. Like an edit it reaches the channel only
# after a person with the ``report`` scope approves it (#674): the change is
# announced, the Report subagent proposes ``publish_meeting_report_correction``
# with the correction's id, and the approved one goes out as a reply under the
# original post -- or as a new message in the team's channel when the thread is
# out of reach. Each is sent at most once, and checked and escaped like any
# report text.

_CORRECTION_HEADER: Final = "✏️ 수정본 · {when} · {name}"

CORRECTION_SEND_WINDOW: Final = timedelta(minutes=5)
"""An approved correction not posted by then counts as failed, and a new one is
accepted. Measured from the claim, not from writing it: a correction waits for
approval as long as that takes (#658 review). After the claim, posting can stop
short with nothing to report it -- a Slack error, a lost worker."""

_CORRECTION_ORPHAN: Final = "원래 게시물을 찾지 못해 새 메시지로 올립니다 · {title}"
_ORPHAN_TITLE_CHARS: Final = 80
"""The title in that line is cut to this, and a name in the header to
``_NAME_CHARS``, so the message stays under ``MAX_OUTBOUND_CHARS`` whatever the
meeting or the person is called (#658 review): 3,000 checked at writing, plus a
longer name at the claim, plus this line."""
_NAME_CHARS: Final = 40


def correct_meeting_report(
    session: Session, meeting_id: str, body: str, *, user_id: str
) -> MeetingReportRead:
    """Store a member's correction to a posted report, to wait for approval.

    Refused for a draft (edit it instead), for a report whose post never reached
    Slack (there is no message to correct, and the channel never saw the
    original), for a team whose Slack or channel was disconnected since (it
    could never be posted, #698), while an approved correction is being posted,
    and for anyone
    outside the team (404, as for a missing meeting). A correction still waiting
    for approval is replaced: the new one takes a new ``correction_id``, so the
    approval for the earlier one posts nothing. Saving the same text again is
    refused, so the approvers are not asked twice. The caller commits, then
    announces it. The text passes the length cap and the personal-data check; a
    refusal names categories, never the text.
    """
    row = _report_for_member(session, meeting_id, user_id)
    if row.sent_at is None:
        raise ConflictError("meeting report is not posted; edit the draft", meeting_id=meeting_id)
    if row.slack_channel is None or row.slack_ts is None:
        raise ConflictError("meeting report did not reach slack", meeting_id=meeting_id)
    if not _slack_connected(session, row.team_id):
        raise ConflictError("slack is not connected", meeting_id=meeting_id)
    now = datetime.now(UTC)
    status = _correction_status(row, now)
    if status == "sending":
        raise ConflictError("the previous correction is still being sent", meeting_id=meeting_id)
    if not body.strip():
        raise ValidationError("correction is empty", field="body")
    if status == "pending" and _same_text(body, row.correction_body or ""):
        raise ValidationError("correction is unchanged", field="body")
    corrector = session.scalar(sa.select(User.display_name).where(User.id == user_id)) or ""
    text = _correction_text(body, name=corrector, when=now)
    if len(_slack_escape(text)) > MEETING_REPORT_MAX_CHARS:
        raise ValidationError(
            f"correction exceeds {MEETING_REPORT_MAX_CHARS} characters", field="body"
        )
    try:
        assert_masked(text, destination="intel_meeting_reports")
    except PrivacyViolationError as exc:
        categories = ", ".join(exc.details.get("categories", []))
        raise ValidationError(
            f"correction still holds personal data: {categories}", field="body"
        ) from exc
    row.correction_body = body
    row.correction_id = new_id("rcr")
    row.corrected_by = user_id
    row.corrected_at = now
    row.correction_sent_at = None
    row.correction_slack_ts = None
    row.correction_failed_at = None
    session.flush()
    editor = (
        session.scalar(sa.select(User.display_name).where(User.id == row.edited_by))
        if row.edited_by is not None
        else None
    )
    return _report_read(row, editor, corrector)


def _correction_status(
    row: IntelMeetingReport, now: datetime
) -> Literal["pending", "sending", "sent", "failed"] | None:
    if row.correction_body is None:
        return None
    if row.correction_slack_ts is not None:
        return "sent"
    if row.correction_failed_at is not None:
        return "failed"
    if row.correction_sent_at is None:
        return "pending"
    if now - row.correction_sent_at < CORRECTION_SEND_WINDOW:
        return "sending"
    return "failed"


def _slack_connected(session: Session, team_id: str) -> bool:
    """The team has Slack connected -- a token and a channel to post to. Read
    without decrypting the token: whether it is there is all this needs."""
    found = session.execute(
        sa.select(TeamIntegration.config, TeamIntegration.secret).where(
            TeamIntegration.team_id == team_id, TeamIntegration.service == "slack"
        )
    ).one_or_none()
    if found is None or found.secret is None:
        return False
    return bool((found.config or {}).get("channel"))


def fail_meeting_report_correction(
    session: Session, meeting_id: str, *, correction_id: str
) -> None:
    """Mark an approved correction failed when it cannot be posted at all.

    For a team whose Slack or channel went away after the report was posted:
    left unclaimed it would read "승인 대기" forever (#698). Only the correction
    the approval named, and only while unclaimed.
    """
    row = session.get(IntelMeetingReport, meeting_id, with_for_update=True)
    if row is None or row.correction_id != correction_id or row.correction_sent_at is not None:
        return
    row.correction_failed_at = datetime.now(UTC)
    session.flush()


def _correction_text(body: str, *, name: str, when: datetime) -> str:
    local = when.astimezone(_KST)
    shown = name[:_NAME_CHARS] if name and not find_unmasked(name) else "팀원"
    header = _CORRECTION_HEADER.format(when=f"{local.month}/{local.day} {local:%H:%M}", name=shown)
    return f"{header}\n\n{body}"


def meeting_report_correction(session: Session, meeting_id: str, correction_id: str) -> str | None:
    """The correction ``correction_id`` names as it would be posted, or ``None``
    once another replaced it -- for the approval card's preview."""
    row = session.get(IntelMeetingReport, meeting_id)
    if row is None or row.correction_body is None or row.correction_id != correction_id:
        return None
    name = (
        session.scalar(sa.select(User.display_name).where(User.id == row.corrected_by))
        if row.corrected_by
        else None
    ) or ""
    return _correction_text(row.correction_body, name=name, when=row.corrected_at or row.updated_at)


@dataclass(frozen=True)
class ClaimedCorrection:
    """What ``post_meeting_report_correction`` needs, read under the claim."""

    meeting_id: str
    channel: str | None
    thread_ts: str | None
    title: str
    text: str
    """Slack-escaped already."""
    correction_id: str
    """Which correction this is: ``record_meeting_report_correction`` keeps the ts
    only if no newer correction replaced it while this one was in flight."""


def claim_meeting_report_correction(
    session: Session, meeting_id: str, *, correction_id: str
) -> ClaimedCorrection | None:
    """Mark the approved correction sent and hand it out.

    ``None`` when there is none or another task claimed it; ``ConflictError``
    when a newer correction replaced the approved one -- it waits for its own
    approval. Committed before the post: at most once.
    """
    row = session.execute(
        sa.select(IntelMeetingReport)
        .where(IntelMeetingReport.meeting_id == meeting_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or row.correction_body is None:
        return None
    if row.correction_id != correction_id:
        raise ConflictError("meeting report correction was replaced", meeting_id=meeting_id)
    if row.correction_sent_at is not None:
        return None
    name = (
        session.scalar(sa.select(User.display_name).where(User.id == row.corrected_by))
        if row.corrected_by
        else None
    ) or ""
    title, _, _ = split_report_document(row.body_markdown)
    text = _slack_escape(
        _correction_text(row.correction_body, name=name, when=row.corrected_at or datetime.now(UTC))
    )
    # The check the Slack client runs, made before the claim, as the report's
    # claim does: a refusal leaves the correction unclaimed (#658 review).
    check_outbound({"text": text}, destination="slack", addressing=SlackClient.addressing)
    row.correction_sent_at = datetime.now(UTC)
    session.flush()
    return ClaimedCorrection(
        meeting_id=meeting_id,
        channel=row.slack_channel,
        thread_ts=row.slack_ts,
        title=_slack_escape(title[:_ORPHAN_TITLE_CHARS]),
        text=text,
        correction_id=correction_id,
    )


def post_meeting_report_correction(
    slack: SlackApi, channel: str, correction: ClaimedCorrection
) -> str:
    """Reply under the original post; when it cannot be reached, post a new
    message in ``channel`` that says so. Returns the message ts."""
    if correction.channel and correction.thread_ts:
        try:
            return slack.reply_in_thread(correction.channel, correction.thread_ts, correction.text)
        except PermanentIntegrationError:
            # Reconnected with a new bot or channel: the old thread is out of reach.
            log.info(
                "intelligence_meeting_report_correction_thread_lost",
                meeting_id=correction.meeting_id,
            )
    orphan = _CORRECTION_ORPHAN.format(title=correction.title)
    return slack.post_message(channel, f"{orphan}\n{correction.text}")


def record_meeting_report_correction(
    session: Session, meeting_id: str, slack_ts: str, *, correction_id: str
) -> None:
    row = session.get(IntelMeetingReport, meeting_id)
    if row is None:  # the meeting was deleted while the post was in flight
        return
    if row.correction_id != correction_id:
        # A newer correction was written after this one failed its window; it
        # waits for its own approval and records its own ts.
        return
    row.correction_slack_ts = slack_ts
    session.flush()


# --- a person deleted their own speech (#587, #614) -----------------------------------


@on_speech_deleted("intelligence")
def forget_deleted_speech(user_id: str, utterance_ids: Sequence[str]) -> None:
    """Before a person's own speech is deleted: E lets go of the words it copied
    from it -- in its copies of B's, C's and D's results and in the meeting
    reports quoting them (``forget``). The work stays.

    Registered from this file because ``router`` imports it: A's deletion runs
    in the API process, which imports every router and no ``tasks`` module (as
    C's hook is). Raises on failure, so A's deletion stops rather than leaving
    the words in E; commits in its own transaction before A deletes, erring
    toward deleting more. Ids and counts only.
    """
    with session_scope() as session:
        done = forget.forget_speech(session, utterance_ids)
    log.info(
        "intelligence_speech_forgotten",
        user_id=user_id,
        utterances=len(utterance_ids),
        meetings=len(done.meetings),
        texts_replaced=done.texts_replaced,
        topics_removed=done.topics_removed,
        reports_changed=done.reports_changed,
    )
