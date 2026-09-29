"""Pre-meeting brief: a recap of the meeting this one follows, before it starts.

Shortly before a scheduled meeting (``ContextSettings.brief_lead_minutes``,
default 10), ``tasks.send_brief`` posts a brief to the team channel. It carries
two things:

- **A recap of the past meeting this one follows** -- its topics and the
  decisions it recorded, including whether a decision changed or reversed an
  earlier one. Both come from D's own rows (``ctx_embeddings``,
  ``ctx_decision_versions``); nothing is generated, so the brief quotes only
  what the context tab already shows.
- **The issues this meeting is expected to take up** -- from Jira, which
  module B integrates. That interface is not agreed yet, so ``agenda_for``
  returns none and the brief says so (docs/modules/context.md, "Pre-meeting
  brief").

Which past meeting, in order:

1. ``series`` -- the most recent analyzed meeting with the same title. A
   recurring meeting ("주간 스탠드업") is the common case, and its title is
   exactly the signal retrieval cannot use: it names the series, not a topic.
2. ``topic`` -- hybrid retrieval over the title and agenda, re-ranked, the same
   retrieve-broad, re-rank-narrow path topic linking takes, asserted on the
   same rule a topic link is.
3. ``latest`` -- the team's most recent analyzed meeting.

**Stores which meeting was chosen, never the recap** -- see ``CtxBrief``. The
recap is rendered from the chosen meeting's rows each time it is read, so a
meeting swept by retention takes its recap with it.

Reads shared entities; writes only ``ctx_briefs``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from autune_context.config import get_settings
from autune_context.dates import meeting_day
from autune_context.models import CtxBrief, CtxDecisionVersion, CtxEmbedding, CtxMeetingStatus
from autune_context.notify import AgendaItem, BriefDecision, BriefRecap
from autune_context.pipeline import get_embedder, get_reranker
from autune_context.pipeline.retrieval import HybridRetriever, visible_meeting_clauses
from autune_context.pipeline.topics import TopicSegment
from autune_contracts import ChangeType
from autune_core import Meeting, get_logger
from autune_core.errors import NotFoundError

log = get_logger(__name__)

SERIES = "series"
TOPIC = "topic"
LATEST = "latest"

_CANDIDATE_LIMIT = 200
"""How many of the team's most recent analyzed meetings the series and latest
steps look through. A series match further back than this is not "the meeting
this one follows" anyway."""


@dataclass(frozen=True)
class Brief:
    meeting_id: str
    title: str
    starts_at: datetime | None
    recap: BriefRecap | None
    recap_gone: bool
    """A past meeting was chosen, and has since been deleted or expired."""
    match_reason: str | None
    agenda: tuple[AgendaItem, ...]
    sent_at: datetime | None


def _meeting_time():
    return func.coalesce(Meeting.started_at, Meeting.created_at)


def _is_visible(meeting: Meeting, now: datetime) -> bool:
    return meeting.expires_at is None or meeting.expires_at > now


