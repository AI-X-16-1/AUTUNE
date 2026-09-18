"""Business logic for the Meeting Context Engine (``autune_context``).

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module. Cross-module output goes out as the ``ContextLinks``
contract on a Celery task, never as a direct call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from celery import current_app
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from autune_context.config import get_settings
from autune_context.models import (
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_context.notify import (
    build_decision_drift_channel_notice,
    build_decision_drift_personal_dm,
    build_topic_link_notice,
    build_topic_link_rollup_notice,
)
from autune_context.pipeline import get_embedder, get_nli, get_reranker
from autune_context.pipeline.retrieval import HybridRetriever, visible_meeting_clauses
from autune_context.pipeline.topics import extract_topics
from autune_contracts import ChangeType, ContextLinks, DecisionChange, NliLabel, TopicLink
from autune_core import Meeting, Participant, TeamMember, get_logger, session_scope
from autune_core.errors import ConflictError, NotFoundError
from autune_integrations import SlackApi, assert_personal_delivery

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

    ``tasks.on_extraction_completed`` calls ``build_decision_lineage`` (which
    sets ``extraction_seen`` itself), not this — there is currently no
    production caller. Kept for tests that want the publish gate flipped
    without exercising the matching/NLI machinery.
    """
    with session_scope() as session:
        _upsert_status(session, meeting_id, extraction_seen=True)


