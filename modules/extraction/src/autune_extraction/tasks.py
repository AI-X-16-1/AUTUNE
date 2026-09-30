"""Celery tasks for module B.

Discovered automatically by apps/worker. Tasks parse, delegate to ``service``,
and publish. Every task must be safe to run twice — see
docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy.orm import Session

from autune_contracts import (
    AGENDA_PUBLISH_EVERY,
    EXTRACTION_AGENDA_CHANGED,
    EXTRACTION_COMPLETED,
    TranscriptReady,
    validate_major_version,
)
from autune_core import (
    Meeting,
    PrivacyViolationError,
    get_logger,
    load_integration,
    load_user_integration,
    periodic,
    publish,
    session_scope,
    users_with_integration,
)
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import (
    CalendarClient,
    IntegrationError,
    NotionClient,
    PermanentIntegrationError,
    refresh_access_token,
)

from . import calendar_sync, service
from .models import ExtActionItem, ExtCalendarPoll, ExtDecision
from .pipeline.registry import get_classifier, get_nli, get_resolver

log = get_logger(__name__)


@shared_task(name="autune.extraction.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    """Consume TranscriptReady from module A: classify, then write what it found.

    Decisions grouped, draft items for commitments, and a record of every
    ambiguous agreement -- not yet asked about, because nothing can send the DM
    (#70, #30).

    ``shared_task`` binds to whichever Celery app is running, so this module
    never imports apps/worker.

    Classification, then NLI, then reference resolution (#175) happen between
    two sessions, never inside one -- all three are model inference, and a
    transaction held around them holds a connection and its locks for all of
    that time (``service.classify_utterances``, ``service.verify_utterances``,
    ``service.resolve_commitment_references``). The first session only reads
    which utterances belong to a speaker who consented (privacy.md section 5);
    nobody else's speech reaches any of the three models. The writes that
    follow share one transaction: classifications, decisions and draft items
    all come from the same NLI-verified, reference-resolved predictions, and a
    meeting holding one run's labels and another run's items is not a state
    anything downstream should be able to read.

    Safe to run twice. Every write replaces the meeting's model-made rows rather
    than adding to them, so a redelivered task ends where the first one did --
    except that draft items are left alone once a person has edited any
    (``service.build_action_items``).

    **Then it publishes ``ExtractionResult`` on ``autune.extraction.completed``**
    (D and E consume it), built by ``service.result_for_meeting`` from the rows
    this run just wrote -- the same builder ``GET /results/{meeting_id}`` uses,
    so what the event carries and what the board reads are one thing read one
    way, including an item a person added or kept. It is built inside the write
    transaction and sent only after that transaction has closed: a rollback
    after the event had gone would leave two modules analysing a result that
    was never stored. Every run publishes, a redelivered one included: a
    decision whose source utterances changed has a new ``dec_`` id
    (``decisions.decision_id``), and module A mints new ``utt_`` ids whenever it
    reprocesses a recording (#194), so D has to hear the ids that are now in the
    table.
    """
    transcript = TranscriptReady.model_validate(payload)
    validate_major_version(transcript)
    # Refuses a transcript whose raw audio was not deleted or text not masked.
    transcript.require_privacy_guarantees()

    log.info(
        "extraction_received_transcript",
        meeting_id=transcript.meeting_id,
        utterances=len(transcript.utterances),
    )

    with session_scope() as session:
        consented = service.consented_utterance_ids(session, transcript.meeting_id)

    classifier = get_classifier()
    classified = service.classify_utterances(classifier, transcript.utterances, consented=consented)
    classified = service.verify_utterances(get_nli(), classified)

    resolver = get_resolver()
    resolved_descriptions = service.resolve_commitment_references(resolver, classified)

    with session_scope() as session:
        stored = service.store_classifications(
            session,
            meeting_id=transcript.meeting_id,
            utterances=classified,
            model_version=classifier.model_version,
        )
        decisions = service.build_decisions(
            session, meeting_id=transcript.meeting_id, utterances=classified
        )
        items = service.build_action_items(
            session,
            meeting_id=transcript.meeting_id,
            utterances=transcript.utterances,
            classified=classified,
            resolved=resolved_descriptions,
        )
        ambiguous = service.record_ambiguous_agreements(
            session, meeting_id=transcript.meeting_id, classified=classified
        )
        result = service.result_for_meeting(session, transcript.meeting_id)

    # Counts and ids only. The utterances are meeting content.
    log.info(
        "extraction_classified",
        meeting_id=transcript.meeting_id,
        utterances=len(classified),
        excluded=sum(1 for u in transcript.utterances if u.id not in consented),
        classified=stored,
        decisions=len(decisions),
        action_items=len(items) if items is not None else "kept",
        ambiguous=ambiguous,
        model_version=classifier.model_version,
        resolver_model_version=resolver.model_version,
        resolved_commitments=len(resolved_descriptions),
    )
    # TODO(강민구): step 6, the DM: for each of ``service.unasked_confirmations``,
    # resolve the speaker's Slack account and call
    # ``service.ask_for_confirmation`` -- blocked on an account mapping (#70)
    # and a team Slack client (#30). Step 7, Notion (#30) -- Jira was
    # dropped (#82): both its auth paths tie a workspace to whoever set it up.

    # Step 8, after the writes have committed. The payload is never logged:
    # decision statements and item descriptions are meeting content.
    publish(EXTRACTION_COMPLETED, result.model_dump(mode="json"))


@shared_task(name="autune.extraction.sync_action_item", acks_late=True)
def sync_action_item(action_item_id: str) -> None:
    """Step 7 for one item past confirmation: create its Notion page the
    first time, update the same page every edit after (#30, #342).

    Runs whenever the board changes an item that has already left
    ``needs_confirmation`` (``sync_after_confirmation``), never before: nothing
    the model drafted is confirmed at that point, and #246 keeps unconfirmed
    items in Autune.

    A team that has not connected Notion is skipped, not failed -- the ordinary
    answer from ``load_integration`` (``autune_core.integrations_config``). The
    client is built from the team's own credential and dropped when the task
    ends, the way module E builds its Slack client.

    Safe to run twice: the page is claimed in ``ext_external_refs`` before the
    call, and the claim is the primary key (``service.sync_action_item_to_notion``).
    A failed call rolls the claim back, so a later confirmation can send.

    **It does not retry itself.** A timeout is raised as a transient error, and the
    common shape of one is a POST that reached Notion and made the page while the
    response was lost: retrying then claims again and makes a second page, with
    only the last one recorded. Losing a page to a timeout is the cheaper failure —
    the person can confirm again. Raised in review of #294.
    """
    with session_scope() as session:
        item = session.get(ExtActionItem, action_item_id)
        meeting = session.get(Meeting, item.meeting_id) if item is not None else None
        if item is None or meeting is None:
            log.info("extraction_notion_item_gone", action_item_id=action_item_id)
            return
        config = load_integration(session, meeting.team_id, "notion")
        database_id = config.config.get("action_db_id") if config is not None else None
        if config is None or not config.secret or not database_id:
            # Asked for, not required: a team that connected Notion for decisions
            # only, or whose token is gone, is skipped. ``require_secret()`` and
            # ``require()`` raise ValidationError, which is not an
            # IntegrationError -- it came out of the confirming request as a 422
            # rather than a skipped page. Raised in review of #294.
            log.info(
                "extraction_notion_not_connected",
                action_item_id=action_item_id,
                team_id=meeting.team_id,
            )
            return
        service.sync_action_item_to_notion(
            session,
            NotionClient(config.secret),
            action_item_id=action_item_id,
            database_id=database_id,
            property_names=config.config.get("action_properties"),
        )


def sync_after_confirmation(action_item_id: str) -> None:
    """Run the sync in the API process, right after an edit's response --
    the confirming edit and every one after it (#342), not confirmation only.

    The router hands this to FastAPI's background tasks rather than queueing
    ``sync_action_item`` on the broker: apps/api builds no Celery app, so a
    ``delay`` from a request has nowhere to go, and wiring one in is a change to
    the team's shared assembly. The claim in ``ext_external_refs`` makes the
    first page once; ``with_for_update`` in ``sync_action_item_to_notion``
    keeps two of these in flight at once from writing out of order.

    The person's edit is already committed when this runs, so a Notion failure
    must not surface as an error on the board. It is logged by id and the claim
    is rolled back, which lets the next confirmation of that item send.

    **``PrivacyViolationError`` is caught the same way.** ``check_outbound``
    raises it, not ``IntegrationError`` -- a sibling, not a subclass -- when
    the confirmed description or assignee label still carries unmasked PII.
    No leak happens either way; the send is still blocked. Left uncaught here
    it would crash this background task instead of logging gracefully, the
    same silent failure an unhandled ``IntegrationError`` would be (review,
    #333).
    """
    try:
        sync_action_item(action_item_id)
    except IntegrationError:
        log.warning("extraction_notion_sync_failed", action_item_id=action_item_id)
    except PrivacyViolationError:
        log.warning(
            "extraction_notion_sync_blocked_by_privacy_guard", action_item_id=action_item_id
        )
    # Separately, so a Notion failure never costs the calendar its event and
    # the other way round.
    try:
        sync_action_item_calendar(action_item_id)
    except IntegrationError:
        log.warning("extraction_calendar_sync_failed", action_item_id=action_item_id)
    except PrivacyViolationError:
        log.warning(
            "extraction_calendar_sync_blocked_by_privacy_guard", action_item_id=action_item_id
        )


@contextmanager
def _calendars(session: Session) -> Iterator[calendar_sync.CalendarFor]:
    """A lookup from a person to their own calendar client and calendar id --
    ``None`` for someone who has not connected one -- with every client it
    opened closed on the way out.

    Each person's own grant (``user_integrations``, #444) is refreshed with the
    deployment's Google client (core's ``google_client_id``/``google_client_secret``,
    #425); a deployment without them has nobody connected as far as this is
    concerned. A refused refresh token raises ``ReconnectRequiredError`` -- an
    ``IntegrationError`` -- for the caller to handle.
    """
    core = get_core_settings()
    client_id = core.google_client_id
    client_secret = core.google_client_secret
    opened: dict[str, tuple[calendar_sync.CalendarEvents, str]] = {}
    clients: list[CalendarClient] = []

    def calendar_for(user_id: str) -> tuple[calendar_sync.CalendarEvents, str] | None:
        if user_id in opened:
            return opened[user_id]
        config = load_user_integration(session, user_id, calendar_sync.CALENDAR)
        if config is None or not config.secret or not client_id or not client_secret:
            return None
        token = refresh_access_token(
            client_id=client_id, client_secret=client_secret, refresh_token=config.secret
        )
        client = CalendarClient(token)
        clients.append(client)
        opened[user_id] = (client, str(config.config.get("calendar_id") or "primary"))
        return opened[user_id]

    try:
        yield calendar_for
    finally:
        for client in clients:
            client.close()


@shared_task(name="autune.extraction.sync_action_item_calendar", acks_late=True)
def sync_action_item_calendar(action_item_id: str) -> None:
    """Step 7's calendar half (#435): the item's due date on its assignee's own
    calendar -- ``calendar_sync.sync_due_date_to_calendar``.

    Skipped, not failed, for an assignee who has not connected a calendar and
    for a deployment without Google client credentials (``_calendars``). Like
    the Notion sync it does not retry itself: a timed-out create may have made
    the event, and a retry would make a second.
    """
    with session_scope() as session, _calendars(session) as calendar_for:
        calendar_sync.sync_due_date_to_calendar(
            session, calendar_for, action_item_id=action_item_id
        )


CALENDAR_POLL_OVERLAP = timedelta(minutes=2)
"""Each read starts this far before the last one ended, so an edit saved while
the previous read was running is not missed. Reading an event twice is
harmless: a date equal to ``synced_due_date`` is ignored."""

CALENDAR_FIRST_LOOKBACK = timedelta(days=1)


@shared_task(name="autune.extraction.periodic.pull_calendar_changes")
@periodic(timedelta(minutes=10))
def pull_calendar_changes() -> None:
    """Every ten minutes, read back what each connected person changed on their
    own calendar (#435): a task they dragged to another day has a new due date.
    One person at a time, each with their own grant and their own transaction,
    so one person's revoked token does not stop anyone else's read.

    **Anything one person's read raises is theirs alone** -- not only an
    integration error but a database error or an answer that did not parse
    (PARKJAEKYUNG0525, review of #441). It is logged by the error's type and the
    user id, never its message: an exception string is treated as published
    (privacy.md section 6), and a database error carries its parameters.
    """
    with session_scope() as session:
        user_ids = users_with_integration(session, calendar_sync.CALENDAR)
    for user_id in user_ids:
        try:
            moved = _pull_one(user_id)
        except Exception as exc:  # noqa: BLE001 -- one person's failure is theirs alone
            log.warning(
                "extraction_calendar_pull_failed", user_id=user_id, error=type(exc).__name__
            )
            continue
        # Committed; now Notion follows the new date, the way it follows an
        # edit on the board.
        for action_item_id in moved:
            try:
                sync_action_item(action_item_id)
            except (IntegrationError, PrivacyViolationError):
                log.warning("extraction_notion_sync_failed", action_item_id=action_item_id)


def _pull_one(user_id: str) -> list[str]:
    started = datetime.now(UTC)
    with session_scope() as session, _calendars(session) as calendar_for:
        connection = calendar_for(user_id)
        if connection is None:
            return []
        client, calendar_id = connection
        cursor = session.get(ExtCalendarPoll, user_id)
        since = (
            cursor.polled_at - CALENDAR_POLL_OVERLAP
            if cursor is not None
            else started - CALENDAR_FIRST_LOOKBACK
        )
        try:
            moved = calendar_sync.pull_calendar_changes(
                session, client, user_id=user_id, calendar_id=calendar_id, since=since
            )
        except PermanentIntegrationError as exc:
            # 410 updatedMinTooLongAgo: the cursor is older than Google keeps
            # changes for -- a person back after a week-long lapsed grant. Read
            # from the first-connection lookback instead, or the cursor never
            # moves again (mminjae97, review of #441).
            if exc.details.get("upstream_status") != 410:
                raise
            moved = calendar_sync.pull_calendar_changes(
                session,
                client,
                user_id=user_id,
                calendar_id=calendar_id,
                since=started - CALENDAR_FIRST_LOOKBACK,
            )
        # An upsert: two overlapping runs for someone just connected would
        # otherwise both insert, and the second would fail on the key (review
        # of #441; ``autune_core.periodic`` requires overlap safety).
        session.execute(
            service._insert_if_absent_into(session, ExtCalendarPoll)
            .values(user_id=user_id, polled_at=started)
            .on_conflict_do_update(index_elements=["user_id"], set_={"polled_at": started})
        )
        return moved


def remove_calendar_event(action_item_id: str) -> None:
    """Before the board deletes an item: its event off its assignee's calendar
    (``calendar_sync.remove_event``). Runs in the deleting request, best effort
    -- an unreachable calendar never blocks a deletion."""
    try:
        with session_scope() as session, _calendars(session) as calendar_for:
            calendar_sync.remove_event(session, calendar_for, action_item_id=action_item_id)
    except Exception as exc:  # noqa: BLE001 -- a deletion must not fail on a calendar
        log.warning(
            "extraction_calendar_remove_failed",
            action_item_id=action_item_id,
            error=type(exc).__name__,
        )


@shared_task(name="autune.extraction.sync_decision", acks_late=True)
def sync_decision(decision_id: str) -> None:
    """Step 7 for one decision a person just confirmed: its Notion page, once.

    ``sync_action_item``'s rules, for the team's decision database
    (``decision_db_id`` in its Notion config). A team that connected Notion for
    action items only has no ``decision_db_id``, and is skipped rather than failed.
    No self-retry, for the reason ``sync_action_item`` gives.
    """
    with session_scope() as session:
        decision = session.get(ExtDecision, decision_id)
        meeting = session.get(Meeting, decision.meeting_id) if decision is not None else None
        if decision is None or meeting is None:
            log.info("extraction_notion_decision_gone", decision_id=decision_id)
            return
        config = load_integration(session, meeting.team_id, "notion")
        database_id = config.config.get("decision_db_id") if config is not None else None
        if config is None or not config.secret or not database_id:
            log.info(
                "extraction_notion_decisions_not_connected",
                decision_id=decision_id,
                team_id=meeting.team_id,
            )
            return
        service.sync_decision_to_notion(
            session,
            NotionClient(config.secret),
            decision_id=decision_id,
            database_id=database_id,
            property_names=config.config.get("decision_properties"),
        )


def sync_decision_after_confirmation(decision_id: str) -> None:
    """``sync_after_confirmation`` for a decision: in the API process, never
    failing the confirmation that started it. Catches ``PrivacyViolationError``
    the same way and for the same reason -- see that function's own note."""
    try:
        sync_decision(decision_id)
    except IntegrationError:
        log.warning("extraction_notion_decision_sync_failed", decision_id=decision_id)
    except PrivacyViolationError:
        log.warning(
            "extraction_notion_decision_sync_blocked_by_privacy_guard", decision_id=decision_id
        )


@shared_task(name="autune.extraction.periodic.publish_team_agendas")
@periodic(AGENDA_PUBLISH_EVERY)
def publish_team_agendas() -> None:
    """Every five minutes, each team's open Jira issues as one ``TeamAgenda``
    (#436), for the brief D sends ten minutes before a meeting.

    Periodic rather than on each edit: the board's edits run in the API
    process, which has no Celery app to publish from (#170), and a snapshot
    that is republished cannot be lost the way a single change event can. A
    team whose last issue closed still gets its now-empty snapshot, so D does
    not keep showing it. Only ids are logged; the titles are meeting content.
    """
    now = datetime.now(UTC)
    with session_scope() as session:
        agendas = [
            service.team_agenda(session, team_id, now=now)
            for team_id in service.teams_with_jira_issues(session)
        ]
    for agenda in agendas:
        publish(EXTRACTION_AGENDA_CHANGED, agenda.model_dump(mode="json"))
        log.info("extraction_agenda_published", team_id=agenda.team_id, issues=len(agenda.issues))
