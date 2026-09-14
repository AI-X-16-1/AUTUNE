"""Business logic for the Meeting Context Engine (``autune_context``).

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module. Cross-module output goes out as the ``ContextLinks``
contract on a Celery task, never as a direct call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from celery import current_app
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from autune_context.config import get_settings
from autune_context.models import (
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_context.pipeline import get_embedder, get_reranker
from autune_context.pipeline.retrieval import HybridRetriever
from autune_context.pipeline.topics import extract_topics
from autune_contracts import ContextLinks, TopicLink
from autune_core import Meeting, get_logger, session_scope

if TYPE_CHECKING:
    from autune_contracts import TranscriptReady

log = get_logger(__name__)

# D publishes to E's consumer task directly — the async pipeline has no broker
# abstraction (docs/architecture/async-pipeline.md, "Payloads"). A string, not an
# import, so the module boundary holds.
_CONTEXT_CONSUMER_TASK = "autune.intelligence.on_context_completed"

_PUBLISHABLE = ("asserted", "confirmed")


# --------------------------------------------------------------------------- #
# Topic linking — off autune.transcript.ready, in parallel with B and C
# --------------------------------------------------------------------------- #


def run_topic_linking(transcript: TranscriptReady) -> None:
    """Extract this meeting's topics, link them to past meetings, persist.

    Idempotent: a re-run replaces every ``ctx_*`` row this task owns for the
    meeting. Does not publish — that is ``publish_if_ready``.
    """
    settings = get_settings()
    embedder = get_embedder()
    reranker = get_reranker()

    with session_scope() as session:
        meeting = session.get(Meeting, transcript.meeting_id)
        if meeting is None:
            raise ValueError(f"{transcript.meeting_id}: meeting row not found")

        topics = extract_topics(
            list(transcript.utterances),
            embedder,
            window=settings.topic_window,
            min_segment=settings.topic_min_segment,
            depth_threshold=settings.topic_depth_threshold,
        )
        log.info(
            "context_topics_extracted",
            meeting_id=transcript.meeting_id,
            topics=len(topics),
        )

        session.execute(
            delete(CtxEmbedding).where(
                CtxEmbedding.meeting_id == transcript.meeting_id,
                CtxEmbedding.kind == "topic",
            )
        )
        session.execute(
            delete(CtxTopicLink).where(CtxTopicLink.meeting_id == transcript.meeting_id)
        )
        session.flush()

        for topic in topics:
            session.add(
                CtxEmbedding(
                    meeting_id=transcript.meeting_id,
                    kind="topic",
                    ref_label=topic.label,
                    embedding=topic.vector,
                    model_version=embedder.model_version,
                )
            )
        session.flush()

        retriever = HybridRetriever(
            session, retrieve_top_k=settings.retrieve_top_k, rrf_k=settings.rrf_k
        )
        before = meeting.started_at or datetime.now(tz=UTC)
        links_written = 0
        for topic in topics:
            candidates = retriever.retrieve(
                topic,
                team_id=meeting.team_id,
                before=before,
                exclude_meeting_id=transcript.meeting_id,
            )
            links_written += _link_topic(
                session, transcript.meeting_id, topic, candidates, reranker, settings, embedder
            )

        _upsert_status(
            session,
            transcript.meeting_id,
            topic_linking_done=True,
            deadline_at=datetime.now(tz=UTC) + timedelta(seconds=settings.publish_timeout_s),
        )
        log.info(
            "context_topic_linking_done",
            meeting_id=transcript.meeting_id,
            links=links_written,
        )


def _link_topic(
    session: Session,
    meeting_id: str,
    topic,
    candidates: list,
    reranker,
    settings,
    embedder,
) -> int:
    if not candidates:
        return 0
    scores = reranker.score(topic.text, [c.topic_label for c in candidates])
    ranked = sorted(zip(candidates, scores, strict=True), key=lambda cs: cs[1], reverse=True)

    written = 0
    for candidate, rerank_score in ranked[: settings.rerank_top_k]:
        if candidate.linked_meeting_date is None:
            log.info(
                "context_link_skipped_no_date",
                meeting_id=meeting_id,
                linked_meeting_id=candidate.linked_meeting_id,
            )
            continue
        status = "asserted" if rerank_score >= settings.link_confidence_threshold else "pending"
        session.add(
            CtxTopicLink(
                meeting_id=meeting_id,
                topic_label=topic.label,
                linked_meeting_id=candidate.linked_meeting_id,
                linked_meeting_date=_as_datetime(candidate.linked_meeting_date),
                similarity=_clamp(candidate.similarity),
                rerank_score=_clamp(float(rerank_score)),
                confidence=_clamp(float(rerank_score)),
                status=status,
                retriever_version=f"hybrid-rrf+{embedder.model_version}",
                reranker_version=reranker.model_version,
            )
        )
        written += 1
    return written


# --------------------------------------------------------------------------- #
# Decision lineage entry point — Phase 3 builds the versions here
# --------------------------------------------------------------------------- #


def mark_extraction_seen(meeting_id: str) -> None:
    """Record that B has reported. Phase 3 replaces this with lineage building."""
    with session_scope() as session:
        _upsert_status(session, meeting_id, extraction_seen=True)


# --------------------------------------------------------------------------- #
# Publishing — once both halves are in, or B has timed out
# --------------------------------------------------------------------------- #


def publish_if_ready(meeting_id: str) -> bool:
    """Publish ``ContextLinks`` when topic linking is done and either lineage is
    done, B has reported, or the deadline has passed. Returns whether it published.

    A failure in B must never cost the user their topic links.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id)
        if status is None or not status.topic_linking_done:
            return False
        if status.published_at is not None:
            return False

        timed_out = status.deadline_at is not None and datetime.now(tz=UTC) >= status.deadline_at
        if not (status.lineage_done or status.extraction_seen or timed_out):
            return False

        links = _build_context_links(session, meeting_id, status)
        current_app.send_task(_CONTEXT_CONSUMER_TASK, args=[links.model_dump(mode="json")])
        status.published_at = datetime.now(tz=UTC)
        log.info(
            "context_published",
            meeting_id=meeting_id,
            topic_links=len(links.topic_links),
            missing_sources=links.missing_sources,
        )
        return True


