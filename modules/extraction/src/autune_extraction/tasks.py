"""Celery tasks for module B.

Discovered automatically by apps/worker. Tasks parse, delegate to ``service``,
and publish. Every task must be safe to run twice — see
docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_contracts import (
    AGENDA_PUBLISH_EVERY,
    EXTRACTION_AGENDA_CHANGED,
    EXTRACTION_COMPLETED,
    TranscriptReady,
    validate_major_version,
)
from autune_contracts.enums import ActionStatus
from autune_contracts.transcript import Utterance as TranscriptUtterance
from autune_core import (
    AutuneError,
    Meeting,
    PrivacyViolationError,
    TeamIntegration,
    Utterance,
    get_logger,
    jira_access,
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
    JiraClient,
    NotionClient,
    PermanentIntegrationError,
    SlackClient,
    refresh_access_token,
)
from autune_integrations.errors import SlackRecipientNotLinkedError

from . import calendar_sync, jira_sync, notion_setup, service
from .models import ExtActionItem, ExtCalendarPoll, ExtDecision, ExtExternalRef
from .pipeline.base import give_roster
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
    _extract(transcript.meeting_id, transcript.utterances)


def _extract(meeting_id: str, utterances: Sequence[TranscriptUtterance]) -> None:
    """Everything ``on_transcript_ready`` does after the payload is checked;
    ``reextract_consent_changes`` runs it too, on the stored transcript."""
    with session_scope() as session:
        consented = service.consented_utterance_ids(session, meeting_id)
        roster = service.team_roster(session, meeting_id)
        day = service.decision_day(session, meeting_id)

    classifier = get_classifier()
    # A classifier that sends text out replaces these names first (#411).
    give_roster(classifier, roster)
    classified = service.classify_utterances(classifier, utterances, consented=consented)
    classified = service.verify_utterances(get_nli(), classified)

    resolver = get_resolver()
    # The resolver sends text out too, when it is the ``llm`` one (#411).
    give_roster(resolver, roster)
    summaries = service.resolve_commitment_summaries(resolver, classified)
    resolved_descriptions = {uid: resolution.text for uid, resolution in summaries.items()}
    related_lines = {uid: resolution.used for uid, resolution in summaries.items()}
    decision_summaries = service.resolve_decision_summaries(
        resolver, classified, meeting_id=meeting_id, day=day
    )

    with session_scope() as session:
        stored = service.store_classifications(
            session,
            meeting_id=meeting_id,
            utterances=classified,
            model_version=classifier.model_version,
        )
        decisions = service.build_decisions(
            session,
            meeting_id=meeting_id,
            utterances=classified,
            summaries=decision_summaries,
        )
        items = service.build_action_items(
            session,
            meeting_id=meeting_id,
            utterances=utterances,
            classified=classified,
            resolved=resolved_descriptions,
            related=related_lines,
        )
        ambiguous = service.record_ambiguous_agreements(
            session, meeting_id=meeting_id, classified=classified
        )
        # With the rows it describes: a rollback takes both (#518).
        service.record_extraction(session, meeting_id=meeting_id, consented=consented)
        result = service.result_for_meeting(session, meeting_id)

    # Counts and ids only. The utterances are meeting content.
    log.info(
        "extraction_classified",
        meeting_id=meeting_id,
        utterances=len(classified),
        excluded=sum(1 for u in utterances if u.id not in consented),
        classified=stored,
        decisions=len(decisions),
        action_items=len(items) if items is not None else "kept",
        ambiguous=ambiguous,
        model_version=classifier.model_version,
        resolver_model_version=resolver.model_version,
        resolved_commitments=len(resolved_descriptions),
    )
    # Step 6, the DM, is ``ask_confirmations``, not this run: a speaker who is
    # identified or links Slack a little later is still asked. Step 7 waits for
    # a person to confirm (#246).

    # Step 8, after the writes have committed. The payload is never logged:
    # decision statements and item descriptions are meeting content.
    publish(EXTRACTION_COMPLETED, result.model_dump(mode="json"))


@shared_task(name="autune.extraction.periodic.reextract_consent_changes")
@periodic(timedelta(minutes=10))
def reextract_consent_changes() -> list[str]:
    """Extract again every meeting whose consenting speech changed after its
    last extraction (#518). Returns those meetings' ids.

    B extracts when ``TranscriptReady`` arrives, and the consent filter reads
    ``participants.consented`` at that moment. A team that records consent
    after the recording was analysed -- A's ``attest_consent`` -- would
    otherwise keep an empty result for that meeting for good: nothing announces
    the change (#360), so this compares ``ext_extraction_runs`` with the
    consent as it is now, the way C's ``rescore_changed_people`` does (#506).

    The transcript is read back from the shared tables (``stored_transcript``).
    The extraction is the event's own (``_extract``): model rows are replaced,
    an item list a person has edited is kept, and ``ExtractionResult`` is
    published again, so D and E see the new result. Speech that lost consent
    leaves B's model rows the same way; what a person already edited or sent
    out from it is the second half of #518, which waits on per-person
    withdrawal (S10/S11).

    Every ten minutes because a consent attestation is a person on a screen,
    and a run that finds nothing changed is two queries. Safe to overlap: two
    runs that see the same change both extract, and the second writes what the
    first did. One meeting failing does not stop the rest -- its row still
    disagrees, so the next run tries it again -- **except a privacy violation**,
    which is raised once the others are done, ids only, as C's sweep does.
    """
    with session_scope() as session:
        changed = service.meetings_with_changed_consent(session)

    done: list[str] = []
    violations: list[str] = []
    for meeting_id in changed:
        try:
            with session_scope() as session:
                utterances = service.stored_transcript(session, meeting_id)
            _extract(meeting_id, utterances)
        except PrivacyViolationError:
            violations.append(meeting_id)
            continue
        except Exception as exc:  # noqa: BLE001 - one meeting must not stop the rest
            # The class name only: an exception over stored rows can carry
            # transcript text in its message.
            log.warning(
                "extraction_reextract_failed", meeting_id=meeting_id, reason=type(exc).__name__
            )
            continue
        done.append(meeting_id)

    log.info(
        "extraction_consent_swept",
        changed=len(changed),
        reextracted=len(done),
        violations=len(violations),
    )
    if violations:
        raise PrivacyViolationError(
            f"unmasked value on re-extraction in {len(violations)} meeting(s): "
            f"{', '.join(violations)}"
        )
    return done


@shared_task(name="autune.extraction.periodic.fill_identified_assignees")
@periodic(timedelta(minutes=10))
def fill_identified_assignees() -> list[str]:
    """Items whose speaker was identified after extraction get that person as
    their assignee (#360; ``service.fill_identified_assignees``). Returns the
    items' ids.

    Then, after the commit, an item already confirmed goes through
    ``sync_after_confirmation`` -- the same call the router makes after a board
    edit -- so its Notion page and Jira issue name the person, and a due date
    goes on their own calendar.

    ``ExtractionResult`` is **not** published again, the way a board edit does
    not publish it: D and E read the assignee on the meeting's next run. A
    republish here would reopen E's aggregation for just these meetings, days
    later, and for no other kind of correction (lsh2217's review of #536).

    Every ten minutes because identifying a speaker is a person on a screen,
    and a run that finds nothing is one query. Safe to overlap: the update is
    conditional on the assignee and label as read, so a second run changes
    nothing the first did.
    """
    with session_scope() as session:
        filled = service.fill_identified_assignees(session)
        confirmed = [
            item.id for item in filled if item.status != ActionStatus.NEEDS_CONFIRMATION.value
        ]
        filled_ids = [item.id for item in filled]

    for action_item_id in confirmed:
        sync_after_confirmation(action_item_id)
    # Ids and counts only: the assignee is a person.
    log.info("extraction_assignees_filled", items=len(filled_ids), synced=len(confirmed))
    return filled_ids


@shared_task(name="autune.extraction.periodic.ask_confirmations")
@periodic(timedelta(minutes=5))
def ask_confirmations() -> list[str]:
    """Step 6: DM each speaker the ambiguous agreement they made -- "was that a
    commitment?" -- and start its clock (#70, WBS 8.3). Returns the utterance
    ids asked about.

    What is asked is ``service.confirmations_to_ask``: not yet asked, recorded
    inside ``CONFIRMATION_TIMEOUT``, by an identified, consenting speaker. The
    DM goes to that speaker only, through the team's Slack bot, to the Slack
    account they linked (#255, #478); ``send_confirmation_dm`` refuses any
    other recipient. The answer comes back through ``slack.handle_block_action``.

    Each question is claimed and sent in its own transaction
    (``ask_for_confirmation``): a failed send takes the claim back and the next
    run asks again, and a claim another run holds sends nothing. A team with no
    Slack connection, and a speaker who has not linked a Slack account, are
    skipped and looked at again next time, until the window closes. Any other
    integration failure is logged by class and retried the same way. **A
    privacy violation is never swallowed**: the others are still asked, then it
    is raised with the utterance ids, as the extraction's sweeps do.

    Every five minutes: a question should reach the speaker while the meeting
    is still on their mind, and a run with nothing to ask is one query.
    """
    with session_scope() as session:
        pending = service.confirmations_to_ask(session)
        secrets: dict[str, str | None] = {}
        for team_id in sorted({q.team_id for q in pending}):
            config = load_integration(session, team_id, "slack")
            secrets[team_id] = config.require_secret() if config is not None else None

    asked: list[str] = []
    violations: list[str] = []
    for question in pending:
        secret = secrets[question.team_id]
        if secret is None:
            continue
        try:
            with session_scope() as session:
                said = session.get(Utterance, question.utterance_id)
                if said is None:
                    continue
                row = service.ask_for_confirmation(
                    session,
                    SlackClient(secret),
                    meeting_id=question.meeting_id,
                    speaker_id=question.speaker_id,
                    recipient_id=question.speaker_id,
                    utterance_id=question.utterance_id,
                    quoted_text=said.text,
                )
        except PrivacyViolationError:
            violations.append(question.utterance_id)
            continue
        except SlackRecipientNotLinkedError:
            log.info("extraction_confirmation_not_linked", utterance_id=question.utterance_id)
            continue
        except IntegrationError as exc:
            log.warning(
                "extraction_confirmation_send_failed",
                utterance_id=question.utterance_id,
                reason=type(exc).__name__,
            )
            continue
        if row is not None:
            asked.append(question.utterance_id)

    log.info(
        "extraction_confirmations_asked",
        pending=len(pending),
        asked=len(asked),
        violations=len(violations),
    )
    if violations:
        raise PrivacyViolationError(
            f"unmasked value in {len(violations)} confirmation DM(s): {', '.join(violations)}"
        )
    return asked


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
        database_id = notion_setup.database_id(session, meeting.team_id, config, "action_db_id")
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
    # And Jira on its own too. ``AutuneError`` covers a refused Jira grant
    # (``JiraReconnectRequiredError``) as well as integration errors.
    try:
        sync_action_item_jira(action_item_id)
    except AutuneError as exc:
        log.warning("extraction_jira_sync_failed", action_item_id=action_item_id, error=exc.code)


@shared_task(name="autune.extraction.sync_action_item_jira", acks_late=True)
def sync_action_item_jira(action_item_id: str) -> None:
    """Step 7's Jira half (#82): the item as one issue in the team's chosen
    project -- ``jira_sync.sync_action_item_to_jira``.

    Skipped, not failed, for a team that has not connected Jira, has not chosen
    a project, or whose connection needs someone to reconnect
    (``autune_core.jira_access`` answers ``None``). The access token is fetched
    fresh for the run; the refresh token never reaches this module. Like the
    Notion sync it does not retry itself: a timed-out create may have made the
    issue, and a retry would make a second.
    """
    with session_scope() as session:
        item = session.get(ExtActionItem, action_item_id)
        meeting = session.get(Meeting, item.meeting_id) if item is not None else None
        if item is None or meeting is None:
            return
        # ``check_project``: a project deleted in Jira comes back as no project,
        # recorded for the screen to ask for a new one (#458).
        access = jira_access(meeting.team_id, check_project=True)
        if access is None or not access.project_key:
            log.info("extraction_jira_not_connected", action_item_id=action_item_id)
            return
        config = load_integration(session, meeting.team_id, jira_sync.JIRA)
        client = JiraClient.for_cloud(access.access_token, access.cloud_id)
        try:
            jira_sync.sync_action_item_to_jira(
                session,
                client,
                action_item_id=action_item_id,
                project_key=access.project_key,
                site=access.cloud_id,
                site_url=config.config.get("site_url") if config is not None else None,
            )
        finally:
            client.close()


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
            except IntegrationError:
                log.warning("extraction_notion_sync_failed", action_item_id=action_item_id)
            except PrivacyViolationError:
                # Blocked, not failed: the outbound check refused the send and
                # nothing left. Its own event, as ``sync_after_confirmation``
                # logs it, so a privacy block never reads as a flaky Notion.
                log.warning(
                    "extraction_notion_sync_blocked_by_privacy_guard",
                    action_item_id=action_item_id,
                )


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


@shared_task(name="autune.extraction.periodic.pull_jira_changes")
@periodic(timedelta(minutes=10))
def pull_jira_changes() -> None:
    """Every ten minutes, read back the status people moved their issues to in
    Jira (``jira_sync.pull_status_changes``): an issue dragged to Done is a done
    item on the board. One team at a time, each with its own access token and
    its own transaction, so one team's lapsed connection stops nobody else's.

    Skipped for a team whose connection needs a person to reconnect
    (``jira_access`` answers ``None``). Anything one team's read raises is
    logged by the error's type and the team id, never its message -- the
    calendar read-back's rule. After the commit, Notion follows the new status
    the way it follows a board edit.
    """
    with session_scope() as session:
        team_ids = _jira_teams(session)
    for team_id in team_ids:
        try:
            moved = _pull_jira_team(team_id)
        except Exception as exc:  # noqa: BLE001 -- one team's failure is theirs alone
            log.warning("extraction_jira_pull_failed", team_id=team_id, error=type(exc).__name__)
            continue
        for action_item_id in moved:
            try:
                sync_action_item(action_item_id)
            except IntegrationError:
                log.warning("extraction_notion_sync_failed", action_item_id=action_item_id)
            except PrivacyViolationError:
                # Blocked, not failed: the outbound check refused the send and
                # nothing left. Its own event, as ``sync_after_confirmation``
                # logs it, so a privacy block never reads as a flaky Notion.
                log.warning(
                    "extraction_notion_sync_blocked_by_privacy_guard",
                    action_item_id=action_item_id,
                )


def _jira_teams(session: Session) -> list[str]:
    """Teams with a Jira connection stored. A read of core's table, never a write."""
    return list(
        session.scalars(
            select(TeamIntegration.team_id)
            .where(TeamIntegration.service == jira_sync.JIRA, TeamIntegration.secret.is_not(None))
            .order_by(TeamIntegration.team_id)
        )
    )


def _pull_jira_team(team_id: str) -> list[str]:
    access = jira_access(team_id)
    if access is None:
        return []
    client = JiraClient.for_cloud(access.access_token, access.cloud_id)
    try:
        with session_scope() as session:
            return jira_sync.pull_status_changes(
                session, client, team_id=team_id, site=access.cloud_id
            )
    finally:
        client.close()


def backfill_jira(team_id: str) -> dict[str, int]:
    """Every confirmed item of the team into its Jira project -- after a project
    is chosen, including a new one chosen because the old was deleted (#458).

    Each item goes through ``sync_action_item_to_jira``: an issue that still
    exists is rewritten, one that went with a deleted project answers 404 and
    is made again in the chosen project, one never sent is created. One access
    token for the run; one item's failure is counted and does not stop the rest.
    """
    counts = {"synced": 0, "failed": 0}
    access = jira_access(team_id, check_project=True)
    if access is None or not access.project_key:
        return counts
    with session_scope() as session:
        config = load_integration(session, team_id, jira_sync.JIRA)
        site_url = config.config.get("site_url") if config is not None else None
        item_ids = list(
            session.scalars(
                select(ExtActionItem.id)
                .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
                .where(
                    Meeting.team_id == team_id,
                    ExtActionItem.status != ActionStatus.NEEDS_CONFIRMATION.value,
                )
                .order_by(ExtActionItem.id)
            )
        )
    client = JiraClient.for_cloud(access.access_token, access.cloud_id)
    try:
        for action_item_id in item_ids:
            try:
                with session_scope() as session:
                    jira_sync.sync_action_item_to_jira(
                        session,
                        client,
                        action_item_id=action_item_id,
                        project_key=access.project_key,
                        site=access.cloud_id,
                        site_url=site_url,
                    )
                counts["synced"] += 1
            except Exception as exc:  # noqa: BLE001 -- one item's failure is its own
                counts["failed"] += 1
                log.warning(
                    "extraction_jira_backfill_item_failed",
                    action_item_id=action_item_id,
                    error=type(exc).__name__,
                )
    finally:
        client.close()
    log.info("extraction_jira_backfilled", team_id=team_id, **counts)
    return counts


def trash_notion_page(action_item_id: str) -> None:
    """Before the board deletes an item: its Notion page to the workspace's
    trash, restorable there for 30 days (decided with the user, #467). Runs in
    the deleting request, best effort -- an unreachable Notion never blocks a
    deletion. Jira closes instead (``close_jira_issue``): Jira has no trash."""
    try:
        with session_scope() as session:
            ref = session.get(ExtExternalRef, (action_item_id, "notion"))
            item = session.get(ExtActionItem, action_item_id)
            meeting = session.get(Meeting, item.meeting_id) if item is not None else None
            if ref is None or not ref.external_id or meeting is None:
                return
            config = load_integration(session, meeting.team_id, "notion")
            if config is None or not config.secret:
                return
            client = NotionClient(config.secret)
            try:
                client.trash_page(str(ref.external_id))
            finally:
                client.close()
            log.info("extraction_notion_trashed_with_item", action_item_id=action_item_id)
    except Exception as exc:  # noqa: BLE001 -- a deletion must not fail on Notion
        log.warning(
            "extraction_notion_trash_failed",
            action_item_id=action_item_id,
            error=type(exc).__name__,
        )


def close_jira_issue(action_item_id: str) -> None:
    """Before the board deletes an item: its Jira issue closed with a note
    (``jira_sync.close_for_deleted_item``). Runs in the deleting request, best
    effort -- an unreachable Jira never blocks a deletion."""
    try:
        with session_scope() as session:
            item = session.get(ExtActionItem, action_item_id)
            meeting = session.get(Meeting, item.meeting_id) if item is not None else None
            if meeting is None:
                return
            access = jira_access(meeting.team_id)
            if access is None:
                return
            client = JiraClient.for_cloud(access.access_token, access.cloud_id)
            try:
                jira_sync.close_for_deleted_item(
                    session, client, action_item_id=action_item_id, site=access.cloud_id
                )
            finally:
                client.close()
    except Exception as exc:  # noqa: BLE001 -- a deletion must not fail on Jira
        log.warning(
            "extraction_jira_close_failed", action_item_id=action_item_id, error=type(exc).__name__
        )


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
        database_id = notion_setup.database_id(session, meeting.team_id, config, "decision_db_id")
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
