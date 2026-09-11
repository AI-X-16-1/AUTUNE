"""Business logic for the Meeting Context Engine (``autune_context``).

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module. Cross-module output goes out as the ``ContextLinks``
contract on a Celery task, never as a direct call.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from celery import current_app
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from autune_context.config import get_settings
from autune_context.models import (
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_context.pipeline import get_embedder, get_nli, get_reranker
from autune_context.pipeline.retrieval import HybridRetriever
from autune_context.pipeline.topics import extract_topics
from autune_contracts import ChangeType, ContextLinks, DecisionChange, NliLabel, TopicLink
from autune_core import Meeting, Participant, get_logger, session_scope

if TYPE_CHECKING:
    from autune_context.pipeline.base import Embedder, NliModel
    from autune_contracts import ExtractionResult, TranscriptReady

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
# Decision lineage — off autune.extraction.completed, after B
# --------------------------------------------------------------------------- #

# NLI reads the previous statement as the premise and the current one as the
# hypothesis: entailment means the decision still holds, contradiction means it
# was reversed, neutral means it moved without being undone.
_NLI_TO_CHANGE: dict[NliLabel, ChangeType] = {
    NliLabel.ENTAILMENT: ChangeType.UNCHANGED,
    NliLabel.CONTRADICTION: ChangeType.REVERSED,
    NliLabel.NEUTRAL: ChangeType.MODIFIED,
}


def mark_extraction_seen(meeting_id: str) -> None:
    """Record that B has reported, without building lineage.

    The production path is ``build_decision_lineage``; this stays for callers
    that only need the publish gate flipped (and for tests).
    """
    with session_scope() as session:
        _upsert_status(session, meeting_id, extraction_seen=True)


def build_decision_lineage(result: ExtractionResult) -> None:
    """Thread each of B's decisions into a lineage and classify how it moved.

    B owns *what counts as a decision in this meeting*; D owns *whether it is the
    same decision as one from before*. Each ``result.decisions`` entry is matched
    to an existing ``ctx_decisions`` thread by cosine similarity of its statement
    to the thread's most recent statement, or opens a new ``thr_`` thread
    anchored on the meeting's team.

    Every thread this run touches is then re-chained end to end by
    ``_rethread``: ``previous_*``, ``change_type`` and ``nli_label`` are a
    function of *when meetings happened*, not the order B finished them, so a
    meeting that B processes late — a long February meeting landing after
    January's, a backfill of past recordings — still has to slot into the middle
    of a thread's history.

    Idempotent: every ``ctx_decision_versions`` row for this meeting is replaced
    and its threads are re-chained, then any thread left empty is swept. A re-run
    means the published ``ContextLinks`` should be rebuilt — that is
    ``publish_if_ready``'s job, not this one.
    """
    settings = get_settings()
    embedder = get_embedder()
    nli = get_nli()

    with session_scope() as session:
        meeting = session.get(Meeting, result.meeting_id)
        if meeting is None:
            raise ValueError(f"{result.meeting_id}: meeting row not found")

        # Idempotency: drop this meeting's versions up front. The threads they
        # were in still need re-chaining even if this run matches the decisions
        # elsewhere, so remember them.
        affected: set[str] = set(
            session.scalars(
                select(CtxDecisionVersion.thread_id).where(
                    CtxDecisionVersion.meeting_id == result.meeting_id
                )
            ).all()
        )
        session.execute(
            delete(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == result.meeting_id)
        )
        session.flush()

        heads = _thread_heads(session, meeting.team_id, embedder)
        decisions = list(result.decisions)
        statement_vectors = embedder.embed([d.statement for d in decisions]) if decisions else []
        matched: set[str] = set()
        new_threads = 0

        for decision, vector in zip(decisions, statement_vectors, strict=True):
            head = _match_thread(vector, heads, matched, settings.lineage_match_threshold)
            if head is not None:
                thread_id = head.thread_id
            else:
                thread = CtxDecision(team_id=meeting.team_id, topic_label=decision.statement[:400])
                session.add(thread)
                session.flush()
                thread_id = thread.id
                new_threads += 1
            # A seed row: _rethread fills in every chain-dependent field once the
            # thread's full history is in place.
            session.add(
                CtxDecisionVersion(
                    thread_id=thread_id,
                    source_decision_id=decision.id,
                    meeting_id=meeting.id,
                    current_statement=decision.statement,
                    previous_version_id=None,
                    previous_statement=None,
                    previous_meeting_id=None,
                    change_type=ChangeType.NEW.value,
                    nli_label=None,
                    confidence=_clamp(decision.confidence),
                    key_stakeholders_absent=[],
                    nli_version=nli.model_version,
                )
            )
            matched.add(thread_id)
            affected.add(thread_id)

        session.flush()
        for thread_id in affected:
            _rethread(session, thread_id, nli)

        swept = sweep_orphan_decision_threads(session)
        _upsert_status(session, result.meeting_id, extraction_seen=True, lineage_done=True)
        log.info(
            "context_decision_lineage_done",
            meeting_id=result.meeting_id,
            decisions=len(decisions),
            new_threads=new_threads,
            threads_swept=swept,
        )


class _ThreadHead:
    """A thread's most recent statement, embedded — the target ``_match_thread``
    scores a new decision against."""

    __slots__ = ("thread_id", "vector")

    def __init__(self, thread_id: str, vector: list[float]) -> None:
        self.thread_id = thread_id
        self.vector = vector


def _meeting_time():
    """Order key for a decision version: its meeting's start, falling back to
    when the meeting row was created for the rare meeting with no ``started_at``."""
    return func.coalesce(Meeting.started_at, Meeting.created_at)


def _thread_heads(session: Session, team_id: str, embedder: Embedder) -> list[_ThreadHead]:
    """Every non-empty thread for this team, represented by its latest version
    (by meeting time), embedded.

    One query and one batch embed — threads accumulate per team over the
    retention window, so neither can be per-thread. The returned list is in a
    deterministic order so ``_match_thread``'s tie-break is stable.
    """
    versions = session.scalars(
        select(CtxDecisionVersion)
        .join(CtxDecision, CtxDecision.id == CtxDecisionVersion.thread_id)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(CtxDecision.team_id == team_id)
        .order_by(CtxDecisionVersion.thread_id, _meeting_time(), CtxDecisionVersion.id)
    ).all()
    head_by_thread: dict[str, CtxDecisionVersion] = {}
    for version in versions:
        head_by_thread[version.thread_id] = version  # last row per thread = its head
    if not head_by_thread:
        return []
    ordered = sorted(head_by_thread.items())
    vectors = embedder.embed([version.current_statement for _, version in ordered])
    return [
        _ThreadHead(thread_id, vector)
        for (thread_id, _version), vector in zip(ordered, vectors, strict=True)
    ]


def _match_thread(
    vector: list[float], heads: list[_ThreadHead], used: set[str], threshold: float
) -> _ThreadHead | None:
    """The most similar thread not already matched this run, if it clears the
    threshold. ``>`` on the running best keeps the first-scanned thread on a tie,
    and ``heads`` is ordered, so the result is deterministic."""
    best: _ThreadHead | None = None
    best_sim = -1.0
    for head in heads:
        if head.thread_id in used:
            continue
        sim = _cosine(vector, head.vector)
        if sim > best_sim:
            best_sim, best = sim, head
    return best if best is not None and best_sim >= threshold else None


def _rethread(session: Session, thread_id: str, nli: NliModel) -> None:
    """Re-chain every version of one thread in meeting-chronological order.

    ``previous_*``, ``change_type``, ``nli_label``, ``confidence`` and
    ``key_stakeholders_absent`` all depend on which meeting a version follows, so
    a version that arrived out of order can only be placed by rebuilding the
    chain. NLI is re-run for every adjacent pair (one batched call): it is
    deterministic given the model, and ``nli_version`` is refreshed, so a
    re-chain does not drift. ``confidence`` on the first version keeps B's
    decision confidence; on every later version it is the NLI score of the
    winning label, and it is not recomputed back to B's number if the version
    later becomes the head of its thread (same stance as
    ``sweep_dangling_previous_statements``: what changed survives).
    """
    versions = session.scalars(
        select(CtxDecisionVersion)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(CtxDecisionVersion.thread_id == thread_id)
        .order_by(_meeting_time(), CtxDecisionVersion.id)
    ).all()
    if not versions:
        return

    pairs = [
        (versions[i - 1].current_statement, versions[i].current_statement)
        for i in range(1, len(versions))
    ]
    scored = nli.classify(pairs) if pairs else []

    prior_meeting_ids: list[str] = []
    for index, version in enumerate(versions):
        if index == 0:
            version.previous_version_id = None
            version.previous_statement = None
            version.previous_meeting_id = None
            version.change_type = ChangeType.NEW.value
            version.nli_label = None
            version.key_stakeholders_absent = []
        else:
            prev = versions[index - 1]
            label = NliLabel(scored[index - 1].label)
            version.previous_version_id = prev.id
            version.previous_statement = prev.current_statement
            version.previous_meeting_id = prev.meeting_id
            version.change_type = _NLI_TO_CHANGE[label].value
            version.nli_label = label.value
            version.confidence = _clamp(float(getattr(scored[index - 1], label.value)))
            known = _meeting_user_ids(session, *prior_meeting_ids)
            present = _meeting_user_ids(session, version.meeting_id)
            version.key_stakeholders_absent = sorted(known - present)
        version.nli_version = nli.model_version
        prior_meeting_ids.append(version.meeting_id)
    session.flush()


def _meeting_user_ids(session: Session, *meeting_ids: str) -> set[str]:
    """Resolved user ids of the participants of the given meetings.

    Reads the shared ``participants`` table — never writes it (invariant 4).
    Participants who never resolved to an account (``user_id is None``) drop out.
    """
    if not meeting_ids:
        return set()
    rows = session.scalars(
        select(Participant.user_id).where(
            Participant.meeting_id.in_(set(meeting_ids)),
            Participant.user_id.is_not(None),
        )
    ).all()
    return {user_id for user_id in rows if user_id is not None}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


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
    version_rows = session.scalars(
        select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id == meeting_id)
    ).all()
    decision_lineage = [
        DecisionChange(
            thread_id=row.thread_id,
            source_decision_id=row.source_decision_id,
            current_statement=row.current_statement,
            previous_statement=row.previous_statement,
            previous_meeting_id=row.previous_meeting_id,
            change_type=ChangeType(row.change_type),
            nli_label=NliLabel(row.nli_label) if row.nli_label is not None else None,
            confidence=row.confidence,
            key_stakeholders_absent=list(row.key_stakeholders_absent),
        )
        for row in version_rows
    ]

    missing_sources = [] if status.extraction_seen else ["extraction"]
    return ContextLinks(
        meeting_id=meeting_id,
        topic_links=topic_links,
        decision_lineage=decision_lineage,
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