def minutes_until(starts_at: datetime, now: datetime) -> int:
    """Whole minutes to the start, rounded up and never below one -- "0분 뒤"
    reads as already started."""
    return max(1, math.ceil((starts_at - now).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Which meetings are due
# --------------------------------------------------------------------------- #


def due_meeting_ids(session: Session, now: datetime) -> list[str]:
    """Scheduled meetings starting within the lead time that have no brief yet.

    A meeting whose start has already passed is not due: a brief after the
    meeting began is noise, so a brief missed while the worker was down stays
    missed. ``status`` must still be ``scheduled`` -- module A moves it on once
    a recording starts, and a meeting already under way needs no brief.
    """
    lead = timedelta(minutes=get_settings().brief_lead_minutes)
    has_brief = select(CtxBrief.meeting_id).where(CtxBrief.meeting_id == Meeting.id).exists()
    return list(
        session.scalars(
            select(Meeting.id)
            .where(
                Meeting.status == "scheduled",
                Meeting.started_at > now,
                Meeting.started_at <= now + lead,
                or_(Meeting.expires_at.is_(None), Meeting.expires_at > now),
                ~has_brief,
            )
            .order_by(Meeting.started_at, Meeting.id)
        )
    )


# --------------------------------------------------------------------------- #
# What goes in it
# --------------------------------------------------------------------------- #


def agenda_for(session: Session, meeting: Meeting) -> list[AgendaItem]:
    """The issues ``meeting`` is expected to take up.

    Jira is module B's integration, and how its issues reach D is not agreed
    yet -- D never calls Jira itself and never reads another module's tables.
    Until that interface lands in ``packages/contracts`` (#436), there is no agenda,
    and the brief says "no linked issues" rather than guessing one.
    """
    return []


def _normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def _analyzed_before(session: Session, meeting: Meeting, now: datetime) -> list[tuple[str, str]]:
    """``(id, title)`` of the team's visible meetings that D has analyzed and
    that started before ``meeting``, newest first.

    "Analyzed" is ``topic_linking_done``: a meeting D never processed has no
    topics or decisions to recap.
    """
    assert meeting.started_at is not None
    return [
        (meeting_id, title)
        for meeting_id, title in session.execute(
            select(Meeting.id, Meeting.title)
            .join(CtxMeetingStatus, CtxMeetingStatus.meeting_id == Meeting.id)
            .where(
                CtxMeetingStatus.topic_linking_done.is_(True),
                Meeting.id != meeting.id,
                _meeting_time() < meeting.started_at,
                *visible_meeting_clauses(meeting.team_id, now=now),
            )
            .order_by(_meeting_time().desc(), Meeting.id.desc())
            .limit(_CANDIDATE_LIMIT)
        )
    ]


def _topic_match(
    session: Session, meeting: Meeting, agenda: list[AgendaItem], candidate_ids: set[str]
) -> str | None:
    """The past meeting whose topics best match this one's title and agenda,
    if confident enough to assert it.

    The same two signals, and the same thresholds, topic linking asserts a link
    on (``service._link_topic``): dense similarity at or above
    ``link_similarity_threshold``, or a re-rank score of the candidate's closest
    segment at or above ``link_confidence_threshold``. The strongest of the two
    picks among the confident candidates.
    """
    assert meeting.started_at is not None
    settings = get_settings()
    embedder = get_embedder()
    reranker = get_reranker()

    query = " ".join([meeting.title, *(item.title for item in agenda)])
    segment = TopicSegment(
        label=meeting.title[:400], text=query, utterance_ids=[], vector=embedder.embed([query])[0]
    )
    retriever = HybridRetriever(
        session, retrieve_top_k=settings.retrieve_top_k, rrf_k=settings.rrf_k
    )
    candidates = [
        candidate
        for candidate in retriever.retrieve(
            segment,
            team_id=meeting.team_id,
            before=meeting.started_at,
            exclude_meeting_id=meeting.id,
        )
        if candidate.linked_meeting_id in candidate_ids
    ][: settings.rerank_top_k]
    if not candidates:
        return None

    scores = reranker.score(query, [candidate.passage for candidate in candidates])
    confident = [
        (max(candidate.similarity, float(score)), candidate.linked_meeting_id)
        for candidate, score in zip(candidates, scores, strict=True)
        if candidate.similarity >= settings.link_similarity_threshold
        or score >= settings.link_confidence_threshold
    ]
    return max(confident)[1] if confident else None


def choose_previous_meeting(
    session: Session, meeting: Meeting, agenda: list[AgendaItem], *, now: datetime
) -> tuple[str | None, str | None]:
    """``(previous_meeting_id, match_reason)`` -- see the module docstring for
    the order. ``(None, None)`` when the team has no analyzed meeting before
    this one.

    A failure in the topic step (a model endpoint down) falls through to
    ``latest`` rather than failing the brief: the recap is still useful from
    the most recent meeting, and a brief retried until the meeting starts is
    one that never arrives.
    """
    analyzed = _analyzed_before(session, meeting, now)
    if not analyzed:
        return None, None

    title = _normalize_title(meeting.title)
    for meeting_id, past_title in analyzed:
        if _normalize_title(past_title) == title:
            return meeting_id, SERIES

    try:
        matched = _topic_match(session, meeting, agenda, {meeting_id for meeting_id, _ in analyzed})
    except Exception as exc:  # noqa: BLE001 -- any model failure degrades to ``latest``
        # The type only: an HTTP error's message can carry the request, and
        # the request is this meeting's title.
        log.warning(
            "context_brief_topic_match_failed", meeting_id=meeting.id, error=type(exc).__name__
        )
        matched = None
    if matched is not None:
        return matched, TOPIC
    return analyzed[0][0], LATEST


def _recap(session: Session, previous_meeting_id: str, now: datetime) -> BriefRecap | None:
    """The chosen meeting's topics and decisions, or ``None`` once it is gone.

    Expired counts as gone, the same as deleted: the retention sweep may not
    have run yet, but D stops showing a meeting at ``expires_at``, not at the
    sweep (docs/modules/context.md, "Deletion").
    """
    previous = session.get(Meeting, previous_meeting_id)
    if previous is None or not _is_visible(previous, now):
        return None

    topics: list[str] = []
    for label in session.scalars(
        select(CtxEmbedding.ref_label)
        .where(CtxEmbedding.meeting_id == previous_meeting_id, CtxEmbedding.kind == "topic")
        .order_by(CtxEmbedding.id)
    ):
        if label not in topics:
            topics.append(label)
    decisions = [
        BriefDecision(statement=statement, change_type=ChangeType(change_type))
        for statement, change_type in session.execute(
            select(CtxDecisionVersion.current_statement, CtxDecisionVersion.change_type)
            .where(CtxDecisionVersion.meeting_id == previous_meeting_id)
            .order_by(CtxDecisionVersion.id)
        )
    ]
    return BriefRecap(
        meeting_id=previous.id,
        title=previous.title,
        day=meeting_day(previous.started_at) if previous.started_at else None,
        topics=tuple(topics),
        decisions=tuple(decisions),
    )


def _render(
    session: Session, meeting: Meeting, row: CtxBrief, agenda: list[AgendaItem], now: datetime
) -> Brief:
    recap = (
        _recap(session, row.previous_meeting_id, now)
        if row.previous_meeting_id is not None
        else None
    )
    return Brief(
        meeting_id=meeting.id,
        title=meeting.title,
        starts_at=meeting.started_at,
        recap=recap,
        # Chosen once (``match_reason`` is set) and not readable now: either
        # SET NULL by a deletion, or expired and not yet swept.
        recap_gone=row.match_reason is not None and recap is None,
        match_reason=row.match_reason,
        agenda=tuple(agenda),
        sent_at=row.sent_at,
    )


# --------------------------------------------------------------------------- #
# Composing and reading
# --------------------------------------------------------------------------- #


def compose_due_brief(
    session: Session, meeting_id: str, *, now: datetime, will_send: bool
) -> Brief | None:
    """Claim this meeting's brief, choose what it recaps, and return it.

    ``None`` when the meeting is no longer due -- it started, or a recording
    already began -- or when another run claimed it first. The claim is the
    ``ctx_briefs`` insert itself (``ON CONFLICT DO NOTHING``): an overlapping
    run blocks on the uncommitted row, then finds it taken. Periodic runs
    overlap by design (``autune_core.periodic``), and the same meeting is
    enqueued once per tick until its row commits.

    ``will_send`` stamps ``sent_at`` in this same transaction, before the caller
    posts to Slack: a worker that dies between this commit and the post loses
    the brief rather than sending it twice -- the trade the other notices make.
    A failure *before* the commit rolls the claim back, and the next tick
    retries while the meeting is still ahead.
    """
    meeting = session.get(Meeting, meeting_id)
    if (
        meeting is None
        or meeting.status != "scheduled"
        or meeting.started_at is None
        or meeting.started_at <= now
    ):
        log.info("context_brief_not_due", meeting_id=meeting_id)
        return None

    claimed = session.scalar(
        insert(CtxBrief)
        .values(meeting_id=meeting_id)
        .on_conflict_do_nothing(index_elements=[CtxBrief.meeting_id])
        .returning(CtxBrief.meeting_id)
    )
    if claimed is None:
        log.info("context_brief_already_claimed", meeting_id=meeting_id)
        return None
    row = session.get(CtxBrief, meeting_id)
    assert row is not None

    agenda = agenda_for(session, meeting)
    previous_meeting_id, match_reason = choose_previous_meeting(session, meeting, agenda, now=now)
    row.previous_meeting_id = previous_meeting_id
    row.match_reason = match_reason
    if will_send:
        row.sent_at = now
    session.flush()
    log.info(
        "context_brief_composed",
        meeting_id=meeting_id,
        previous_meeting_id=previous_meeting_id,
        match_reason=match_reason,
        agenda_items=len(agenda),
    )
    return _render(session, meeting, row, agenda, now)


def get_brief(session: Session, meeting_id: str, *, now: datetime | None = None) -> Brief:
    """A composed brief, as the app shows it.

    Raises ``NotFoundError`` before the brief is composed (it is composed at
    ``brief_lead_minutes`` before the start, not on request -- composing needs
    the models, which the API process does not load) and once the meeting
    itself has expired.
    """
    now = now or datetime.now(tz=UTC)
    meeting = session.get(Meeting, meeting_id)
    row = session.get(CtxBrief, meeting_id)
    if meeting is None or row is None or not _is_visible(meeting, now):
        raise NotFoundError("brief", meeting_id)
    return _render(session, meeting, row, agenda_for(session, meeting), now)