def build_decision_lineage(result: ExtractionResult) -> bool:
    """Thread each of B's decisions into a lineage and classify how it moved.

    Returns whether a late-lineage catch-up is owed for this meeting --
    ``ContextLinks`` already published via the B-timeout fallback (that
    publish's ``missing_sources`` would have included ``"extraction"``, since
    B hadn't reported yet) before this lineage arrived. ``tasks
    .on_extraction_completed`` uses this to force a republish (now carrying
    the completed ``decision_lineage``) and a one-off drift-only notify -- see
    ``publish_if_ready(force=...)`` and ``tasks.notify_late_drift``. A caller
    that doesn't need this (tests, ``mark_extraction_seen``) can ignore the
    return value.

    This is *not* simply "was this run late": that would go back to False on
    a Celery redelivery of ``on_extraction_completed`` landing after this
    function's own commit (``extraction_seen`` already flipped to True) but
    before ``on_extraction_completed`` reaches its
    ``publish_if_ready.delay(force=True)`` call -- silently losing the drift
    warning the same way #257 originally did, just with the window narrowed
    instead of closed. Once a run determines it's late, it sets
    ``late_drift_due_at`` in the same transaction as ``extraction_seen``; the
    return value is ``late_drift_due_at is not None`` *after* that write, so
    the "still owed" state persists on the row across a redelivery instead of
    being recomputed fresh each time. ``tasks.notify_late_drift`` clears it
    once it actually claims and sends.

    B owns *what counts as a decision in this meeting*; D owns *whether it is the
    same decision as one from before*. Each of ``result.decisions`` is matched by
    cosine similarity of its statement to the most similar existing thread's most
    recent statement — best pairing first across the whole batch, not
    first-in-``result.decisions``-order, so one weak match earlier in the list
    can't grab a thread out from under a much stronger match later in it. No
    match above ``lineage_match_threshold`` opens a new ``thr_`` thread anchored
    on the meeting's team.

    Matching happens *before* this meeting's own previous versions are deleted.
    B's ``dec_`` id is stable across a rebuild whose sources did not change and
    fresh only when they did (see ``autune_extraction.decisions.decision_id``,
    #171) — which includes every reprocess in module A, since that mints new
    ``utt_`` ids (#194) — but D never matched on that id in the first place:
    whether a decision is the same one as before is D's question, not B's
    (#25), so matching runs by wording regardless of which way B's id moved. A solo
    thread — one with no *other* meeting's version to rediscover it by — has
    nothing but that wording to compare against. Deleting first would erase the
    one piece of evidence (the meeting's own about-to-be-replaced statement)
    that lets a rebuild with materially unchanged wording land back on the same
    thread instead of forking a new one every time B reprocesses the meeting.

    That evidence is added to ``heads`` alongside every thread's team-wide head,
    not used in its place: ``_thread_heads`` only ever returns one version per
    thread (the most recent), so if this meeting sits in the *middle* of a
    thread rather than at its head, comparing only against the head compares a
    rebuilt decision to a *later* meeting's wording instead of its own —
    wording that can easily fall below the adjacent-pair threshold even though
    the meeting's own statement did not change. Appending this meeting's own
    pre-delete versions as extra candidates for the same ``thread_id`` closes
    that gap; ``_assign_decisions_to_threads`` dedupes by ``thread_id`` so a
    thread offered twice (once as team head, once as this meeting's own
    version) is still only assigned once.

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

    Retention is enforced on the *read* side only: ``_thread_heads`` and
    ``_rethread`` both exclude an expired meeting from matching and chaining
    (see "Deletion" in docs/modules/context.md), but this function still writes
    a version for the current meeting even if that meeting is itself already
    past ``expires_at``. In practice extraction finishes hours after a meeting,
    long before its 90-day window, so this only matters for a backfill or a
    badly delayed pipeline run — not worth gating on until it does.

    Concurrency: two meetings for the same team can be matching against the
    same thread heads at once (``cpu_heavy`` is a concurrent queue —
    docs/architecture/async-pipeline.md). Under READ COMMITTED neither would see
    the other's uncommitted insert, and both could chain onto the same "latest"
    version. A ``pg_advisory_xact_lock`` keyed on the team serializes this
    function per team (held for the transaction, never across teams) so that
    can't happen.

    Also runs the other two ``ctx_*`` retention sweeps (``sweep_stale_topic_labels``,
    ``sweep_dangling_previous_statements``) alongside the orphan sweep this
    function has always run — global and idempotent, so exercising them on
    every call closes real gaps ahead of #87 rather than leaving them for tests
    to be the only caller.
    """
    settings = get_settings()
    embedder = get_embedder()
    nli = get_nli()

    with session_scope() as session:
        meeting = session.get(Meeting, result.meeting_id)
        if meeting is None:
            raise ValueError(f"{result.meeting_id}: meeting row not found")

        # Captured before this run's own _upsert_status call below overwrites
        # extraction_seen -- "already published, but not because of us" is
        # exactly the B-timeout-fallback-then-late-lineage case, and it only
        # matches on the one run that flips extraction_seen False -> True, so
        # a later B reprocess of an already-seen meeting correctly reads False
        # here instead of re-triggering the late-catch-up path every time.
        existing_status = session.get(CtxMeetingStatus, result.meeting_id)
        was_late = (
            existing_status is not None
            and existing_status.published_at is not None
            and not existing_status.extraction_seen
        )

        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:team_id))"),
            {"team_id": meeting.team_id},
        )

        # Match before deleting: this meeting's own current versions are still
        # in the DB here, so a rebuild with unchanged (or similar) wording can
        # match back onto its own thread even though B gave it a new dec_ id.
        # Appended, not substituted, for threads whose team-wide head is a
        # *later* meeting — this meeting may sit mid-thread.
        heads = _thread_heads(session, meeting.team_id, embedder)
        own_versions = session.execute(
            select(CtxDecisionVersion.thread_id, CtxDecisionVersion.current_statement)
            .where(CtxDecisionVersion.meeting_id == result.meeting_id)
            .order_by(CtxDecisionVersion.id)
        ).all()
        if own_versions:
            own_vectors = embedder.embed([statement for _, statement in own_versions])
            heads += [
                _ThreadHead(thread_id, vector)
                for (thread_id, _statement), vector in zip(own_versions, own_vectors, strict=True)
            ]
        decisions = list(result.decisions)
        vectors = embedder.embed([d.statement for d in decisions]) if decisions else []
        assignment = _assign_decisions_to_threads(vectors, heads, settings.lineage_match_threshold)

        # Idempotency: now drop this meeting's old versions. The threads they
        # were in still need re-chaining even when this run's decisions land
        # elsewhere (wording changed enough to move threads, or B dropped a
        # decision the last run had), so remember them first.
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

        new_threads = 0
        matched_count = 0
        for index, decision in enumerate(decisions):
            head = assignment.get(index)
            if head is not None:
                thread_id = head.thread_id
                matched_count += 1
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
            affected.add(thread_id)

        session.flush()
        for thread_id in affected:
            _rethread(session, thread_id, nli)

        orphans_swept = sweep_orphan_decision_threads(session)
        labels_swept = sweep_stale_topic_labels(session)
        statements_swept = sweep_dangling_previous_statements(session)
        status = _upsert_status(session, result.meeting_id, extraction_seen=True, lineage_done=True)
        if was_late and status.late_drift_due_at is None:
            # ``ContextLinks`` already went out for this meeting -- the
            # B-timeout fallback published before this (late) lineage
            # arrived, with "extraction" in missing_sources. Recorded on the
            # row (not just this function's return value) so a Celery
            # redelivery of the caller still knows a catch-up is owed even
            # after extraction_seen has already flipped -- see this
            # function's docstring and ``tasks.notify_late_drift``.
            status.late_drift_due_at = datetime.now(tz=UTC)
            log.warning(
                "context_late_lineage_after_publish",
                meeting_id=result.meeting_id,
                published_at=status.published_at.isoformat() if status.published_at else None,
            )
        log.info(
            "context_decision_lineage_done",
            meeting_id=result.meeting_id,
            decisions=len(decisions),
            matched=matched_count,
            new_threads=new_threads,
            threads_swept=orphans_swept,
            labels_swept=labels_swept,
            statements_swept=statements_swept,
        )
        return status.late_drift_due_at is not None


