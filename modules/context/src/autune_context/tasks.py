"""Celery tasks for the Meeting Context Engine.

Two entry points with different inputs:

- **Topic linking** needs only the transcript, so it runs in parallel with B
  and C, straight off ``autune.transcript.ready``.
- **Decision lineage** needs the decisions B extracted, so it runs after
  ``autune.extraction.completed``.

``ContextLinks`` is published once both have run — or, if B never reports, with
an empty ``decision_lineage`` and ``"extraction"`` in ``missing_sources``. A
failure in B must not cost the user their topic links.

A successful publish also fires ``notify_context_events``: the topic-link
notice and the decision-drift warning, docs/modules/context.md "Slack surface".
A rerun of either half after that (a module A reprocess) goes out through
``republish`` instead, with no notice.

Separately, on a clock: ``periodic.send_due_briefs`` finds scheduled meetings
about to start and ``send_brief`` posts each one's pre-meeting brief
(``autune_context.briefs``). Its agenda comes from ``autune.extraction.agenda_changed``
-- B's ``TeamAgenda``, kept by ``on_extraction_agenda_changed`` (#436).

Also on a clock: ``periodic.refresh_absence`` corrects who a changed decision's
meeting is recorded as missing once its speakers have been named (#360).

See docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from celery import shared_task
from sqlalchemy.orm import Session

import autune_context.pipeline  # noqa: F401  (registers the worker_process_init warm-up hook)
from autune_context import briefs, service
from autune_context.config import get_settings
from autune_context.models import CtxBrief, CtxMeetingStatus
from autune_context.notify import build_pre_meeting_brief
from autune_contracts import (
    ExtractionResult,
    TeamAgenda,
    TranscriptReady,
    validate_major_version,
)
from autune_core import (
    IntegrationConfig,
    Meeting,
    get_logger,
    load_integration,
    periodic,
    session_scope,
)
from autune_integrations import SlackClient, TransientIntegrationError

log = get_logger(__name__)


def _slack_target(
    session: Session, meeting_id: str, *, event_prefix: str
) -> tuple[str, IntegrationConfig] | None:
    """This meeting's team's Slack channel and credentials, or ``None`` with a
    logged reason -- shared by ``notify_context_events`` and
    ``notify_late_drift``, which otherwise repeat the same three checks."""
    team_id = session.scalar(sa.select(Meeting.team_id).where(Meeting.id == meeting_id))
    if team_id is None:
        log.info(f"{event_prefix}_meeting_gone", meeting_id=meeting_id)
        return None
    config = load_integration(session, team_id, "slack")
    if config is None:
        log.info(f"{event_prefix}_no_slack", meeting_id=meeting_id, team_id=team_id)
        return None
    channel = config.config.get("channel")
    if channel is None:
        log.info(f"{event_prefix}_no_channel", meeting_id=meeting_id, team_id=team_id)
        return None
    return channel, config


@shared_task(name="autune.context.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    """Link this meeting's topics to past meetings. Runs in parallel with B and C."""
    transcript = TranscriptReady.model_validate(payload)
    validate_major_version(transcript)
    # Refuses a transcript whose raw audio was not deleted or text not masked.
    transcript.require_privacy_guarantees()

    log.info(
        "context_received_transcript",
        meeting_id=transcript.meeting_id,
        utterances=len(transcript.utterances),
    )
    if service.run_topic_linking(transcript):
        # A rerun of a meeting that already published (module A reprocessed
        # the recording, or this is a redelivery): the gate below would refuse
        # it, and E would keep the old links.
        republish.delay(transcript.meeting_id)
        return

    # Try now (B may already be in); also arm the B-timeout fallback.
    publish_if_ready.delay(transcript.meeting_id)
    publish_if_ready.apply_async(
        (transcript.meeting_id,), countdown=get_settings().publish_timeout_s
    )


@shared_task(name="autune.context.rederive_topics", acks_late=True)
def rederive_topics(meeting_id: str) -> None:
    """Re-run topic linking from the stored transcript after the consent
    behind it changed -- see ``service.rederive_topics``.

    Routes like ``on_transcript_ready``: a meeting that already published is
    republished with no notice; one still waiting on B gets its publish check
    and B-timeout fallback armed again, since re-running moved its deadline.
    A meeting the service declines (never analysed, expired, no privacy
    guarantees) is left as it was.
    """
    already_published = service.rederive_topics(meeting_id)
    if already_published is None:
        return
    if already_published:
        republish.delay(meeting_id)
        return
    publish_if_ready.delay(meeting_id)
    publish_if_ready.apply_async((meeting_id,), countdown=get_settings().publish_timeout_s)


