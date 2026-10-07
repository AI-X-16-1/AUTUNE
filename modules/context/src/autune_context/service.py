"""Business logic for the Meeting Context Engine (``autune_context``).

Owner: 문민재. See docs/modules/context.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ctx_*`` tables.
Never imports another module. Cross-module output goes out as the ``ContextLinks``
contract on a Celery task, never as a direct call.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from celery import current_app
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from autune_context.config import get_settings
from autune_context.constants import SPEECH_DELETED_TEXT
from autune_context.dates import meeting_day
from autune_context.models import (
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTeamAgenda,
    CtxTopicLink,
)
from autune_context.notify import (
    build_decision_drift_channel_notice,
    build_decision_drift_personal_dm,
    build_topic_link_notice,
    build_topic_link_rollup_notice,
)
from autune_context.pipeline import get_embedder, get_llm_judge, get_nli, get_reranker
from autune_context.pipeline.change import classify_change, strip_keep_words
from autune_context.pipeline.retrieval import HybridRetriever, visible_meeting_clauses
from autune_context.pipeline.topics import extract_topics
from autune_contracts import ChangeType, ContextLinks, DecisionChange, NliLabel, TopicLink
from autune_contracts import Utterance as UtteranceContract
from autune_core import (
    Meeting,
    Participant,
    TeamMember,
    User,
    Utterance,
    get_logger,
    session_scope,
)
from autune_core.deletion import on_speech_deleted
from autune_core.errors import ConflictError, NotFoundError
from autune_integrations import PermanentIntegrationError, SlackApi, assert_personal_delivery

if TYPE_CHECKING:
    from autune_context.pipeline.base import Embedder, NliModel
    from autune_context.pipeline.llm_judge import LlmJudge
    from autune_contracts import ExtractionResult, TranscriptReady

log = get_logger(__name__)

# The two answers a user gives a ``pending`` link (``POST /links/{id}/confirm``).
_HUMAN_LINK_STATUSES = ("confirmed", "rejected")

# D publishes to E's consumer task directly — the async pipeline has no broker
# abstraction (docs/architecture/async-pipeline.md, "Payloads"). A string, not an
# import, so the module boundary holds.
_CONTEXT_CONSUMER_TASK = "autune.intelligence.on_context_completed"

_PUBLISHABLE = ("asserted", "confirmed")


class LineageOutcome(StrEnum):
    """What a ``build_decision_lineage`` run leaves owed to ``ContextLinks``.

    Two different questions used to share one boolean: "is a drift warning
    owed" and "does E need this lineage". A rebuild of a meeting that already
    published answers the first no and the second yes, and with one flag it
    got neither -- E kept the old ``dec_`` ids.
    """

    FIRST = "first"
    """Nothing published yet: the ordinary ``publish_if_ready`` gate decides,
    and its publish sends the usual notices."""
    LATE = "late"
    """Published by the B-timeout fallback before this lineage existed:
    republish, and send the drift warning that publish could not carry."""
    REBUILT = "rebuilt"
    """Published before, and this run rebuilt the lineage (B reran -- a module
    A reprocess, or a redelivery): republish so E has the current rows, and
    notify nobody -- everything this meeting had to say already went out."""


# --------------------------------------------------------------------------- #
# Topic linking — off autune.transcript.ready, in parallel with B and C
# --------------------------------------------------------------------------- #


def run_topic_linking(transcript: TranscriptReady) -> bool:
    """Extract this meeting's topics, link them to past meetings, persist.

    Idempotent: a re-run replaces every ``ctx_*`` row this task owns for the
    meeting, except that a link a user confirmed or rejected keeps that answer
    when the same topic label links to the same meeting again. Does not
    publish — that is ``publish_if_ready``.

    Returns whether ``ContextLinks`` had already been published for this
    meeting, in which case the links just rebuilt are not what E holds and
    ``tasks.on_transcript_ready`` republishes them. A re-run is a module A
    reprocess or a redelivery; the ordinary publish would refuse both on its
    ``published_at`` guard.

    Only a consenting speaker's utterances are analysed (privacy.md section 5,
    ``consented_utterance_ids``): the rest never reach the embedder, a topic
    label, the BM25 corpus, the re-ranker, or ``ctx_embeddings.utterance_ids``.
    A meeting where nobody consented gets no topics and no links -- and a
    re-run after a speaker withdraws drops what their speech produced.

    ``settings.engine_mode`` picks who decides whether a candidate is the same
    topic: the re-ranker and two thresholds (``classic``), an external LLM
    (``llm``, ``_link_topic_llm``), or the first with the second checking every
    link it is about to assert (``hybrid``, ``_link_topic``'s ``verifier``).
    Segmentation and retrieval are the same in all three, so an ``llm`` run needs
    no re-ranker endpoint. A user's answer to a link is theirs in every mode.
    """
    return _link_topics(transcript.meeting_id, list(transcript.utterances))


def rederive_topics(meeting_id: str) -> bool | None:
    """Re-run topic linking for an analysed meeting from its stored utterances.

    For when the consent behind a meeting's topics has changed after D
    analysed it -- which ``run_topic_linking`` alone never sees, because no
    event arrives:

    - **Topics derived before #439.** Every utterance was analysed then, so
      ``ctx_embeddings.ref_label``, its vector and ``ctx_topic_links
      .topic_label`` can carry a non-consenting speaker's speech. privacy.md
      section 5 says excluded speech is not stored, not merely hidden, and the
      read-time filter on the re-ranker's passages cannot reach a label.
    - **Consent attested after analysis.** ``autune_audio.service
      .attest_consent`` can land once the meeting is analysed and tells no
      consumer, so D kept no topics for speech that is now allowed.

    Reads the utterances module A stored (already PII-masked before their first
    write) and runs them through the same path as the event, so the consent
    filter and everything after it are one code path, not two.

    Returns ``None`` -- and touches nothing -- for a meeting D has not analysed
    (``topic_linking_done`` unset: the event path has not run, and this must
    not stand in for it), one past its retention window, or one whose row does
    not carry module A's privacy guarantees (the checks ``TranscriptReady
    .require_privacy_guarantees`` makes on the event). Otherwise returns what
    ``run_topic_linking`` does: whether ``ContextLinks`` had already been
    published, so the caller can route the same way ``on_transcript_ready``
    does.

    **One meeting is not the whole cleanup.** This replaces the links *out of*
    ``meeting_id``. A later meeting's link *into* it (``linked_meeting_id ==
    meeting_id``) stays, and was scored against the topics this run just
    dropped, so after a withdrawal it still says "discussed in that meeting"
    for speech that is gone. Re-derive the team's later meetings too --
    ``rederivable_meeting_ids`` finds them, oldest first -- a caller of this
    one alone has to.
    """
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        status = session.get(CtxMeetingStatus, meeting_id)
        refusal = (
            "no_such_meeting"
            if meeting is None
            else "not_analysed"
            if status is None or not status.topic_linking_done
            else "expired"
            if meeting.expires_at is not None and meeting.expires_at <= now
            else "no_privacy_guarantees"
            if not (meeting.pii_masked and meeting.original_audio_deleted)
            else None
        )
        if refusal is not None:
            log.info("context_rederive_skipped", meeting_id=meeting_id, reason=refusal)
            return None
        rows = session.execute(
            select(
                Utterance.id,
                Utterance.speaker_label,
                Utterance.participant_id,
                Utterance.start_sec,
                Utterance.end_sec,
                Utterance.text,
                Utterance.confidence,
            )
            .where(Utterance.meeting_id == meeting_id)
            .order_by(Utterance.start_sec, Utterance.id)
        ).all()
    # ``model_construct``: these rows were validated as a contract when module
    # A published them; one bad legacy row must not stop a backfill. It does
    # not complain about a field left out, so every column that has a contract
    # counterpart is passed -- the event path and this one must hand
    # ``_link_topics`` the same thing. ``role`` has no column and stays unset.
    utterances = [
        UtteranceContract.model_construct(
            id=uid,
            speaker=speaker,
            speaker_id=participant_id,
            start=start,
            end=end,
            text=text_,
            confidence=confidence,
        )
        for uid, speaker, participant_id, start, end, text_, confidence in rows
    ]
    return _link_topics(meeting_id, utterances)


def rederivable_meeting_ids(session: Session, *, team_id: str | None = None) -> list[str]:
    """The meetings a backfill has to re-derive, oldest first.

    Not every analysed meeting: re-deriving replaces a meeting's links, and a
    meeting whose result cannot change has nothing to gain from it. Two kinds
    are in:

    - **Meetings that may hold excluded speech.** Some utterance's speaker did
      not consent, and a topic row was cut from it (``ctx_embeddings
      .utterance_ids`` names it) or cannot say what it was cut from (``None``,
      a row from before #397). Where the excluded speech was never analysed, or
      the meeting has none, the result would be the same, so it is left alone.
    - **Meetings that link to those.** A link into a re-derived meeting was
      scored against topics that no longer exist. Their own topics do not
      change, only what they are scored against, so one step is enough -- no
      further chain.

    Only meetings ``rederive_topics`` would act on: analysed, unexpired, and
    carrying module A's privacy flags. Consent attested *after* analysis is not
    found here -- nothing stored says which meetings that happened to, and
    re-deriving one produces topics rather than cleaning any up -- so that case
    is ``rederive_topics`` on the meeting.

    Oldest first because each meeting's links are scored against the topics of
    the meetings before it: re-deriving in time order means every meeting is
    linked against predecessors that are already re-derived.
    """
    now = datetime.now(tz=UTC)
    stmt = (
        select(Meeting.id)
        .join(CtxMeetingStatus, CtxMeetingStatus.meeting_id == Meeting.id)
        .where(
            CtxMeetingStatus.topic_linking_done.is_(True),
            or_(Meeting.expires_at.is_(None), Meeting.expires_at > now),
            Meeting.pii_masked.is_(True),
            Meeting.original_audio_deleted.is_(True),
        )
    )
    if team_id is not None:
        stmt = stmt.where(Meeting.team_id == team_id)
    eligible = set(session.scalars(stmt))

    exposed = {mid for mid in eligible if _holds_excluded_speech(session, mid)}
    if not exposed:
        return []
    linking_in = set(
        session.scalars(
            select(CtxTopicLink.meeting_id).where(CtxTopicLink.linked_meeting_id.in_(exposed))
        )
    )
    chosen = exposed | (linking_in & eligible)
    return list(
        session.scalars(
            select(Meeting.id).where(Meeting.id.in_(chosen)).order_by(_meeting_time(), Meeting.id)
        )
    )


def _holds_excluded_speech(session: Session, meeting_id: str) -> bool:
    """Whether a topic of this meeting may have been cut from speech that is
    excluded from analysis -- see ``rederivable_meeting_ids``."""
    excluded = set(
        session.scalars(select(Utterance.id).where(Utterance.meeting_id == meeting_id))
    ) - consented_utterance_ids(session, meeting_id)
    if not excluded:
        return False
    provenance = session.scalars(
        select(CtxEmbedding.utterance_ids).where(
            CtxEmbedding.meeting_id == meeting_id, CtxEmbedding.kind == "topic"
        )
    )
    return any(not ids or excluded.intersection(ids) for ids in provenance)


def human_verdict_count(session: Session, meeting_ids: list[str]) -> int:
    """How many links on these meetings a user settled (``confirmed`` or
    ``rejected``) -- what re-deriving puts at risk. A verdict survives when the
    same topic label still links to the same meeting (``_link_topics``) and is
    gone otherwise."""
    return (
        session.scalar(
            select(func.count())
            .select_from(CtxTopicLink)
            .where(
                CtxTopicLink.meeting_id.in_(meeting_ids),
                CtxTopicLink.status.in_(_HUMAN_LINK_STATUSES),
            )
        )
        or 0
    )


def _link_topics(meeting_id: str, utterances: list[UtteranceContract]) -> bool:
    """``run_topic_linking``'s work, for utterances from the event or the
    database alike -- see its docstring."""
    settings = get_settings()
    embedder = get_embedder()
    mode = settings.engine_mode
    judge = get_llm_judge() if mode in ("llm", "hybrid") else None
    reranker = get_reranker() if mode != "llm" else None

    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            raise ValueError(f"{meeting_id}: meeting row not found")

        consented = consented_utterance_ids(session, meeting_id)
        analysed = [u for u in utterances if u.id in consented]
        topics = extract_topics(
            analysed,
            embedder,
            window=settings.topic_window,
            min_segment=settings.topic_min_segment,
            depth_threshold=settings.topic_depth_threshold,
        )
        log.info(
            "context_topics_extracted",
            meeting_id=meeting_id,
            topics=len(topics),
            utterances=len(analysed),
            excluded=len(utterances) - len(analysed),
        )

        session.execute(
            delete(CtxEmbedding).where(
                CtxEmbedding.meeting_id == meeting_id,
                CtxEmbedding.kind == "topic",
            )
        )
        # A user's answer to a ``pending`` link is theirs, not this run's: read
        # them before the delete, and ``_link_topic`` puts each back on a link
        # to the same meeting under the same label.
        verdicts = {
            (link.linked_meeting_id, link.topic_label): link.status
            for link in session.scalars(
                select(CtxTopicLink).where(
                    CtxTopicLink.meeting_id == meeting_id,
                    CtxTopicLink.status.in_(_HUMAN_LINK_STATUSES),
                )
            )
        }
        session.execute(delete(CtxTopicLink).where(CtxTopicLink.meeting_id == meeting_id))
        session.flush()

        for topic in topics:
            session.add(
                CtxEmbedding(
                    meeting_id=meeting_id,
                    kind="topic",
                    ref_label=topic.label,
                    utterance_ids=topic.utterance_ids,
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
                exclude_meeting_id=meeting_id,
            )
            if mode == "llm":
                assert judge is not None
                links_written += _link_topic_llm(
                    session, meeting_id, topic, candidates, judge, settings, embedder, verdicts
                )
            else:
                links_written += _link_topic(
                    session,
                    meeting_id,
                    topic,
                    candidates,
                    reranker,
                    settings,
                    embedder,
                    verdicts,
                    verifier=judge,
                )

        # Under the row lock publish_if_ready takes: either a publish committed
        # first and this run republishes, or it waits and publishes these links.
        prior = session.get(
            CtxMeetingStatus, meeting_id, with_for_update=True, populate_existing=True
        )
        already_published = prior is not None and prior.published_at is not None
        _upsert_status(
            session,
            meeting_id,
            topic_linking_done=True,
            deadline_at=datetime.now(tz=UTC) + timedelta(seconds=settings.publish_timeout_s),
        )
        log.info(
            "context_topic_linking_done",
            meeting_id=meeting_id,
            links=links_written,
            already_published=already_published,
        )
        return already_published


def consented_utterance_ids(session: Session, meeting_id: str) -> set[str]:
    """This meeting's utterances whose speaker consented to analysis.

    The line modules B and C already draw (``autune_extraction.service
    .consented_utterance_ids``, #163): ``Participant.consented`` is False for a
    speaker whose speech is excluded from analysis, and an utterance with no
    participant behind it is out as well -- whether its speaker consented is
    unknown, and unknown is not yes. ``TranscriptReady`` carries every
    utterance; filtering is the consumer's job.

    Reads the shared ``utterances`` and ``participants`` tables -- never writes
    them (invariant 4).
    """
    return set(
        session.scalars(
            select(Utterance.id)
            .join(Participant, Participant.id == Utterance.participant_id)
            .where(Utterance.meeting_id == meeting_id, Participant.consented.is_(True))
        ).all()
    )


def _link_topic(
    session: Session,
    meeting_id: str,
    topic,
    candidates: list,
    reranker,
    settings,
    embedder,
    verdicts: dict[tuple[str | None, str], str],
    verifier: LlmJudge | None = None,
) -> int:
    """``classic`` topic linking; with a ``verifier`` (``hybrid`` mode), also a
    check on every link about to be asserted.

    The verifier is asked only about candidates the thresholds would *assert*: a
    link classic already leaves ``pending`` needs no second opinion, and this
    keeps the calls to a handful per meeting. Its answer replaces classic's
    verdict on that link (and ``confidence``, the deciding signal, with it):
    at ``llm_link_threshold`` or above it stays ``asserted``; under it but at
    ``llm_pending_floor`` or above it is demoted to ``pending``; under that the
    model is fairly sure it is wrong and no row is written. ``rerank_score``
    keeps the re-ranker's number, and ``reranker_version`` names both models. A
    link the verifier could not judge (refused, cut off, not JSON) keeps
    classic's verdict -- an unreadable answer is not a veto.

    A link the user has already answered (``verdicts``) keeps that answer and is
    not put to the verifier: the answer is theirs, not the model's to overrule.
    """
    if not candidates:
        return 0
    scores = reranker.score(topic.text, [c.passage for c in candidates])
    # Two signals, either of which is enough to assert. The cross-encoder
    # answers "does this passage answer this query", so a past meeting on the
    # same topic but with different content -- the plan vs. its status update,
    # the same issue in other words -- scores near zero; dense similarity
    # between the two segments is what separates same-topic from
    # different-topic (docs/modules/context.md, "Metric"). The reranker stays
    # as a second way in: on the evaluation set it asserted nothing wrong.
    # ``confidence`` is the stronger of the two, and orders the trim below.
    confidence = {
        c.linked_meeting_id: max(c.similarity, float(s))
        for c, s in zip(candidates, scores, strict=True)
    }
    ranked = sorted(
        zip(candidates, scores, strict=True),
        key=lambda cs: confidence[cs[0].linked_meeting_id],
        reverse=True,
    )

    kept = []
    for candidate, rerank_score in ranked[: settings.rerank_top_k]:
        if candidate.linked_meeting_date is None:
            log.info(
                "context_link_skipped_no_date",
                meeting_id=meeting_id,
                linked_meeting_id=candidate.linked_meeting_id,
            )
            continue
        kept.append((candidate, rerank_score))

    def confident(candidate, rerank_score) -> bool:
        return bool(
            candidate.similarity >= settings.link_similarity_threshold
            or rerank_score >= settings.link_confidence_threshold
        )

    checked: dict[str, float | None] = {}
    if verifier is not None:
        asked = [
            c
            for c, s in kept
            if confident(c, s) and (c.linked_meeting_id, topic.label) not in verdicts
        ]
        if asked:
            checked = dict(
                zip(
                    (c.linked_meeting_id for c in asked),
                    verifier.verify_topics(topic.text, [c.passage for c in asked]),
                    strict=True,
                )
            )

    written = 0
    for candidate, rerank_score in kept:
        status = "asserted" if confident(candidate, rerank_score) else "pending"
        link_confidence = confidence[candidate.linked_meeting_id]
        reranker_version = reranker.model_version
        if verifier is not None and candidate.linked_meeting_id in checked:
            reranker_version = f"{reranker.model_version}|{verifier.model_version}"
            verdict = checked[candidate.linked_meeting_id]
            if verdict is not None:  # unjudged: classic's verdict stands
                if verdict < settings.llm_pending_floor:
                    continue
                link_confidence = verdict
                status = "asserted" if verdict >= settings.llm_link_threshold else "pending"
        session.add(
            CtxTopicLink(
                meeting_id=meeting_id,
                topic_label=topic.label,
                linked_meeting_id=candidate.linked_meeting_id,
                linked_meeting_date=candidate.linked_meeting_date,
                similarity=_clamp(candidate.similarity),
                rerank_score=_clamp(float(rerank_score)),
                confidence=_clamp(link_confidence),
                status=verdicts.get((candidate.linked_meeting_id, topic.label), status),
                retriever_version=f"hybrid-rrf+{embedder.model_version}",
                reranker_version=reranker_version,
            )
        )
        written += 1
    return written


def _link_topic_llm(
    session: Session,
    meeting_id: str,
    topic,
    candidates: list,
    judge: LlmJudge,
    settings,
    embedder,
    verdicts: dict[tuple[str | None, str], str],
) -> int:
    """``_link_topic`` with an LLM in place of the re-ranker and both thresholds.

    Asks only about the ``llm_topic_candidates`` best past meetings by hybrid
    retrieval (``candidates`` arrives fusion-ordered): one external call per
    candidate is what bounds this, where ``classic`` re-ranks all fifty for
    free. The LLM's probability that it is the same topic is the score: at or
    above ``llm_link_threshold`` the link is asserted, above ``llm_pending_floor``
    it is ``pending`` (ask the user), below that no row is written -- asking about
    a link the model is fairly sure is wrong is noise. Dense similarity is
    recorded but decides nothing here.

    A link the user has already answered (``verdicts``) keeps that answer and is
    not put to the model. ``rerank_score`` and ``reranker_version`` hold the LLM's
    score and the judge's version (prompt + model): the columns say what produced
    the score, not which kind of model it was.
    """
    shortlist = candidates[: settings.llm_topic_candidates]
    if not shortlist:
        return 0
    asked = [c for c in shortlist if (c.linked_meeting_id, topic.label) not in verdicts]
    scored = dict(
        zip(
            (c.linked_meeting_id for c in asked),
            judge.topic_relatedness(topic.text, [c.passage for c in asked]) if asked else [],
            strict=True,
        )
    )
    ranked = sorted(shortlist, key=lambda c: scored.get(c.linked_meeting_id, 1.0), reverse=True)

    written = 0
    for candidate in ranked[: settings.rerank_top_k]:
        human = verdicts.get((candidate.linked_meeting_id, topic.label))
        score = scored.get(candidate.linked_meeting_id, 0.0)
        if human is None and score < settings.llm_pending_floor:
            continue
        if candidate.linked_meeting_date is None:
            log.info(
                "context_link_skipped_no_date",
                meeting_id=meeting_id,
                linked_meeting_id=candidate.linked_meeting_id,
            )
            continue
        session.add(
            CtxTopicLink(
                meeting_id=meeting_id,
                topic_label=topic.label,
                linked_meeting_id=candidate.linked_meeting_id,
                linked_meeting_date=candidate.linked_meeting_date,
                similarity=_clamp(candidate.similarity),
                rerank_score=_clamp(score),
                confidence=_clamp(candidate.similarity if human else score),
                status=human or ("asserted" if score >= settings.llm_link_threshold else "pending"),
                retriever_version=f"hybrid-rrf+{embedder.model_version}",
                reranker_version=judge.model_version,
            )
        )
        written += 1
    return written


# --------------------------------------------------------------------------- #
# Decision lineage — off autune.extraction.completed, after B
# --------------------------------------------------------------------------- #

# How NLI output becomes a change type — both directions, plus a
# negation/cancellation cue for contradictions — is ``pipeline.change``.


def mark_extraction_seen(meeting_id: str) -> None:
    """Record that B has reported, without building lineage.

    ``tasks.on_extraction_completed`` calls ``build_decision_lineage`` (which
    sets ``extraction_seen`` itself), not this — there is currently no
    production caller. Kept for tests that want the publish gate flipped
    without exercising the matching/NLI machinery.
    """
    with session_scope() as session:
        _upsert_status(session, meeting_id, extraction_seen=True)


def build_decision_lineage(result: ExtractionResult) -> LineageOutcome:
    """Thread each of B's decisions into a lineage and classify how it moved.

    Returns what this run leaves owed to ``ContextLinks`` (``LineageOutcome``),
    which ``tasks.on_extraction_completed`` routes on:

    - ``LATE`` -- a late-lineage catch-up is owed: ``ContextLinks`` already
      published via the B-timeout fallback (with ``"extraction"`` in
      ``missing_sources``) before this lineage arrived. Forces a republish
      carrying the completed ``decision_lineage`` and a one-off drift-only
      notify -- see ``publish_if_ready(force=...)`` and
      ``tasks.notify_late_drift``.
    - ``REBUILT`` -- the meeting published before and this run rebuilt its
      lineage (B reran it). Republished with no notice: the drift warning
      belongs to the run that first built the lineage, but E has to hear the
      rebuilt rows -- B's ``dec_`` ids move whenever a decision's sources do.
    - ``FIRST`` -- nothing published yet; the ordinary gate decides.

    A caller that doesn't need this (tests, ``mark_extraction_seen``) can
    ignore the return value.

    ``LATE`` is *not* simply "was this run late": that would go back to False on
    a Celery redelivery of ``on_extraction_completed`` landing after this
    function's own commit (``extraction_seen`` already flipped to True) but
    before ``on_extraction_completed`` reaches its
    ``publish_if_ready.delay(force=True)`` call -- silently losing the drift
    warning the same way #257 originally did, just with the window narrowed
    instead of closed. Once a run determines it's late, it sets
    ``late_drift_due_at`` in the same transaction as ``extraction_seen``; the
    outcome is ``LATE`` whenever ``late_drift_due_at is not None`` *after*
    that write, so the "still owed" state persists on the row across a
    redelivery instead of being recomputed fresh each time.
    ``tasks.notify_late_drift`` clears it once it actually claims and sends;
    a rerun after that is ``REBUILT``.

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
    ``publish_if_ready``'s job, not this one; ``REBUILT`` is what asks for it.
    Other meetings whose versions the re-chaining touched are not republished
    (see "Decision lineage" in docs/modules/context.md).

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
    # ``engine_mode="llm"`` swaps NLI + cosine threshold for an LLM verdict, both
    # for which thread a decision joins and for how it changed (see
    # ``pipeline.llm_judge``); ``nli_version`` then records the judge. ``hybrid``
    # runs this classic: the LLM only checks topic links.
    judge: LlmJudge | None = None
    nli: NliModel | None = None
    if settings.engine_mode == "llm":
        judge = get_llm_judge()
        verdict_version = judge.model_version
    else:
        nli = get_nli()
        verdict_version = nli.model_version

    with session_scope() as session:
        meeting = session.get(Meeting, result.meeting_id)
        if meeting is None:
            raise ValueError(f"{result.meeting_id}: meeting row not found")

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
                _ThreadHead(thread_id, vector, statement)
                for (thread_id, statement), vector in zip(own_versions, own_vectors, strict=True)
            ]
        decisions = list(result.decisions)
        vectors = embedder.embed([d.statement for d in decisions]) if decisions else []
        if judge is not None:
            assignment = _assign_decisions_by_judge(
                [d.statement for d in decisions], vectors, heads, judge, settings
            )
        else:
            assignment = _assign_decisions_to_threads(
                vectors, heads, settings.lineage_match_threshold
            )

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
                    source_utterance_ids=list(decision.source_utterance_ids),
                    meeting_id=meeting.id,
                    current_statement=decision.statement,
                    previous_version_id=None,
                    previous_statement=None,
                    previous_meeting_id=None,
                    change_type=ChangeType.NEW.value,
                    nli_label=None,
                    confidence=_clamp(decision.confidence),
                    key_stakeholders_absent=[],
                    nli_version=verdict_version,
                )
            )
            affected.add(thread_id)

        session.flush()
        for thread_id in affected:
            _rethread(session, thread_id, nli, judge)

        orphans_swept = sweep_orphan_decision_threads(session)
        labels_swept = sweep_stale_topic_labels(session)
        statements_swept = sweep_dangling_previous_statements(session)

        # Read here, under the row lock, right before this run's own
        # _upsert_status overwrites extraction_seen -- not at the top of the
        # function. Embedding, NLI and the sweeps above take long enough for
        # ``publish_if_ready``'s B-timeout fallback to commit in between; a
        # read from before them would still say "not published", this run
        # would commit as an ordinary one, and the fallback's ``published_at``
        # would then turn the ordinary publish away: no drift warning, and E
        # never gets the lineage. With the lock held, either the fallback
        # committed first (we see ``published_at`` and take the late path) or
        # it waits behind us and publishes with this lineage. "Already
        # published, but not because of us" only matches on the one run that
        # flips extraction_seen False -> True, so a later B reprocess of an
        # already-seen meeting reads False and does not re-trigger the late
        # path every time -- it is REBUILT instead, read off the same row.
        before = session.get(
            CtxMeetingStatus, result.meeting_id, with_for_update=True, populate_existing=True
        )
        already_published = before is not None and before.published_at is not None
        was_late = already_published and before is not None and not before.extraction_seen
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
        if status.late_drift_due_at is not None:
            outcome = LineageOutcome.LATE
        elif already_published:
            outcome = LineageOutcome.REBUILT
        else:
            outcome = LineageOutcome.FIRST
        log.info(
            "context_decision_lineage_done",
            meeting_id=result.meeting_id,
            decisions=len(decisions),
            matched=matched_count,
            new_threads=new_threads,
            threads_swept=orphans_swept,
            labels_swept=labels_swept,
            statements_swept=statements_swept,
            outcome=outcome.value,
        )
        return outcome