class _ThreadHead:
    """A thread's most recent statement, embedded — the target
    ``_assign_decisions_to_threads`` scores a new decision against."""

    __slots__ = ("thread_id", "vector")

    def __init__(self, thread_id: str, vector: list[float]) -> None:
        self.thread_id = thread_id
        self.vector = vector


def _meeting_time():
    """Order key for a decision version: its meeting's start, falling back to
    when the meeting row was created for the rare meeting with no ``started_at``."""
    return func.coalesce(Meeting.started_at, Meeting.created_at)


def _thread_heads(session: Session, team_id: str, embedder: Embedder) -> list[_ThreadHead]:
    """Every non-empty thread for this team, represented by its latest *visible*
    version (by meeting time), embedded.

    "Visible" is ``visible_meeting_clauses``: a meeting past its retention
    window is excluded here exactly as it is from topic retrieval, even before
    the meeting row itself is deleted — a decision must not match onto, or
    quote, a meeting that has expired.

    One query and one batch embed — threads accumulate per team over the
    retention window, so neither can be per-thread. The returned list is in a
    deterministic order so ``_assign_decisions_to_threads``'s tie-break is
    stable.
    """
    versions = session.scalars(
        select(CtxDecisionVersion)
        .join(CtxDecision, CtxDecision.id == CtxDecisionVersion.thread_id)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(CtxDecision.team_id == team_id, *visible_meeting_clauses(team_id))
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


def _assign_decisions_to_threads(
    vectors: list[list[float]], heads: list[_ThreadHead], threshold: float
) -> dict[int, _ThreadHead]:
    """Best-similarity-first assignment of decisions (by index into ``vectors``)
    to threads, each used at most once.

    Every (decision, thread) pair at or above ``threshold`` is scored up front
    and assignments are made strongest-first — greedy, not the optimal
    maximum-weight matching, but it fixes the concrete failure that motivated
    it: a decision earlier in the list matching a thread only weakly can no
    longer grab it out from under a decision later in the list that matches it
    almost exactly. ``heads`` is deterministically ordered (``_thread_heads``)
    and ties are broken by (decision index, thread index), so the result does
    not depend on dict/set iteration order.

    ``heads`` can list the same ``thread_id`` twice — once as the team-wide
    head ``_thread_heads`` found, once as the reprocessed meeting's own
    about-to-be-replaced version (see ``build_decision_lineage``) — so "a
    thread used at most once" is tracked by ``thread_id``, not by index into
    ``heads``: the two entries are candidates for the *same* slot, not two
    slots.
    """
    candidates = [
        (_cosine(vector, head.vector), d_idx, h_idx)
        for d_idx, vector in enumerate(vectors)
        for h_idx, head in enumerate(heads)
    ]
    candidates = [c for c in candidates if c[0] >= threshold]
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    assigned_decisions: set[int] = set()
    assigned_threads: set[str] = set()
    assignment: dict[int, _ThreadHead] = {}
    for _similarity, d_idx, h_idx in candidates:
        head = heads[h_idx]
        if d_idx in assigned_decisions or head.thread_id in assigned_threads:
            continue
        assigned_decisions.add(d_idx)
        assigned_threads.add(head.thread_id)
        assignment[d_idx] = head
    return assignment


def _rethread(session: Session, thread_id: str, nli: NliModel) -> None:
    """Re-chain every *visible* version of one thread in meeting-chronological
    order, and refresh the thread's ``topic_label`` to match.

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

    A version whose meeting has passed its retention window (``expires_at``) is
    excluded from the chain entirely — it is treated as already gone, the same
    way ``_thread_heads`` treats it for matching, rather than as a live
    predecessor whose text keeps getting copied into ``previous_statement``.

    ``key_stakeholders_absent`` is filtered to *current* ``TeamMember`` rows of
    ``thread.team_id`` (see ``_current_team_member_ids``). Without that filter,
    someone who attended an early meeting in the thread and then left the team
    -- or a guest who was never a member -- would show up as "absent" on every
    later version, and once ``notify_decision_drift`` learns to resolve a
    ``user_id`` to a Slack id, that turns into a drift DM to someone who has no
    reason to get one.
    """
    thread = session.get(CtxDecision, thread_id)
    if thread is None:
        return

    versions = session.scalars(
        select(CtxDecisionVersion)
        .join(CtxDecision, CtxDecision.id == CtxDecisionVersion.thread_id)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(
            CtxDecisionVersion.thread_id == thread_id,
            *visible_meeting_clauses(CtxDecision.team_id),
        )
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
            absent = known - present
            version.key_stakeholders_absent = sorted(
                _current_team_member_ids(session, thread.team_id, absent)
            )
        version.nli_version = nli.model_version
        prior_meeting_ids.append(version.meeting_id)

    thread.topic_label = versions[-1].current_statement[:400]
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


def _current_team_member_ids(session: Session, team_id: str, user_ids: set[str]) -> set[str]:
    """Narrows ``user_ids`` to those still a ``TeamMember`` of ``team_id``.

    Reads the shared ``team_members`` table — never writes it (invariant 4).
    """
    if not user_ids:
        return set()
    rows = session.scalars(
        select(TeamMember.user_id).where(
            TeamMember.team_id == team_id, TeamMember.user_id.in_(user_ids)
        )
    ).all()
    return set(rows)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


# --------------------------------------------------------------------------- #
# Publishing — once both halves are in, or B has timed out
# --------------------------------------------------------------------------- #


def publish_if_ready(meeting_id: str, *, force: bool = False) -> bool:
    """Publish ``ContextLinks`` when topic linking is done and either lineage is
    done, B has reported, or the deadline has passed. Returns whether it published.

    A failure in B must never cost the user their topic links.

    ``on_transcript_ready`` enqueues this twice (an immediate check and a
    B-timeout fallback) and Celery can retry it again on top of that, so two
    workers can reach here for the same meeting at once. ``with_for_update``
    makes the ``published_at IS NULL`` read and the publish it guards atomic:
    the second worker blocks on the row lock until the first commits, then
    sees ``published_at`` already set and returns ``False`` instead of
    publishing and notifying a second time.

    ``force=True`` is the one deliberate exception to that guard: it is set
    only by ``tasks.on_extraction_completed`` when
    ``build_decision_lineage`` reports its lineage arrived *after* a meeting
    already published via the B-timeout fallback (``missing_sources =
    ["extraction"]``). That republish carries the now-complete
    ``decision_lineage`` to E -- which already knows how to accept a second
    completion for a meeting it aggregated once (see
    ``autune_intelligence.tasks``' "a completion after the first pass reopens
    and re-enqueues") -- and does not re-touch ``published_at`` or re-check
    the deadline/lineage gate, since we already know why we're here.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None or not status.topic_linking_done:
            return False
        if status.published_at is not None and not force:
            return False

        if not force:
            timed_out = (
                status.deadline_at is not None and datetime.now(tz=UTC) >= status.deadline_at
            )
            if not (status.lineage_done or status.extraction_seen or timed_out):
                return False

        links = _build_context_links(session, meeting_id, status)
        current_app.send_task(_CONTEXT_CONSUMER_TASK, args=[links.model_dump(mode="json")])
        first_publish = status.published_at is None
        if first_publish:
            status.published_at = datetime.now(tz=UTC)
        log.info(
            "context_republished" if force else "context_published",
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
# Slack notices — fired once, after ``publish_if_ready`` actually publishes
#
# Split into "collect" (reads ``ctx_*`` rows, needs a session) and "send"
# (pure Slack I/O, no session) so a caller can close its session — and free
# the pooled connection — before making any Slack HTTP call. See
# ``tasks.notify_context_events``, which does exactly that; the combined
# ``notify_topic_links``/``notify_decision_drift`` below stay for callers (and
# tests) that don't need the split.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TopicLinkNotice:
    topic_label: str
    linked_meeting_date: date


@dataclass(frozen=True)
class DriftNotice:
    thread_label: str
    statement_preview: str
    change_type: ChangeType
    absent_user_ids: tuple[str, ...]
    meeting_date: date | None
    """The *changing* meeting's own date (``Meeting.started_at``), not the
    thread's -- see ``notify.build_decision_drift_channel_notice``."""


def _deliver_personal(
    slack: SlackApi, recipient_user_id: str, fallback: str, blocks: list[dict]
) -> None:
    """Send a DM that describes exactly one person, to that person only.

    Same shape as ``autune_intelligence.service._deliver_personal``: a single
    id serves as both the guard's subject and the recipient, so the two
    cannot drift apart at a call site.
    """
    assert_personal_delivery(
        subject_id=recipient_user_id, recipient_id=recipient_user_id, is_direct=True
    )
    slack.send_dm(recipient_user_id, fallback, blocks)


def collect_topic_link_notices(session: Session, meeting_id: str) -> list[TopicLinkNotice]:
    """This meeting's *asserted* topic links, as notice-ready values.

    Only ``asserted`` links, not ``pending`` ones the user later ``confirmed``
    -- the notice is for a link the system was confident enough to assert by
    itself, not for one the user just confirmed. A link with no
    ``linked_meeting_date`` cannot happen for an ``asserted`` row (see
    ``_link_topic``, which skips writing one), but the guard is kept here too
    since this function's contract is "safe to call on whatever is in the
    table," not "safe to call right after ``_link_topic``."
    """
    rows = session.scalars(
        select(CtxTopicLink).where(
            CtxTopicLink.meeting_id == meeting_id, CtxTopicLink.status == "asserted"
        )
    ).all()
    return [
        TopicLinkNotice(
            topic_label=row.topic_label, linked_meeting_date=row.linked_meeting_date.date()
        )
        for row in rows
        if row.linked_meeting_date is not None
    ]


def collect_drift_notices(session: Session, meeting_id: str) -> list[DriftNotice]:
    """This meeting's decision-drift events, as notice-ready values.

    Only ``MODIFIED`` and ``REVERSED`` are a drift: ``ChangeType.NEW`` never
    has an absent list (see ``_rethread``), and ``UNCHANGED`` means the NLI
    check found the statement re-affirmed, not changed, so notifying on it
    would tell an absent stakeholder a decision moved when it did not.
    """
    rows = session.scalars(
        select(CtxDecisionVersion).where(
            CtxDecisionVersion.meeting_id == meeting_id,
            CtxDecisionVersion.change_type.in_(
                (ChangeType.MODIFIED.value, ChangeType.REVERSED.value)
            ),
        )
    ).all()
    notices = []
    for version in rows:
        absent = version.key_stakeholders_absent
        if not absent:
            continue
        thread = session.get(CtxDecision, version.thread_id)
        thread_label = thread.topic_label if thread is not None else version.current_statement[:400]
        meeting = session.get(Meeting, version.meeting_id)
        # ``current_statement`` is a ``Text`` column with no length limit, and
        # it is quoted in both the channel notice and the DM. An unusually
        # long statement from B can push the outbound payload past
        # ``assert_within_size``'s cap, which raises ``PrivacyViolationError``
        # with no handler around the send loop -- and since the caller is
        # ``acks_late``, Celery just redelivers the same failing meeting
        # forever, so its drift warning never goes out. The same 400-char
        # preview used for ``thread_label`` keeps this well under the cap.
        notices.append(
            DriftNotice(
                thread_label=thread_label,
                statement_preview=version.current_statement[:400],
                change_type=ChangeType(version.change_type),
                absent_user_ids=tuple(absent),
                meeting_date=meeting.started_at.date() if meeting and meeting.started_at else None,
            )
        )
    return notices


def send_topic_link_notices(slack: SlackApi, channel: str, notices: list[TopicLinkNotice]) -> int:
    """Post the channel notices for already-collected topic links.

    Capped at ``ContextSettings.max_topic_link_notices`` individual messages;
    anything past the cap collapses into one rollup notice instead of posting
    one message per topic, so a meeting with many linked topics does not flood
    the channel. Returns the total notice count (shown plus rolled up).
    """
    cap = get_settings().max_topic_link_notices
    shown, overflow = notices[:cap], notices[cap:]
    for notice in shown:
        fallback, blocks = build_topic_link_notice(
            topic_label=notice.topic_label, linked_meeting_date=notice.linked_meeting_date
        )
        slack.post_message(channel, fallback, blocks)
    if overflow:
        fallback, blocks = build_topic_link_rollup_notice(count=len(overflow))
        slack.post_message(channel, fallback, blocks)
    log.info(
        "context_topic_link_notice_sent",
        count=len(notices),
        shown=len(shown),
        rolled_up=len(overflow),
    )
    return len(notices)


def send_decision_drift_notices(slack: SlackApi, channel: str, notices: list[DriftNotice]) -> int:
    """Post the decision-drift warnings for already-collected drift events.

    One channel notice per event (never names the absentees -- see
    ``notify.py``), plus one DM per absent stakeholder (does not need to name
    anyone -- they are the recipient). Returns the count of drift events sent
    (not the count of DMs sent).
    """
    for notice in notices:
        channel_fallback, channel_blocks = build_decision_drift_channel_notice(
            thread_label=notice.thread_label,
            current_statement=notice.statement_preview,
            change_type=notice.change_type,
            absent_count=len(notice.absent_user_ids),
            meeting_date=notice.meeting_date,
        )
        slack.post_message(channel, channel_fallback, channel_blocks)

        dm_fallback, dm_blocks = build_decision_drift_personal_dm(
            thread_label=notice.thread_label,
            current_statement=notice.statement_preview,
            change_type=notice.change_type,
            meeting_date=notice.meeting_date,
        )
        for user_id in notice.absent_user_ids:
            _deliver_personal(slack, user_id, dm_fallback, dm_blocks)

    log.info("context_decision_drift_notice_sent", count=len(notices))
    return len(notices)


def notify_topic_links(session: Session, slack: SlackApi, channel: str, meeting_id: str) -> int:
    """Collect and send this meeting's topic-link notices in one call.

    For callers that don't need to release their session before the Slack
    calls below -- ``tasks.notify_context_events`` does, and calls
    ``collect_topic_link_notices``/``send_topic_link_notices`` separately
    instead.
    """
    return send_topic_link_notices(slack, channel, collect_topic_link_notices(session, meeting_id))


def notify_decision_drift(session: Session, slack: SlackApi, channel: str, meeting_id: str) -> int:
    """Collect and send this meeting's decision-drift warnings in one call.

    For callers that don't need to release their session before the Slack
    calls below -- ``tasks.notify_context_events`` does, and calls
    ``collect_drift_notices``/``send_decision_drift_notices`` separately
    instead.
    """
    return send_decision_drift_notices(slack, channel, collect_drift_notices(session, meeting_id))


# --------------------------------------------------------------------------- #
# Reads — served by router.py
# --------------------------------------------------------------------------- #


def get_topic_links(
    session: Session, meeting_id: str
) -> tuple[list[CtxTopicLink], list[CtxTopicLink]]:
    """This meeting's topic links, split into (asserted, pending).

    ``asserted`` reuses ``_PUBLISHABLE`` — it covers both ``asserted`` (above
    threshold) and ``confirmed`` (a ``pending`` link the user accepted), the
    same "settled" definition ``_build_context_links`` publishes to E.
    ``rejected`` links are dismissed and appear in neither list.

    A meeting past its own retention window is treated as gone the moment it
    expires (docs/modules/context.md, "Deletion"), not only once it is actually
    deleted, so its links are filtered out here too — the same join and
    ``visible_meeting_clauses`` every other lineage read uses, applied to
    ``meeting_id`` itself rather than to a linked meeting. An expired meeting
    reads the same as an unknown one: both return two empty lists.
    """
    links = session.scalars(
        select(CtxTopicLink)
        .join(Meeting, Meeting.id == CtxTopicLink.meeting_id)
        .where(CtxTopicLink.meeting_id == meeting_id, *visible_meeting_clauses(Meeting.team_id))
        .order_by(CtxTopicLink.confidence.desc())
    ).all()
    asserted = [link for link in links if link.status in _PUBLISHABLE]
    pending = [link for link in links if link.status == "pending"]
    return asserted, pending


def confirm_topic_link(session: Session, link_id: int, new_status: str) -> CtxTopicLink:
    """Record a user's decision on a ``pending`` link.

    Only a ``pending`` link accepts a decision through this route — one already
    settled, whether by an earlier confirm or because it started ``asserted``,
    does not get a second one. A link whose meeting has since expired is
    treated as not found, the same "gone" rule ``get_topic_links`` applies —
    there is nothing left to confirm a link for.
    """
    link = session.get(CtxTopicLink, link_id)
    if link is None or not _meeting_is_visible(session, link.meeting_id):
        raise NotFoundError("topic link", str(link_id))
    if link.status != "pending":
        raise ConflictError(f"topic link {link_id} is not pending", status=link.status)
    link.status = new_status
    session.flush()
    return link


def _meeting_is_visible(session: Session, meeting_id: str) -> bool:
    return (
        session.execute(
            select(Meeting.id).where(
                Meeting.id == meeting_id, *visible_meeting_clauses(Meeting.team_id)
            )
        ).first()
        is not None
    )


def get_decision_lineage(
    session: Session, thread_id: str
) -> tuple[CtxDecision, list[CtxDecisionVersion], set[str]]:
    """A thread's visible timeline (oldest first), plus which of those
    versions' ``previous_meeting_id`` values are themselves still visible.

    Ordered by meeting time (``_meeting_time``) — the same key ``_rethread``
    chains by — rather than by walking ``previous_version_id`` from the root.
    A walk from the root breaks the moment the chain's *first* version ages
    past the retention window without a later meeting having touched this
    thread since: nothing re-chains it until then (see docs/modules/context.md,
    "Deletion" — accepted until #87's hook lands), so ``previous_version_id``
    on the surviving versions still points at a now-invisible row, and a walk
    that requires a visible root to start from would find none and lose the
    rest of the thread along with it. Ordering by meeting time instead only
    ever drops the row that actually expired.

    Dropping an expired version's own row is not enough on its own: the next
    version's ``previous_statement``/``previous_meeting_id`` still quote that
    meeting's wording verbatim, because ``sweep_dangling_previous_statements``
    only blanks them once the meeting is actually *deleted*, not merely
    expired. The same "gone the moment it expires" promise has to hold for a
    quoted predecessor too, so this returns the set of ``previous_meeting_id``
    values that are still visible — the caller (``router.py``) blanks those two
    fields on any returned version whose predecessor isn't in it. Done at the
    schema layer rather than by mutating these rows here: ``get_session``
    commits every request's session on success, so writing ``None`` onto an
    ORM object inside a *read* endpoint would silently persist it.

    Raises ``NotFoundError`` if the thread exists but every version is
    currently invisible — a fully-expired thread is "gone" the same way its
    content is, rather than surfacing a stale ``topic_label`` over an empty
    timeline.
    """
    thread = session.get(CtxDecision, thread_id)
    if thread is None:
        raise NotFoundError("decision thread", thread_id)

    versions = list(
        session.scalars(
            select(CtxDecisionVersion)
            .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
            .where(
                CtxDecisionVersion.thread_id == thread_id,
                *visible_meeting_clauses(thread.team_id),
            )
            .order_by(_meeting_time(), CtxDecisionVersion.id)
        )
    )
    if not versions:
        raise NotFoundError("decision thread", thread_id)

    prior_meeting_ids = {v.previous_meeting_id for v in versions if v.previous_meeting_id}
    visible_prior_meeting_ids = (
        set(
            session.scalars(
                select(Meeting.id).where(
                    Meeting.id.in_(prior_meeting_ids), *visible_meeting_clauses(thread.team_id)
                )
            )
        )
        if prior_meeting_ids
        else set()
    )
    return thread, versions, visible_prior_meeting_ids


def list_decisions(
    session: Session,
    team_id: str,
    *,
    topic: str | None = None,
    change_type: str | None = None,
) -> list[tuple[CtxDecision, CtxDecisionVersion]]:
    """Every thread's current head for a team, most recently touched first.

    A thread's head is its latest *visible* version by meeting time — the same
    definition ``_thread_heads`` matches new decisions against, so what this
    lists is exactly what a new decision would compare against. Filtering
    ``change_type`` reads as "this thread's latest change was X"; a caller
    after the full drift history opens the thread with ``get_decision_lineage``.

    ``topic`` matches the *head version's own* ``current_statement``, as a
    literal case-insensitive substring — not ``ctx_decisions.topic_label``.
    That column is a cache ``_rethread`` sets from whichever version is head
    *at write time*; nothing refreshes it on a mere expiry
    (``sweep_stale_topic_labels`` only runs when the next lineage build
    touches the thread), so it can still quote a version that has since aged
    out of visibility even though an earlier, still-visible version is now the
    correct head (lsh2217's #185 review). Matching is done in Python after
    head selection, not pushed into SQL, for the same reason — the head has to
    be picked first. The router derives the response's own display label the
    same way (``version.current_statement``), never from the cached column.
    """
    rows = session.execute(
        select(CtxDecision, CtxDecisionVersion)
        .join(CtxDecisionVersion, CtxDecisionVersion.thread_id == CtxDecision.id)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(CtxDecision.team_id == team_id, *visible_meeting_clauses(team_id))
        .order_by(CtxDecision.id, _meeting_time(), CtxDecisionVersion.id)
    ).all()

    heads: dict[str, tuple[CtxDecision, CtxDecisionVersion]] = {}
    for thread, version in rows:
        heads[thread.id] = (thread, version)  # last row per thread = its head
    result = list(heads.values())
    if change_type is not None:
        result = [pair for pair in result if pair[1].change_type == change_type]
    if topic is not None:
        needle = topic.casefold()
        result = [pair for pair in result if needle in pair[1].current_statement.casefold()]
    result.sort(key=lambda pair: pair[1].updated_at, reverse=True)
    return result


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


def sweep_stale_topic_labels(session: Session) -> int:
    """Refresh ``ctx_decisions.topic_label`` to each thread's current visible
    head — blanking it if the thread has none — returning how many changed.

    ``topic_label`` is set from a decision's own (masked) statement when its
    thread opens, and ``_rethread`` keeps it in sync whenever the thread is
    next touched by a new meeting — but a thread nobody touches again after the
    meeting that set the label is deleted keeps quoting that meeting's content
    forever otherwise. ``ctx_decisions`` is anchored on ``team_id`` precisely so
    a lineage outlives its origin meeting (see the model docstring); this sweep
    is the other half of that promise for the one column ``_rethread`` cannot
    reach on its own.

    A thread every one of whose versions has expired (but not yet been deleted)
    has no *visible* head either — ``_rethread`` bails out on it the same way,
    since there is nothing left to chain — so it is blanked (``""``, the column
    is not nullable) rather than left quoting expired content until the
    retention sweep eventually deletes the rows and
    ``sweep_orphan_decision_threads`` removes the thread outright.

    Not yet wired into ``autune_core.deletion``, same reason and same place as
    ``sweep_orphan_decision_threads`` (ADR 0008, #87). Global and idempotent.
    """
    versions = session.scalars(
        select(CtxDecisionVersion)
        .join(CtxDecision, CtxDecision.id == CtxDecisionVersion.thread_id)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(*visible_meeting_clauses(CtxDecision.team_id))
        .order_by(CtxDecisionVersion.thread_id, _meeting_time(), CtxDecisionVersion.id)
    ).all()
    latest_statement: dict[str, str] = {}
    for version in versions:
        latest_statement[version.thread_id] = version.current_statement  # last row wins

    every_thread_with_versions = set(
        session.scalars(select(CtxDecisionVersion.thread_id).distinct()).all()
    )
    fully_expired_thread_ids = every_thread_with_versions - set(latest_statement)
    target_ids = set(latest_statement) | fully_expired_thread_ids

    changed = 0
    if target_ids:
        threads = session.scalars(select(CtxDecision).where(CtxDecision.id.in_(target_ids))).all()
        for thread in threads:
            label = latest_statement.get(thread.id, "")[:400]
            if thread.topic_label != label:
                thread.topic_label = label
                changed += 1
    return changed


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