@shared_task(name="autune.context.on_extraction_completed", acks_late=True)
def on_extraction_completed(payload: dict) -> None:
    """Thread B's decisions into lineage. Runs after B.

    Matches each decision to a thread, runs NLI against the previous statement,
    and records how the decision moved. Then routes on
    ``service.LineageOutcome``:

    - ``FIRST`` -- re-checks whether ``ContextLinks`` can be published.
    - ``LATE`` -- the B-timeout fallback already published without this
      lineage: ``force=True`` republishes past the ``published_at`` guard and
      sends the drift warning that publish could not.
    - ``REBUILT`` -- B reran a meeting that already published: ``republish``
      sends E the rebuilt lineage and notifies nobody.
    """
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)

    log.info(
        "context_received_extraction",
        meeting_id=result.meeting_id,
        decisions=len(result.decisions),
    )
    outcome = service.build_decision_lineage(result)
    if outcome is service.LineageOutcome.REBUILT:
        republish.delay(result.meeting_id)
        return
    publish_if_ready.delay(result.meeting_id, force=outcome is service.LineageOutcome.LATE)


@shared_task(name="autune.context.on_extraction_agenda_changed", acks_late=True)
def on_extraction_agenda_changed(payload: dict) -> None:
    """Keep a team's open Jira issues for its next pre-meeting brief (#436).

    B republishes every team's ``TeamAgenda`` every five minutes, so a lost or
    refused one is replaced shortly; a late one older than the stored snapshot
    is ignored (``briefs.store_team_agenda``). Nothing is sent from here -- the
    brief reads the stored snapshot when it is composed.
    """
    agenda = TeamAgenda.model_validate(payload)
    validate_major_version(agenda)
    with session_scope() as session:
        briefs.store_team_agenda(session, agenda)


@shared_task(name="autune.context.publish_if_ready", acks_late=True)
def publish_if_ready(meeting_id: str, force: bool = False) -> None:
    """Publish ContextLinks once both halves are in, or when B has timed out.

    Never block topic links on a failure in B: publish what exists and name what
    is missing.

    ``force`` is set only by ``on_extraction_completed`` for a lineage that
    arrived late; it republishes past the ordinary ``published_at`` guard and
    routes to ``notify_late_drift`` (drift only) instead of
    ``notify_context_events`` (topic links already went out the first time --
    see ``service.publish_if_ready``'s docstring).
    """
    published = service.publish_if_ready(meeting_id, force=force)
    log.info("context_publish_checked", meeting_id=meeting_id, published=published, force=force)
    if not published:
        return
    if force:
        notify_late_drift.apply_async((meeting_id,))
        return
    # ``service.publish_if_ready`` only returns True once per meeting
    # (guarded by ``published_at``), so this enqueues ``notify_context_events``
    # only once even though the task itself is armed twice (an immediate
    # check and a B-timeout fallback) and can also be retried by Celery.
    # That covers *enqueueing* once, not *executing* once -- ``acks_late``
    # is at-least-once delivery, so Celery can still redeliver the queued
    # notify task itself. ``notify_context_events`` closes that gap on its
    # own with a ``notified_at`` claim, so a redelivered execution is a
    # no-op rather than a duplicate post.
    notify_context_events.apply_async((meeting_id,))


@shared_task(name="autune.context.republish", acks_late=True)
def republish(meeting_id: str) -> None:
    """Re-send ``ContextLinks`` after a rerun rebuilt what it carries.

    Enqueued by ``on_transcript_ready`` and ``on_extraction_completed`` when
    their half reran for a meeting that had already published -- a module A
    reprocess mints new ``utt_`` ids, so B's ``dec_`` ids and D's links move
    with it. Sends nothing to Slack: the notices went out with the first
    publish, and a rerun is not news to the team. A catch-up for late lineage
    is ``publish_if_ready(force=True)``, not this.

    A no-op for a meeting that has not published yet (``service
    .publish_if_ready`` refuses ``force`` there), so its first publish still
    goes through the gate and sends its notices.
    """
    republished = service.publish_if_ready(meeting_id, force=True)
    log.info("context_republish_checked", meeting_id=meeting_id, republished=republished)


_NOTIFY_RETRY = {
    "autoretry_for": (TransientIntegrationError,),
    "retry_backoff": True,
    "retry_kwargs": {"max_retries": 3},
}
"""A Slack rate limit or timeout is retried, with the claim handed back first
(``_release_claim``). Anything else -- a refusal for good, a privacy guard --
is not: it would fail the same way again."""