def _build_context_links(
    session: Session, meeting_id: str, status: CtxMeetingStatus
) -> ContextLinks:
    rows = session.scalars(
        select(CtxTopicLink).where(
            CtxTopicLink.meeting_id == meeting_id,
            CtxTopicLink.status.in_(_PUBLISHABLE),
        )
    ).all()
    topic_links = [
        TopicLink(
            topic_label=row.topic_label,
            linked_meeting_id=row.linked_meeting_id,
            linked_meeting_date=row.linked_meeting_date.date(),
            similarity=row.similarity,
            rerank_score=row.rerank_score,
        )
        for row in rows
        if row.linked_meeting_id is not None and row.linked_meeting_date is not None
    ]
    missing_sources = [] if status.extraction_seen else ["extraction"]
    return ContextLinks(
        meeting_id=meeting_id,
        topic_links=topic_links,
        decision_lineage=[],  # Phase 3
        missing_sources=missing_sources,
    )


# --------------------------------------------------------------------------- #
# Deletion — orphan decision threads (see docs/modules/context.md, "Deletion")
# --------------------------------------------------------------------------- #


def sweep_orphan_decision_threads(session: Session) -> int:
    """Delete decision threads that have no versions left, returning the count.

    Four of the five ``ctx_*`` tables cascade from ``meetings.id``.
    ``ctx_decisions`` is anchored on ``team_id`` instead, so a lineage survives
    its origin meeting reaching the retention window — but a thread whose *every*
    version was in a since-deleted meeting is now empty and must go.

    Not yet wired into ``autune_core.deletion``: registering a meeting-deletion
    hook that issues a real ``DELETE`` breaks ``packages/core``'s own unit tests,
    which run before migrations on a clean CI database and iterate every
    registered hook (see ADR 0008 and #87). Until #87 lands this is called
    explicitly — by the integration test, and later by the retention sweep once
    that exists. Global and idempotent.
    """
    # Correlated NOT EXISTS, not `id NOT IN (subquery)`: the latter deletes every
    # thread when the versions table is empty. A globally empty versions table
    # is not a sign of trouble — the retention sweep deleting a team's last
    # version is exactly the case this function exists to clean up after.
    has_version = (
        select(CtxDecisionVersion.id).where(CtxDecisionVersion.thread_id == CtxDecision.id).exists()
    )
    orphans = session.scalars(select(CtxDecision.id).where(~has_version)).all()
    if orphans:
        session.execute(delete(CtxDecision).where(CtxDecision.id.in_(orphans)))
    return len(orphans)


def sweep_dangling_previous_statements(session: Session) -> int:
    """Null ``previous_statement`` on versions whose ``previous_meeting_id`` no
    longer exists, returning the count.

    ``previous_meeting_id`` is deliberately unconstrained (see the model
    docstring) so a retention sweep on that meeting does not cascade into an
    unrelated thread's lineage. ``previous_statement`` is a verbatim copy of
    that meeting's decision text, though, and this module refuses exactly this
    for less: ``ctx_embeddings`` cascades so it can never outlive its meeting,
    and a topic link to a deleted meeting is never used to reconstruct content
    (see "Deletion" in docs/modules/context.md). ``change_type``, ``nli_label``
    and ``confidence`` are untouched — the fact that something changed
    survives; only the deleted meeting's wording goes.

    Not yet wired into ``autune_core.deletion``, same reason and same place as
    ``sweep_orphan_decision_threads`` (ADR 0008, #87). Global and idempotent.
    """
    meeting_exists = (
        select(Meeting.id).where(Meeting.id == CtxDecisionVersion.previous_meeting_id).exists()
    )
    dangling = session.scalars(
        select(CtxDecisionVersion.id).where(
            CtxDecisionVersion.previous_meeting_id.is_not(None),
            CtxDecisionVersion.previous_statement.is_not(None),
            ~meeting_exists,
        )
    ).all()
    if dangling:
        session.execute(
            update(CtxDecisionVersion)
            .where(CtxDecisionVersion.id.in_(dangling))
            .values(previous_statement=None)
        )
    return len(dangling)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _upsert_status(session: Session, meeting_id: str, **fields: object) -> CtxMeetingStatus:
    status = session.get(CtxMeetingStatus, meeting_id)
    if status is None:
        status = CtxMeetingStatus(meeting_id=meeting_id)
        session.add(status)
    for key, value in fields.items():
        setattr(status, key, value)
    session.flush()
    return status


def _as_datetime(value) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
