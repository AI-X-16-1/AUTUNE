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
from datetime import UTC, datetime, timedelta
from typing import Final

import sqlalchemy as sa
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from autune_contracts import (
    ActionItem,
    ActionStatus,
    ContextLinks,
    ExtractionResult,
    GapReport,
    GapSeverity,
    IntelligenceSnapshot,
    Participation,
    QualityScore,
)
from autune_contracts.intelligence import Grade
from autune_core import Meeting, Participant, Utterance, get_logger
from autune_core.errors import NotFoundError
from autune_integrations import SlackApi, assert_personal_delivery

from .config import get_settings
from .feedback import build_speaking_ratio_dm
from .models import (
    IntelAlignment,
    IntelCompletion,
    IntelGapPattern,
    IntelReport,
    IntelScore,
)
from .schemas import DashboardRead, DashboardScoreEntry, HeatmapCell, SpeakingRatioRead
from .speaking import SpeakingShare, SpeechSegment, speaking_shares

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
GRADE_CUTOFFS: Final = ((0.9, "A"), (0.8, "B"), (0.7, "C"), (0.6, "D"), (0.5, "E"))
"""Descending; value below the last cutoff is F. First heuristic — P2 tunes these."""

_MAX_PATTERN_TYPE: Final = 100
"""``Gap.category`` has no length limit but ``intel_gap_patterns.pattern_type`` is
``String(100)`` and part of the PK; truncate before it reaches the table."""


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
    _ = (
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

    distribution = (
        dict(Counter(g.category[:_MAX_PATTERN_TYPE] for g in gap.gaps)) if gap is not None else {}
    )

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
        ids_by_category: dict[str, list[str]] = {}
        for g in gap.gaps:
            category = g.category[:_MAX_PATTERN_TYPE]
            ids_by_category.setdefault(category, []).append(g.id)
        for category, count in distribution.items():
            session.add(
                IntelGapPattern(
                    meeting_id=meeting_id,
                    pattern_type=category,
                    team_id=meeting.team_id,
                    count=count,
                    source_gap_ids=ids_by_category[category],
                )
            )

    row.aggregated_at = datetime.now(UTC)
    session.flush()
    session.expire_all()

    return IntelligenceSnapshot(
        meeting_id=meeting_id,
        team_id=meeting.team_id,
        quality_score=score,
        gap_distribution=distribution,
        alignment=[],
        predictions=[],
        missing_sources=missing,
    )


# --- Read API -------------------------------------------------------------
#
# Routes in ``router.py`` parse the path parameter and call one of these; the
# query and its shaping live here so they are testable without HTTP. Every
# function reads only ``intel_*`` tables.

_DASHBOARD_RECENT_LIMIT: Final = 12
"""How many recent meeting scores the dashboard returns for the trend strip.
Bucketing them into the eight-week bars on S26 is the frontend's job."""


def get_score(session: Session, meeting_id: str) -> IntelScore:
    """The meeting's quality score, or a 404 that names no meeting content."""
    row = session.get(IntelScore, meeting_id)
    if row is None:
        raise NotFoundError("intelligence score", meeting_id)
    return row


def get_heatmap(session: Session, team_id: str) -> list[HeatmapCell]:
    """Role-pair alignment for the team, averaged over its scored meetings.

    Empty until the alignment step that fills ``intel_alignment`` is built.
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
        .order_by(IntelAlignment.role_a, IntelAlignment.role_b)
    ).all()
    return [
        HeatmapCell(role_a=role_a, role_b=role_b, score=float(avg), meeting_count=count)
        for role_a, role_b, avg, count in rows
    ]


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
    gap_rows = session.execute(
        sa.select(IntelGapPattern.pattern_type, func.sum(IntelGapPattern.count))
        .where(IntelGapPattern.team_id == team_id)
        .group_by(IntelGapPattern.pattern_type)
    ).all()

    return DashboardRead(
        team_id=team_id,
        meeting_count=len(scores),
        average_score=(sum(values) / len(values)) if values else None,
        action_item_completion_rate=(sum(rates) / len(rates)) if rates else None,
        recent_scores=[
            DashboardScoreEntry(meeting_id=s.meeting_id, grade=s.grade, value=s.value)
            for s in scores[:_DASHBOARD_RECENT_LIMIT]
        ],
        gap_distribution={pattern: int(total) for pattern, total in gap_rows},
    )


# --- Speaking ratio (pipeline step 7) -----------------------------------
#
# Private to the speaker: computed from A's utterances, delivered by DM, never
# written to a table, never returned for anyone else. docs/architecture/privacy.md
# section 3 is binding here.

_MIN_SPEAKERS_FOR_RATIO: Final = 3
"""Below this many *consenting participants who spoke*, the ratio is withheld.

The measured shares sum to 1.0, so when only two people's speech is in the
denominator a recipient's ``1 - ratio`` is the other person's share exactly —
the response would *contain* someone else's speaking ratio, which
docs/architecture/privacy.md section 3 forbids. An above/below-baseline band
does not help: with two, the two are mirror images. So the number does not go
out at all — ``/me/speaking-ratio`` answers with ``reason="small_meeting"`` and
no DM is sent.

The gate counts speakers, not the consenting head count: a meeting with three
consenting participants where one only listened still splits its speech two
ways, and that is the case this guards."""


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
    - ``reason="not_measured"`` — the requester did not consent to attribution,
      so their speech is not in the measured set. Distinct from a consenting
      participant who was simply silent, who gets ``0.0``.
    """
    participant = session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting_id,
            Participant.user_id == user_id,
        )
    )
    if participant is None:
        return None

    shares = compute_speaking_shares(session, meeting_id)
    # The baseline is 1 / (consenting participants), silent ones included; the
    # gate counts only the speakers the ratio is actually split between.
    participant_count = _consented_participant_count(session, meeting_id)

    if len(shares) < _MIN_SPEAKERS_FOR_RATIO:
        return SpeakingRatioRead(
            meeting_id=meeting_id,
            ratio=None,
            participant_count=participant_count,
            reason="small_meeting",
        )
    if not participant.consented:
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

    A speaker with no linked user account cannot be reached and is skipped. The
    ratio is withheld entirely — no DM at all — when fewer than
    ``_MIN_SPEAKERS_FOR_RATIO`` consenting participants spoke, for the same
    reason ``/me/speaking-ratio`` withholds it. The ratio is not stored
    anywhere; this function writes nothing.
    """
    shares = compute_speaking_shares(session, meeting_id)
    if len(shares) < _MIN_SPEAKERS_FOR_RATIO:
        log.info(
            "speaking_ratio_feedback_withheld_small_meeting",
            meeting_id=meeting_id,
            speakers=len(shares),
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
        _deliver_personal(slack, share.user_id, fallback, blocks)
        sent += 1

    log.info("speaking_ratio_feedback_sent", meeting_id=meeting_id, recipients=sent)
    return sent