def _release_claim(meeting_id: str, **restore: datetime | None) -> None:
    """Hand a notice claim back after a transient Slack failure (#339).

    The claim commits before the first message goes out, so that a redelivered
    task is not a second post. The price is that a rate limit halfway through
    the batch left the meeting claimed with notices unsent, and the retry then
    found it claimed and did nothing -- an absent stakeholder who was never
    told, for good. Releasing it lets the retry send again.

    The retry also sends again what had already gone out before the failure:
    a repeated channel post and a repeated DM, against a warning that never
    arrives. That is the better of the two, and the one this module can bound
    (three tries).
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None:
            return
        for column, value in restore.items():
            setattr(status, column, value)


@shared_task(name="autune.context.notify_context_events", acks_late=True, **_NOTIFY_RETRY)
def notify_context_events(meeting_id: str) -> None:
    """Post this meeting's topic-link notices and decision-drift warnings.

    Fired once, right after ``ContextLinks`` is published. A team that has not
    connected Slack, or has connected it without a channel, is skipped rather
    than failed -- same convention as ``autune_intelligence.tasks``.

    ``notified_at`` claims the meeting -- atomically, via ``with_for_update``,
    mirroring ``publish_if_ready``'s ``published_at`` guard -- *before*
    anything is sent, so a Celery redelivery of this task is a no-op instead of
    a duplicate post. Collecting the rows to send and claiming both happen
    inside one session that is closed *before* any Slack HTTP call, the same
    "gather in session, send after" shape ``autune_intelligence.tasks
    .generate_weekly_report`` uses -- a slow or rate-limited Slack response
    then never holds a pooled DB connection open.

    Skips drift specifically (not topic links) if ``late_drift_notified_at``
    is already set, or if a catch-up is owed (``late_drift_due_at`` is set):
    ordinarily this task claims and sends before this meeting's lineage
    exists at all, so ``collect_drift_notices`` is naturally empty here and
    ``notify_late_drift`` sends the drift later. But ``late_drift_due_at`` and
    the ``ctx_decision_versions`` rows it covers commit in the same
    transaction (see ``service.build_decision_lineage``), so if
    ``build_decision_lineage`` commits between this task's enqueue and its
    run, ``collect_drift_notices`` would otherwise find those rows too and
    send them here *and* again from ``notify_late_drift`` -- the
    ``late_drift_due_at`` check closes that race, not just the
    already-reversed one ``late_drift_notified_at`` alone covers.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None:
            log.info("context_notify_meeting_gone", meeting_id=meeting_id)
            return
        if status.notified_at is not None:
            log.info("context_notify_already_claimed", meeting_id=meeting_id)
            return

        target = _slack_target(session, meeting_id, event_prefix="context_notify")
        if target is None:
            return
        channel, config = target

        topic_notices = service.collect_topic_link_notices(session, meeting_id)
        drift_notices = (
            []
            if status.late_drift_notified_at is not None or status.late_drift_due_at is not None
            else service.collect_drift_notices(session, meeting_id)
        )
        status.notified_at = datetime.now(tz=UTC)

    slack = SlackClient(config.require_secret())
    try:
        links_sent = service.send_topic_link_notices(slack, channel, topic_notices)
        drift_sent = service.send_decision_drift_notices(slack, channel, drift_notices)
    except TransientIntegrationError:
        log.warning("context_notify_released", meeting_id=meeting_id)
        _release_claim(meeting_id, notified_at=None)
        raise

    log.info(
        "context_notify_sent",
        meeting_id=meeting_id,
        topic_links=links_sent,
        drift_warnings=drift_sent,
    )


@shared_task(name="autune.context.notify_late_drift", acks_late=True, **_NOTIFY_RETRY)
def notify_late_drift(meeting_id: str) -> None:
    """Send this meeting's decision-drift warnings when its lineage finished
    late -- after ``ContextLinks`` had already published via the B-timeout
    fallback. See ``service.build_decision_lineage``'s ``was_late`` return and
    ``publish_if_ready(force=...)``, which routes here instead of
    ``notify_context_events``.

    Topic-link notices already went out in the original
    ``notify_context_events`` pass (topic linking finishes before the
    B-timeout fires); this meeting's own ``ctx_decision_versions`` did not
    exist at that first pass (B had not reported yet), so sending its drift
    here duplicates nothing from that earlier notify. Guarded by its own
    ``late_drift_notified_at`` claim -- the same shape as ``notified_at``,
    kept separate because it protects a different, later-firing send. Also
    clears ``late_drift_due_at`` -- see ``service.build_decision_lineage`` --
    since there is nothing left to catch up on once this claims.
    """
    with session_scope() as session:
        status = session.get(CtxMeetingStatus, meeting_id, with_for_update=True)
        if status is None:
            log.info("context_late_drift_meeting_gone", meeting_id=meeting_id)
            return
        if status.late_drift_notified_at is not None:
            log.info("context_late_drift_already_claimed", meeting_id=meeting_id)
            return

        target = _slack_target(session, meeting_id, event_prefix="context_late_drift")
        if target is None:
            # Nowhere to send, so nothing is owed. Left set, every later B
            # reprocess of this meeting would read "still owed" and take the
            # late path again instead of a plain republish, and a team that
            # connects Slack weeks from now would get a drift notice for a
            # meeting long past. Not marked notified either -- nothing was sent.
            status.late_drift_due_at = None
            return
        channel, config = target

        drift_notices = service.collect_drift_notices(session, meeting_id)
        owed_since = status.late_drift_due_at
        status.late_drift_notified_at = datetime.now(tz=UTC)
        status.late_drift_due_at = None

    slack = SlackClient(config.require_secret())
    try:
        drift_sent = service.send_decision_drift_notices(slack, channel, drift_notices)
    except TransientIntegrationError:
        # ``late_drift_due_at`` goes back too: ``notify_context_events`` reads it
        # to leave drift to this task, and cleared with no claim it would send
        # the same warnings itself.
        log.warning("context_late_drift_released", meeting_id=meeting_id)
        _release_claim(meeting_id, late_drift_notified_at=None, late_drift_due_at=owed_since)
        raise
    log.info("context_late_drift_sent", meeting_id=meeting_id, drift_warnings=drift_sent)