class _ThreadHead:
    """A thread's most recent statement, embedded — the target
    ``_assign_decisions_to_threads`` scores a new decision against. ``statement``
    is the text behind ``vector``, for ``_assign_decisions_by_judge`` to show the
    LLM; the cosine path never reads it."""

    __slots__ = ("statement", "thread_id", "vector")

    def __init__(self, thread_id: str, vector: list[float], statement: str = "") -> None:
        self.thread_id = thread_id
        self.vector = vector
        self.statement = statement


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
    # A head whose words were deleted (#614) says nothing to match a decision
    # against, and every such head reads alike, so it is no head at all.
    head_by_thread = {
        thread_id: version
        for thread_id, version in head_by_thread.items()
        if version.current_statement != SPEECH_DELETED_TEXT
    }
    if not head_by_thread:
        return []
    ordered = sorted(head_by_thread.items())
    vectors = embedder.embed([version.current_statement for _, version in ordered])
    return [
        _ThreadHead(thread_id, vector, version.current_statement)
        for (thread_id, version), vector in zip(ordered, vectors, strict=True)
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
    return _assign_scored([c for c in candidates if c[0] >= threshold], heads)


def _assign_scored(
    candidates: list[tuple[float, int, int]], heads: list[_ThreadHead]
) -> dict[int, _ThreadHead]:
    """The greedy strongest-first assignment, from ``(score, decision index,
    head index)`` candidates already filtered to the ones worth taking. Shared by
    the cosine and the LLM paths; the scores mean different things but are only
    ever compared with each other."""
    ordered = sorted(candidates, key=lambda c: (-c[0], c[1], c[2]))

    assigned_decisions: set[int] = set()
    assigned_threads: set[str] = set()
    assignment: dict[int, _ThreadHead] = {}
    for _score, d_idx, h_idx in ordered:
        head = heads[h_idx]
        if d_idx in assigned_decisions or head.thread_id in assigned_threads:
            continue
        assigned_decisions.add(d_idx)
        assigned_threads.add(head.thread_id)
        assignment[d_idx] = head
    return assignment


def _assign_decisions_by_judge(
    statements: list[str],
    vectors: list[list[float]],
    heads: list[_ThreadHead],
    judge: LlmJudge,
    settings,
) -> dict[int, _ThreadHead]:
    """``_assign_decisions_to_threads`` with an LLM in place of the cosine cutoff.

    The embedder still shortlists: each decision is put to the LLM against its
    ``llm_thread_candidates`` most similar threads (one entry per thread, however
    many heads it has), with no similarity floor -- the point is to find out
    whether the model recognises a match the cosine would have missed. A pair is
    taken when the model calls the two the same decision at ``llm_match_threshold``
    confidence or better; the greedy assignment is the one ``classic`` uses, with
    that confidence as the score.

    ``heads`` may list a thread's own reprocessed version and its team head
    (``build_decision_lineage``); the shortlist keeps whichever is closer, so the
    LLM is shown one statement per thread.
    """
    pairs: list[tuple[str, str]] = []
    slots: list[tuple[int, int]] = []
    for d_idx, vector in enumerate(vectors):
        by_similarity = sorted(
            range(len(heads)), key=lambda h: (-_cosine(vector, heads[h].vector), h)
        )
        seen: set[str] = set()
        for h_idx in by_similarity:
            if heads[h_idx].thread_id in seen:
                continue
            seen.add(heads[h_idx].thread_id)
            pairs.append((heads[h_idx].statement, statements[d_idx]))
            slots.append((d_idx, h_idx))
            if len(seen) >= settings.llm_thread_candidates:
                break

    verdicts = judge.compare_decisions(pairs)
    return _assign_scored(
        [
            (verdict.confidence, d_idx, h_idx)
            for verdict, (d_idx, h_idx) in zip(verdicts, slots, strict=True)
            if verdict.related and verdict.confidence >= settings.llm_match_threshold
        ],
        heads,
    )


def _rethread(
    session: Session, thread_id: str, nli: NliModel | None, judge: LlmJudge | None = None
) -> None:
    """Re-chain every *visible* version of one thread in meeting-chronological
    order, and refresh the thread's ``topic_label`` to match.

    Exactly one of ``nli`` and ``judge`` decides how each version changed:
    ``judge`` (``engine_mode="llm"``) asks the LLM about each adjacent pair and
    leaves ``nli_label`` empty -- it is an NLI model's verdict, and there is none
    to record; ``nli_version`` holds the judge's version instead. A pair the LLM
    calls "unrelated" (threaded together by an earlier run, or out of order)
    reads ``modified``, the label a change nobody could match reads under NLI too.

    ``previous_*``, ``change_type``, ``nli_label``, ``confidence`` and
    ``key_stakeholders_absent`` all depend on which meeting a version follows, so
    a version that arrived out of order can only be placed by rebuilding the
    chain. NLI is re-run for every adjacent pair, in both directions (one
    batched call): it is deterministic given the model, and ``nli_version`` is
    refreshed, so a re-chain does not drift. ``confidence`` on the first
    version keeps B's decision confidence; on every later version it is the
    NLI score ``pipeline.change.classify_change`` decided on, and it is not
    recomputed back to B's number if the version
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
    # Per adjacent pair: (change type, the score it was decided on, the NLI
    # label to record -- None when there was no NLI model).
    if judge is not None:
        model_version = judge.model_version
        changes: list[tuple[ChangeType, float, str | None]] = [
            (verdict.change, verdict.confidence, None) for verdict in judge.compare_decisions(pairs)
        ]
    else:
        assert nli is not None
        model_version = nli.model_version
        changes = _classify_pairs_nli(nli, pairs)

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
            change, score, nli_label = changes[index - 1]
            version.previous_version_id = prev.id
            version.previous_statement = prev.current_statement
            version.previous_meeting_id = prev.meeting_id
            version.change_type = change.value
            version.nli_label = nli_label
            version.confidence = _clamp(score)
            version.key_stakeholders_absent = _absent_for(
                session, thread.team_id, version.meeting_id, prior_meeting_ids
            )
        version.nli_version = model_version
        prior_meeting_ids.append(version.meeting_id)

    thread.topic_label = versions[-1].current_statement[:400]
    session.flush()


def _classify_pairs_nli(
    nli: NliModel, pairs: list[tuple[str, str]]
) -> list[tuple[ChangeType, float, str | None]]:
    """``classic``'s change classification for ``(earlier, later)`` pairs: NLI in
    both directions plus the lexical cues (``pipeline.change``), in one batch.

    The label recorded is the model's own forward verdict, kept as-is:
    ``change_type`` is derived from it, not the same thing.
    """
    # One batch: forward, then each pair reversed, then forward against the
    # later statement with its keep-words stripped (only where it had any).
    stripped = {
        i: text
        for i, (_earlier, later) in enumerate(pairs)
        if (text := strip_keep_words(later)) is not None
    }
    batch = [
        *pairs,
        *[(later, earlier) for earlier, later in pairs],
        *[(pairs[i][0], text) for i, text in stripped.items()],
    ]
    out = nli.classify(batch) if pairs else []
    scored, scored_back = out[: len(pairs)], out[len(pairs) : 2 * len(pairs)]
    scored_kept = dict(zip(stripped, out[2 * len(pairs) :], strict=True))

    changes: list[tuple[ChangeType, float, str | None]] = []
    for i, (earlier, later) in enumerate(pairs):
        change, score = classify_change(
            scored[i], scored_back[i], later, earlier, scored_kept.get(i)
        )
        changes.append((change, score, NliLabel(scored[i].label).value))
    return changes


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


def _confirmed_attendance(session: Session, meeting_id: str) -> set[str] | None:
    """Who spoke in ``meeting_id``, or ``None`` until every speaker is named.

    Absence is only knowable once every voice has a name. A participant whose
    ``user_id`` is still NULL is a voice nobody has named: module A identifies
    speakers only when a person confirms one in the app (#370), which happens
    after the transcript -- and so after this lineage -- has been built. Any
    known stakeholder could be that voice, so subtracting only the resolved ids
    would call people absent from a meeting they spoke in, and each of them
    would get a decision-drift DM saying so. A meeting with no participant rows
    at all is the same case: nothing says who was there.

    ``None`` makes the caller record nobody as absent. Missing a warning is the
    cheaper error: a DM telling someone who was in the room that a decision
    changed without them is a false statement about that person. The cost is
    that a meeting with a guest who never resolves to an account never reports
    an absence; see docs/modules/context.md.

    This is the speaker list, not a roll call: module A writes a participant
    row per speaker label that spoke, so someone who attended in silence has
    no row, is not in the returned set, and is still counted absent.

    Reads the shared ``participants`` table — never writes it (invariant 4).
    """
    user_ids = session.scalars(
        select(Participant.user_id).where(Participant.meeting_id == meeting_id)
    ).all()
    if not user_ids or any(user_id is None for user_id in user_ids):
        return None
    return {user_id for user_id in user_ids if user_id is not None}


def _absent_for(
    session: Session, team_id: str, meeting_id: str, prior_meeting_ids: Sequence[str]
) -> list[str]:
    """Who the thread knew from ``prior_meeting_ids`` and ``meeting_id`` lacks.

    Empty while ``meeting_id``'s speakers are not all named
    (``_confirmed_attendance``), and never more than the thread team's *current*
    members (``_current_team_member_ids``). Sorted, so two calls over the same
    rows agree and a refresh can tell a real change from a reordering.
    """
    present = _confirmed_attendance(session, meeting_id)
    if present is None:
        return []
    known = _meeting_user_ids(session, *prior_meeting_ids)
    return sorted(_current_team_member_ids(session, team_id, known - present))


ABSENCE_REFRESH_WINDOW = timedelta(days=30)
"""How far back ``refresh_absence`` looks, by the version's meeting time. Naming a
speaker is something a person does soon after a meeting; a thread older than this
is left as ``_rethread`` last wrote it."""

ABSENCE_REFRESH_LIMIT = 200
"""Versions looked at per run, newest meeting first, so one run is bounded. A team
with more than this many changed decisions in the window leaves the oldest
unrefreshed until the newer ones age out."""


def refresh_absence(session: Session, *, now: datetime | None = None) -> int:
    """Recompute ``key_stakeholders_absent`` for versions whose attendance was
    named after the lineage was built (#360). Returns how many versions changed.

    ``_rethread`` records nobody absent while a meeting has an unnamed voice
    (``_confirmed_attendance``), which is the state of every meeting when its
    lineage is built: module A names a speaker only when a person confirms one
    in the app (#370). Nothing announces that, and #360 settled on consumers
    reading it back rather than on a new event, so this runs on a timer and
    reads ``participants`` again. It computes only the absence (``_absent_for``,
    the same call ``_rethread`` makes): no NLI, and no change to ``change_type``
    or to anything else on the row.

    **Nothing is sent.** Not to Slack, and not as a second ``ContextLinks``:
    ``republish`` would reopen module E's aggregation for one old meeting days
    later, for a field E parses and does not use (the same reasoning as #536's
    for module B's assignees). The corrected value is what the next publish,
    reprocess or ``rederive`` carries, and what ``collect_drift_notices`` reads
    if a catch-up drift warning is still owed. A DM telling someone a decision
    changed without them, days after the meeting, is a product decision this
    does not make.

    Follows a re-identification too (A can move a speaker label from one person
    to another), because it recomputes from ``participants`` each run rather
    than writing once. Idempotent and safe to overlap: two runs over the same
    rows compute the same list. Absent user ids are personal and never leave
    this function in a log line; the count is all it returns.
    """
    moment = now or datetime.now(UTC)
    candidates = session.execute(
        select(CtxDecisionVersion.id, CtxDecisionVersion.thread_id)
        .join(CtxDecision, CtxDecision.id == CtxDecisionVersion.thread_id)
        .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
        .where(
            CtxDecisionVersion.change_type != ChangeType.NEW.value,
            _meeting_time() >= moment - ABSENCE_REFRESH_WINDOW,
            *visible_meeting_clauses(CtxDecision.team_id),
        )
        .order_by(_meeting_time().desc(), CtxDecisionVersion.id)
        .limit(ABSENCE_REFRESH_LIMIT)
    ).all()
    wanted = {version_id for version_id, _thread in candidates}
    changed = 0
    for thread_id in dict.fromkeys(thread for _version, thread in candidates):
        thread = session.get(CtxDecision, thread_id)
        if thread is None:
            continue
        versions = session.scalars(
            select(CtxDecisionVersion)
            .join(Meeting, Meeting.id == CtxDecisionVersion.meeting_id)
            .where(
                CtxDecisionVersion.thread_id == thread_id,
                *visible_meeting_clauses(thread.team_id),
            )
            .order_by(_meeting_time(), CtxDecisionVersion.id)
        ).all()
        prior_meeting_ids: list[str] = []
        for version in versions:
            if version.id in wanted:
                absent = _absent_for(session, thread.team_id, version.meeting_id, prior_meeting_ids)
                if absent != version.key_stakeholders_absent:
                    version.key_stakeholders_absent = absent
                    changed += 1
            prior_meeting_ids.append(version.meeting_id)
    session.flush()
    return changed


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

    ``force=True`` is the one deliberate exception to that guard, and means
    *re*publish: it sends only for a meeting that has already published. Two
    callers set it: ``tasks.publish_if_ready`` for a ``LineageOutcome.LATE``
    lineage (it arrived *after* the B-timeout fallback published with
    ``missing_sources = ["extraction"]``), and ``tasks.republish`` for a
    rerun of either half (``LineageOutcome.REBUILT``, or
    ``run_topic_linking`` reporting the meeting already published). The
    republish carries what the rows say now to E -- which already knows how
    to accept a second completion for a meeting it aggregated once (see
    ``autune_intelligence.tasks``' "a completion after the first pass reopens
    and re-enqueues") -- and does not re-touch ``published_at`` or re-check
    the deadline/lineage gate, since we already know why we're here. A first
    publish always goes through the gate, so the first ``ContextLinks`` is
    never one a republish sent without its notices.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None or not status.topic_linking_done:
            return False
        if status.published_at is not None and not force:
            return False
        if status.published_at is None and force:
            return False

        if not force:
            timed_out = (
                status.deadline_at is not None and datetime.now(tz=UTC) >= status.deadline_at
            )
            if not (status.lineage_done or status.extraction_seen or timed_out):
                return False

        links = _build_context_links(session, meeting_id, status)
        current_app.send_task(_CONTEXT_CONSUMER_TASK, args=[links.model_dump(mode="json")])
        if not force:
            status.published_at = datetime.now(tz=UTC)
        log.info(
            "context_republished" if force else "context_published",
            meeting_id=meeting_id,
            topic_links=len(links.topic_links),
            missing_sources=links.missing_sources,
        )
        return True


def _readable_topic_links(
    session: Session, meeting_id: str, links: Sequence[CtxTopicLink]
) -> list[CtxTopicLink]:
    """Those of this meeting's ``links`` whose label may be shown (privacy.md
    section 5).

    ``ctx_topic_links.topic_label`` is cut from the speech of the segment it
    names, and a row written before #439 was cut from every utterance, consent
    or not. The link has no ``utterance_ids`` of its own; its label is the
    ``ref_label`` of the ``ctx_embeddings`` row written beside it
    (``_link_topics``), and that row says which utterances it came from. So a
    link is readable while every utterance behind its label belongs to a
    speaker who consents *now* -- a withdrawal hides it without re-deriving
    the meeting, the same read-time check ``HybridRetriever._passages`` makes
    for a passage and the pre-meeting brief makes for a recap label.

    Cannot be checked, so left out: a label whose embedding row has no
    ``utterance_ids`` (stored before #397, so possibly before #439), one with
    no embedding row at all, and one whose utterances are gone. Two segments
    can carry the same label and the link does not say which it was cut from,
    so every row with that label has to pass. Re-deriving the meeting
    (``rederive_topics``) rebuilds links and embeddings together from
    consenting speech and brings the rest back.
    """
    if not links:
        return []
    consented = consented_utterance_ids(session, meeting_id)
    segments: dict[str, list[list[str] | None]] = {}
    for label, ids in session.execute(
        select(CtxEmbedding.ref_label, CtxEmbedding.utterance_ids).where(
            CtxEmbedding.meeting_id == meeting_id, CtxEmbedding.kind == "topic"
        )
    ):
        segments.setdefault(label, []).append(ids)
    readable = {
        label
        for label, provenance in segments.items()
        if all(ids and consented.issuperset(ids) for ids in provenance)
    }
    return [link for link in links if link.topic_label in readable]


def _build_context_links(
    session: Session, meeting_id: str, status: CtxMeetingStatus
) -> ContextLinks:
    rows = _readable_topic_links(
        session,
        meeting_id,
        session.scalars(
            select(CtxTopicLink).where(
                CtxTopicLink.meeting_id == meeting_id,
                CtxTopicLink.status.in_(_PUBLISHABLE),
            )
        ).all(),
    )
    topic_links = [
        TopicLink(
            topic_label=row.topic_label,
            linked_meeting_id=row.linked_meeting_id,
            linked_meeting_date=row.linked_meeting_date,
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
    rows = _readable_topic_links(
        session,
        meeting_id,
        session.scalars(
            select(CtxTopicLink).where(
                CtxTopicLink.meeting_id == meeting_id, CtxTopicLink.status == "asserted"
            )
        ).all(),
    )
    return [
        TopicLinkNotice(topic_label=row.topic_label, linked_meeting_date=row.linked_meeting_date)
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
    meeting = session.get(Meeting, meeting_id)
    notices = []
    for version in rows:
        absent = version.key_stakeholders_absent
        if not absent:
            continue
        thread = session.get(CtxDecision, version.thread_id)
        thread_label = thread.topic_label if thread is not None else version.current_statement[:400]
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
                meeting_date=(
                    meeting_day(meeting.started_at) if meeting and meeting.started_at else None
                ),
            )
        )
    return notices


def _post_to_channel(
    slack: SlackApi, channel: str, fallback: str, blocks: list[dict], *, notice: str
) -> bool:
    """Post one channel notice; ``False`` when Slack refuses it for good.

    Every send in this module runs *after* its claim commits
    (``tasks.notify_context_events``, ``tasks.notify_late_drift``), so a
    notice that raises out of a loop takes every notice after it with it, and
    none is ever retried. A permanent refusal -- ``ok: false`` such as
    ``not_in_channel`` once the bot is removed (#280, #478) -- is therefore
    logged and skipped, not raised. A transient failure still raises: the task
    fails loudly rather than a rate limit silently becoming a lost notice. The
    log carries Slack's error code only, never the message it refused.
    """
    try:
        slack.post_message(channel, fallback, blocks)
    except PermanentIntegrationError as exc:
        log.warning("context_channel_notice_refused", notice=notice, error=exc.code)
        return False
    return True


def send_topic_link_notices(slack: SlackApi, channel: str, notices: list[TopicLinkNotice]) -> int:
    """Post the channel notices for already-collected topic links.

    Capped at ``ContextSettings.max_topic_link_notices`` individual messages;
    anything past the cap collapses into one rollup notice instead of posting
    one message per topic, so a meeting with many linked topics does not flood
    the channel. Returns how many topic links reached the channel -- shown ones
    posted, plus the rolled-up count if the rollup posted. A notice Slack
    refuses is skipped (``_post_to_channel``).
    """
    cap = get_settings().max_topic_link_notices
    shown, overflow = notices[:cap], notices[cap:]
    sent = 0
    for notice in shown:
        fallback, blocks = build_topic_link_notice(
            topic_label=notice.topic_label, linked_meeting_date=notice.linked_meeting_date
        )
        sent += _post_to_channel(slack, channel, fallback, blocks, notice="topic_link")
    if overflow:
        fallback, blocks = build_topic_link_rollup_notice(count=len(overflow))
        if _post_to_channel(slack, channel, fallback, blocks, notice="topic_link_rollup"):
            sent += len(overflow)
    log.info(
        "context_topic_link_notice_sent",
        count=len(notices),
        shown=len(shown),
        rolled_up=len(overflow),
        sent=sent,
    )
    return sent


def send_decision_drift_notices(slack: SlackApi, channel: str, notices: list[DriftNotice]) -> int:
    """Post the decision-drift warnings for already-collected drift events.

    One channel notice per event (never names the absentees -- see
    ``notify.py``), plus one DM per absent stakeholder (does not need to name
    anyone -- they are the recipient). Returns the count of drift events whose
    channel notice posted (not the count of DMs sent).

    **One unreachable recipient never costs the others theirs.** A channel
    notice Slack refuses is skipped (``_post_to_channel``) and that event's DMs
    still go. A DM Slack refuses for good -- the person has not linked a Slack
    account for direct messages, or ``ok: false`` (#478) -- is counted and
    skipped, and the next person still gets theirs. Nothing is retried: the
    claim committed before this ran. ``PrivacyViolationError`` is not a
    ``PermanentIntegrationError`` and still raises; a transient failure still
    raises too.

    The skipped-DM log names no one. Each recipient here is someone who was
    *absent* when the decision changed, which the channel notice deliberately
    reduces to a count (``notify.py``); a log line keyed by their id would
    keep the per-person record that notice refuses to publish.
    """
    posted = dms_sent = 0
    dms_skipped: dict[str, int] = {}
    for notice in notices:
        channel_fallback, channel_blocks = build_decision_drift_channel_notice(
            thread_label=notice.thread_label,
            current_statement=notice.statement_preview,
            change_type=notice.change_type,
            absent_count=len(notice.absent_user_ids),
            meeting_date=notice.meeting_date,
        )
        posted += _post_to_channel(
            slack, channel, channel_fallback, channel_blocks, notice="decision_drift"
        )

        dm_fallback, dm_blocks = build_decision_drift_personal_dm(
            thread_label=notice.thread_label,
            current_statement=notice.statement_preview,
            change_type=notice.change_type,
            meeting_date=notice.meeting_date,
        )
        for user_id in notice.absent_user_ids:
            try:
                _deliver_personal(slack, user_id, dm_fallback, dm_blocks)
            except PermanentIntegrationError as exc:
                dms_skipped[exc.code] = dms_skipped.get(exc.code, 0) + 1
                continue
            dms_sent += 1

    if dms_skipped:
        log.warning("context_decision_drift_dm_skipped", by_error=dms_skipped)
    log.info(
        "context_decision_drift_notice_sent",
        count=len(notices),
        posted=posted,
        dms_sent=dms_sent,
    )
    return posted


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


# --------------------------------------------------------------------------- #
# Who may read — every route under /api/context that names a meeting, a link,
# a thread or a team calls one of these first (#189)
# --------------------------------------------------------------------------- #


def _is_team_member(session: Session, *, user_id: str, team_id: str) -> bool:
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.user_id == user_id, TeamMember.team_id == team_id
            )
        )
        is not None
    )


def _refuse(kind: str, ident: object, reader: User, reason: str) -> NotFoundError:
    # Ids only: a title or a topic label is meeting content.
    log.info(
        "context_read_refused", kind=kind, ident=str(ident), reader_id=reader.id, reason=reason
    )
    return NotFoundError(kind, str(ident))


def require_readable_team(session: Session, team_id: str, reader: User) -> None:
    """Raise unless ``reader`` belongs to ``team_id``.

    A token proves who is asking, not whose meetings they may read. **An
    unknown id and somebody else's get the same answer**, a ``NotFoundError``
    and never a 403 -- a 403 confirms the id exists, and ids are all a caller
    needs to walk the table. Same rule as ``autune_gap.service
    .require_readable_meeting`` (#276); the log keeps the reason.
    """
    if not _is_team_member(session, user_id=reader.id, team_id=team_id):
        raise _refuse("team", team_id, reader, "not_a_member")


def require_readable_meeting(session: Session, meeting_id: str, reader: User) -> None:
    """Raise unless ``reader`` belongs to this meeting's team. See
    ``require_readable_team`` for why every refusal is a ``NotFoundError``."""
    team_id = session.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id))
    if team_id is None:
        raise _refuse("meeting", meeting_id, reader, "no_such_meeting")
    if not _is_team_member(session, user_id=reader.id, team_id=team_id):
        raise _refuse("meeting", meeting_id, reader, "not_a_member")


def require_writable_link(session: Session, link_id: int, reader: User) -> None:
    """Raise unless ``reader`` belongs to the team of the meeting this link is on.

    The one write under /api/context, and the id most worth guarding: a link id
    is an integer primary key, so every link can be reached by counting from 1
    (#189). The refusal says "topic link" either way, so a caller learns nothing
    about the meeting behind a link it may not touch.
    """
    team_id = session.scalar(
        select(Meeting.team_id)
        .join(CtxTopicLink, CtxTopicLink.meeting_id == Meeting.id)
        .where(CtxTopicLink.id == link_id)
    )
    if team_id is None:
        raise _refuse("topic link", link_id, reader, "no_such_link")
    if not _is_team_member(session, user_id=reader.id, team_id=team_id):
        raise _refuse("topic link", link_id, reader, "not_a_member")


def require_readable_thread(session: Session, thread_id: str, reader: User) -> None:
    """Raise unless ``reader`` belongs to the team this decision thread is on.

    A thread is anchored on ``team_id``, not on a meeting, so that is the team
    checked.
    """
    team_id = session.scalar(select(CtxDecision.team_id).where(CtxDecision.id == thread_id))
    if team_id is None:
        raise _refuse("decision thread", thread_id, reader, "no_such_thread")
    if not _is_team_member(session, user_id=reader.id, team_id=team_id):
        raise _refuse("decision thread", thread_id, reader, "not_a_member")


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

    A link whose label is cut from speech that is not consented to is left out
    of both lists (``_readable_topic_links``), as it is from what E receives
    and from the Slack notice.
    """
    links = _readable_topic_links(
        session,
        meeting_id,
        session.scalars(
            select(CtxTopicLink)
            .join(Meeting, Meeting.id == CtxTopicLink.meeting_id)
            .where(CtxTopicLink.meeting_id == meeting_id, *visible_meeting_clauses(Meeting.team_id))
            .order_by(CtxTopicLink.confidence.desc())
        ).all(),
    )
    asserted = [link for link in links if link.status in _PUBLISHABLE]
    pending = [link for link in links if link.status == "pending"]
    return asserted, pending


def confirm_topic_link(session: Session, link_id: int, new_status: str) -> CtxTopicLink:
    """Record a user's decision on a ``pending`` link.

    Only a ``pending`` link accepts a decision through this route — one already
    settled, whether by an earlier confirm or because it started ``asserted``,
    does not get a second one. A link whose meeting has since expired is
    treated as not found, the same "gone" rule ``get_topic_links`` applies —
    there is nothing left to confirm a link for. So is a link whose label is not
    readable (``_readable_topic_links``): the route answers with the link, label
    included, so it must not give a hidden one out.
    """
    link = session.get(CtxTopicLink, link_id)
    if (
        link is None
        or not _meeting_is_visible(session, link.meeting_id)
        or not _readable_topic_links(session, link.meeting_id, [link])
    ):
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


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# --------------------------------------------------------------------------- #
# Deletion -- a person deleted their own speech (#587, #614)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpeechForgotten:
    """Counts only: what ``forget_speech`` changed, safe to log."""

    topics_deleted: int = 0
    links_deleted: int = 0
    statements_cleared: int = 0
    agendas_dropped: int = 0


def forget_speech(session: Session, utterance_ids: Sequence[str]) -> SpeechForgotten:
    """Drop the words D copied from utterances that are about to be deleted (#587).

    Decided with the user (2026-10-01): the work stays, their words go. In D:

    - **A topic goes only when every utterance it was cut from goes** -- in this
      batch or an earlier one, so a line already deleted counts as gone -- its
      embedding, which is a vector of that text, and the topic links carrying its
      label. A topic somebody else also named is still the meeting's topic, said
      in their words too, so it stays (as in C). A topic with no record of what it
      was cut from (written before ``utterance_ids``) goes with the meeting's
      first deletion: not knowing is not a reason to keep it.
    - **A decision statement goes when any line it was drawn from goes**: it reads
      ``SPEECH_DELETED_TEXT`` when one of the utterances B drew it from is deleted,
      or when there is no record of them (written before ``source_utterance_ids``).
      The rule differs from a topic's because the two hold different things: a
      statement is B's assembled quote of those lines, word for word, so a
      deleted line's words are still in it however many others sit beside them;
      a topic label is a name several people gave, and is no one person's
      sentence. The thread, its versions, how each changed and when stay -- that is
      the team's work. Wherever the statement was copied -- a later version's
      ``previous_statement``, the thread's label -- the copy reads the same.
    - **The team's agenda snapshot is dropped**: it holds B's issue titles, which
      B rewrites on the same signal, and B republishes it every few minutes. Until
      then the brief says it has no agenda, which is better than quoting a title
      the person has deleted.

    A decision B cited no line for (``[]``) stays: nothing says it was theirs.

    Runs before the utterances are deleted, because that is how it finds the
    meetings. Safe to repeat: the second time there is nothing left to find.
    """
    gone = set(utterance_ids)
    if not gone:
        return SpeechForgotten()
    meetings = {
        meeting_id: team_id
        for meeting_id, team_id in session.execute(
            select(Meeting.id, Meeting.team_id)
            .join(Utterance, Utterance.meeting_id == Meeting.id)
            .where(Utterance.id.in_(gone))
            .distinct()
        )
    }
    if not meetings:
        return SpeechForgotten()

    # Topics: an embedding, and the links named after it.
    topic_ids: list[int] = []
    gone_labels: dict[str, set[str]] = {}
    embeddings = session.scalars(
        select(CtxEmbedding).where(
            CtxEmbedding.meeting_id.in_(meetings), CtxEmbedding.kind == "topic"
        )
    ).all()
    # Lines a topic was cut from that are not in this batch: a topic stays only
    # while one of them still exists, so an earlier round's deletions count.
    others = {
        line
        for embedding in embeddings
        for line in (embedding.utterance_ids or ())
        if line not in gone
    }
    still_said = (
        set(session.scalars(select(Utterance.id).where(Utterance.id.in_(others))))
        if others
        else set()
    )
    for embedding in embeddings:
        cut_from = embedding.utterance_ids
        if cut_from is None or (cut_from and not (set(cut_from) - gone) & still_said):
            topic_ids.append(embedding.id)
            gone_labels.setdefault(embedding.meeting_id, set()).add(embedding.ref_label)
    links_deleted = 0
    if topic_ids:
        session.execute(delete(CtxEmbedding).where(CtxEmbedding.id.in_(topic_ids)))
        session.flush()
        for meeting_id, labels in gone_labels.items():
            still_named = set(
                session.scalars(
                    select(CtxEmbedding.ref_label).where(
                        CtxEmbedding.meeting_id == meeting_id, CtxEmbedding.kind == "topic"
                    )
                )
            )
            links_deleted += len(
                session.scalars(
                    delete(CtxTopicLink)
                    .where(
                        CtxTopicLink.meeting_id == meeting_id,
                        CtxTopicLink.topic_label.in_(labels - still_named),
                    )
                    .returning(CtxTopicLink.id)
                ).all()
            )

    # Decisions: the statement, then every place it was copied to.
    cleared = [
        version
        for version in session.scalars(
            select(CtxDecisionVersion).where(CtxDecisionVersion.meeting_id.in_(meetings))
        )
        if version.current_statement != SPEECH_DELETED_TEXT
        and (
            version.source_utterance_ids is None
            or (version.source_utterance_ids and set(version.source_utterance_ids) & gone)
        )
    ]
    if cleared:
        for version in session.scalars(
            select(CtxDecisionVersion).where(
                CtxDecisionVersion.previous_version_id.in_({v.id for v in cleared}),
                CtxDecisionVersion.previous_statement.is_not(None),
            )
        ):
            version.previous_statement = SPEECH_DELETED_TEXT
        for thread in session.scalars(
            select(CtxDecision).where(CtxDecision.id.in_({v.thread_id for v in cleared}))
        ):
            if any(thread.topic_label == v.current_statement[:400] for v in cleared):
                thread.topic_label = SPEECH_DELETED_TEXT
        for version in cleared:
            version.current_statement = SPEECH_DELETED_TEXT
        session.flush()

    agendas_dropped = len(
        session.scalars(
            delete(CtxTeamAgenda)
            .where(CtxTeamAgenda.team_id.in_(set(meetings.values())))
            .returning(CtxTeamAgenda.team_id)
        ).all()
    )
    return SpeechForgotten(
        topics_deleted=len(topic_ids),
        links_deleted=links_deleted,
        statements_cleared=len(cleared),
        agendas_dropped=agendas_dropped,
    )


@on_speech_deleted("context")
def forget_deleted_speech(user_id: str, utterance_ids: Sequence[str]) -> None:
    """Before a person's own speech is deleted (#587, #614): ``forget_speech``.

    Registered from this file because ``router`` imports it: A's deletion runs in
    the API process, which imports every router and no ``tasks`` module (as C's
    and E's hooks are). Raises on failure, so A's deletion stops rather than
    leaving the words behind in D; commits in its own transaction before A
    deletes, erring toward deleting more. Nothing is republished: E clears its
    own copy of D's links on the same signal. Ids and counts only.
    """
    with session_scope() as session:
        done = forget_speech(session, utterance_ids)
    log.info(
        "context_speech_forgotten",
        user_id=user_id,
        utterances=len(utterance_ids),
        topics_deleted=done.topics_deleted,
        links_deleted=done.links_deleted,
        statements_cleared=done.statements_cleared,
        agendas_dropped=done.agendas_dropped,
    )