@shared_task(name="autune.context.periodic.send_due_briefs", acks_late=True)
@periodic(timedelta(minutes=1))
def send_due_briefs() -> None:
    """Enqueue a brief for every scheduled meeting now inside the lead time.

    Every minute, so a brief lands within a minute of ``brief_lead_minutes``
    before the start. Only finds meetings; ``send_brief`` claims and sends, so
    one meeting's failure does not hold up another's, and a meeting enqueued
    twice (this run overlapping the last, or ``send_brief`` still queued from
    it) is claimed once.

    Also deletes team agendas B stopped refreshing (``briefs.purge_stale_agendas``):
    a snapshot is text copied from B, and this is the clock that bounds how long
    a copy outlives its source.
    """
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        briefs.purge_stale_agendas(session, now)
        due = briefs.due_meeting_starts(session, now)
    for meeting_id, starts_at in due:
        # A send still queued at the start is noise -- and on a stalled
        # cpu_heavy worker, one more per tick. Celery drops it unrun instead.
        send_brief.apply_async((meeting_id,), expires=starts_at)
    if due:
        log.info("context_briefs_due", count=len(due))


@shared_task(name="autune.context.periodic.refresh_absence", acks_late=True)
@periodic(timedelta(minutes=10))
def refresh_absence() -> int:
    """Recompute who was absent from a changed decision once its meeting's
    speakers have been named (#360; ``service.refresh_absence``). Returns how
    many versions changed.

    Every ten minutes because naming a speaker is a person on a screen, and a
    run that finds nothing to change reads a few rows. Sends nothing and
    publishes nothing: see ``service.refresh_absence`` for why not a
    ``republish``. Safe to overlap: two runs over the same rows write the same
    list. The log carries the count only -- the ids are people.
    """
    with session_scope() as session:
        changed = service.refresh_absence(session)
    log.info("context_absence_refreshed", versions=changed)
    return changed


@shared_task(name="autune.context.send_brief", acks_late=True)
def send_brief(meeting_id: str) -> None:
    """Compose one meeting's pre-meeting brief and post it to the team channel.

    Composed even when the team has no Slack channel -- the brief is still
    readable at ``GET /api/context/briefs/{meeting_id}`` -- but then not
    marked sent. Claimed and composed in one session that closes before the
    Slack call, the "gather in session, send after" shape
    ``notify_context_events`` uses.

    A transient Slack failure deletes the claim and raises, and the clock
    retries; it does not retry itself, because the next tick is a minute away
    and the meeting only needs the brief before it starts. Anything else keeps
    the claim, as the other notices do.
    """
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        target = _slack_target(session, meeting_id, event_prefix="context_brief")
        brief = briefs.compose_due_brief(session, meeting_id, now=now, will_send=target is not None)
    if brief is None or target is None or brief.starts_at is None:
        return
    channel, config = target

    fallback, blocks = build_pre_meeting_brief(
        title=brief.title,
        starts_at=brief.starts_at,
        minutes_until=briefs.minutes_until(brief.starts_at, now),
        recap=brief.recap,
        recap_gone=brief.recap_gone,
        agenda=brief.agenda,
        recap_is_related=brief.match_reason != briefs.LATEST,
    )
    try:
        SlackClient(config.require_secret()).post_message(channel, fallback, blocks)
    except TransientIntegrationError:
        # The claim is the ``ctx_briefs`` row, and it is what ``due_meeting_starts``
        # looks for: with it gone, the next tick finds the meeting due again and
        # sends once more while it is still ahead. Left in place, a rate limit
        # cost the meeting its brief for good (#339).
        log.warning("context_brief_released", meeting_id=meeting_id)
        with session_scope() as session:
            session.execute(sa.delete(CtxBrief).where(CtxBrief.meeting_id == meeting_id))
        raise
    log.info("context_brief_sent", meeting_id=meeting_id, match_reason=brief.match_reason)
