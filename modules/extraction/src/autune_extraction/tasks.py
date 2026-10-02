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
    ACTION_PROGRESS_PUBLISH_EVERY,
    AGENDA_PUBLISH_EVERY,
    EXTRACTION_ACTION_PROGRESS,
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
from autune_core.deletion import on_meeting_deleted, on_speech_deleted, on_user_deleted
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import (
    CalendarClient,
    IntegrationError,
    JiraClient,
    NotionClient,
    PermanentIntegrationError,
    SlackClient,
    TransientIntegrationError,
    refresh_access_token,
)
from autune_integrations.errors import SlackRecipientNotLinkedError

from . import calendar_sync, jira_sync, notion_backfill, notion_setup, service
from .confirmations import build_confirmation_dm
from .models import (
    ExtActionItem,
    ExtCalendarCleanup,
    ExtCalendarEvent,
    ExtCalendarPoll,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtExternalRef,
)
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


def _follow_corrections(corrections: service.SourceCorrections) -> None:
    """Queue the outside copies of corrected rows that have them (#586, #657):
    Notion, Jira and the calendar for an item, Notion for a decision. A failure to queue
    is logged: the rows are already right, and the next edit sends them."""
    try:
        for action_item_id in corrections.changed_items:
            sync_action_item.delay(action_item_id)
            sync_action_item_jira.delay(action_item_id)
            sync_action_item_calendar.delay(action_item_id)
        for decision_id in corrections.changed_decisions:
            sync_decision.delay(decision_id)
    except Exception as exc:  # noqa: BLE001 -- the correction itself is committed
        log.warning("extraction_corrections_not_queued", error=type(exc).__name__)
    if corrections.changed_items or corrections.changed_decisions or corrections.flagged:
        log.info(
            "extraction_sources_corrected",
            items=len(corrections.changed_items),
            decisions=len(corrections.changed_decisions),
            flagged=corrections.flagged,
        )


def _extract(meeting_id: str, utterances: Sequence[TranscriptUtterance]) -> None:
    """Everything ``on_transcript_ready`` does after the payload is checked;
    ``reextract_consent_changes`` runs it too, on the stored transcript."""
    with session_scope() as session:
        consented = service.consented_utterance_ids(session, meeting_id)
        roster = service.team_roster(session, meeting_id)
        day = service.decision_day(session, meeting_id)
        confirmed = service.confirmed_commitment_ids(session, meeting_id)

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
    # Agreements their speakers confirmed keep a summary through the rebuild.
    confirmed_summaries = service.confirmed_summaries(resolver, classified, confirmed)
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
            confirmed=confirmed_summaries,
        )
        ambiguous = service.record_ambiguous_agreements(
            session, meeting_id=meeting_id, classified=classified
        )
        # After the rebuild, so what it rebuilt already matches: what was drawn
        # from a line corrected since -- kept because a person edited the
        # meeting, or written by a person -- is fixed or flagged (#586).
        corrections = service.apply_source_corrections(
            session, meeting_id=meeting_id, spoken={u.id: u.text for u in utterances}
        )
        # A DM that quotes a line corrected since it went out (#586).
        stale_dms = service.dms_to_correct(
            session, meeting_id=meeting_id, spoken={u.id: u.text for u in utterances}
        )
        # Pages of decisions this run dropped: their refs are kept so the
        # pages can be retired, not left live in Notion (#669).
        orphaned_pages = service.decision_pages_without_a_decision(session, meeting_id)
        # With the rows it describes: a rollback takes both (#518).
        service.record_extraction(session, meeting_id=meeting_id, consented=consented)
        result = service.result_for_meeting(session, meeting_id)

    _follow_corrections(corrections)
    for utterance_id in stale_dms:
        try:
            update_confirmation_dm.delay(utterance_id)
        except Exception as exc:  # noqa: BLE001 -- queuing only; the next run finds it again
            log.warning("extraction_dm_correction_not_queued", error=type(exc).__name__)
    for decision_id in orphaned_pages:
        try:
            sync_decision.delay(decision_id)
        except Exception as exc:  # noqa: BLE001 -- queuing only; the Notion backfill sweeps it
            log.warning("extraction_decision_page_retire_not_queued", error=type(exc).__name__)
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
                    answer_url=service.answer_url(question.meeting_id),
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


@shared_task(name="autune.extraction.summarise_confirmed_draft", acks_late=True)
def summarise_confirmed_draft(utterance_id: str) -> None:
    """After a speaker answers "약속입니다": write the summary of what they
    agreed to onto the draft their answer made (decided with the user,
    2026-10-01 -- the DM shows only their line; the summary appears on the
    board once they confirm).

    Only now, and only for this one: most ambiguous agreements are never
    confirmed, so none of them costs a model call before an answer. The window
    and the rules are a commitment's (``resolve_commitment_summaries``); the
    model runs outside any session. Nothing happens if the answer changed or a
    person touched the draft in the meantime (``apply_confirmed_summary``), so
    a redelivery is harmless. A privacy refusal from the resolver is raised.
    """
    with session_scope() as session:
        window = service.confirmed_draft_window(session, utterance_id)
        said = session.get(Utterance, utterance_id)
        roster = service.team_roster(session, said.meeting_id) if said is not None else []
    if window is None:
        return
    resolver = get_resolver()
    give_roster(resolver, roster)
    resolution = service.resolve_commitment_summaries(resolver, window)[utterance_id]
    with session_scope() as session:
        applied = service.apply_confirmed_summary(session, utterance_id, resolution)
    # Ids only. The summary is meeting content.
    log.info("extraction_confirmed_draft_summarised", utterance_id=utterance_id, applied=applied)


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
    Google client it was issued to -- core's ``google_integration_credentials``:
    the deployment's integration client when it has one, the sign-in client
    otherwise (#425). A deployment with neither has nobody connected as far as
    this is concerned. A refused refresh token raises ``ReconnectRequiredError`` -- an
    ``IntegrationError`` -- for the caller to handle.
    """
    client_id, client_secret = get_core_settings().google_integration_credentials
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
    Jira (``jira_sync.read_back``): an issue dragged to Done is a done item on
    the board. One team at a time, each with its own access token, and one
    transaction per issue, so one team's lapsed connection stops nobody else's.

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
    """One team's read-back, each issue in its own transaction: no row stays
    locked across another issue's request, and a failure part-way keeps what
    was read before it. The items moved so far are returned even then, for
    Notion to follow; the failure is logged here, by type."""
    access = jira_access(team_id)
    if access is None:
        return []
    moved: list[str] = []
    client = JiraClient.for_cloud(access.access_token, access.cloud_id)
    try:
        with session_scope() as session:
            targets = jira_sync.pull_candidates(session, team_id=team_id, site=access.cloud_id)
        for item_id, key in targets:
            with session_scope() as session:
                if jira_sync.read_back(
                    session, client, item_id=item_id, key=key, site=access.cloud_id
                ):
                    moved.append(item_id)
    except Exception as exc:  # noqa: BLE001 -- one team's failure is theirs alone
        log.warning("extraction_jira_pull_failed", team_id=team_id, error=type(exc).__name__)
    finally:
        client.close()
    return moved


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


@shared_task(name="autune.extraction.backfill_notion", acks_late=True)
def backfill_notion(team_id: str) -> None:
    """After a one-click Notion setup (``notion_connect.set_up``): every confirmed
    action item and decision of the team into the databases just recorded.

    Out of the setup request because Notion takes about three requests a
    second, and a team with hundreds of confirmed rows would outlast it (#481).
    Safe to run again -- a redelivery after a lost worker included: each row
    goes through the same claim-then-call sync a live confirmation uses, and
    one row's failure, a privacy block among them, costs only that row
    (``notion_backfill``). Logs counts only."""
    items = notion_backfill.Stats()
    notion_backfill.backfill_action_items(notion_backfill._confirmed_action_items(team_id), items)
    decisions = notion_backfill.Stats()
    # The confirmed ones get their pages; then the other way round, pages of
    # decisions no longer confirmed, or deleted, whose one retire after the
    # change did not get through (#669).
    notion_backfill.backfill_decisions(
        notion_backfill._confirmed_decisions(team_id)
        + notion_backfill._decision_pages_to_retire(team_id),
        decisions,
    )
    log.info(
        "extraction_notion_backfilled",
        team_id=team_id,
        items_sent=items.sent,
        items_replaced=items.replaced,
        items_failed=items.failed,
        decisions_sent=decisions.sent,
        decisions_retired=decisions.retired,
        decisions_failed=decisions.failed,
    )


@shared_task(name="autune.extraction.periodic.retire_decision_pages")
@periodic(timedelta(minutes=10))
def retire_decision_pages() -> int:
    """Take out of Notion the decision pages that should no longer be there,
    on a timer (#683). Returns how many were retired.

    A decision's page is retired once, right after the decision stops being
    confirmed, is deleted, or is dropped by a rerun (#669), and a failure of
    that one call is only logged. A decision that still exists is tried
    again at its next change; one that is gone has no next change, and its
    page stayed live with the statement until somebody ran the Notion
    backfill. This is that second try with nobody doing anything: the same
    list (``notion_backfill._decision_pages_to_retire``, every team) through
    the same sync, so a decision whose id came back unconfirmed is covered
    too.

    A tick with nothing to retire reads the database and calls Notion not
    at all. A page whose team has no Notion connection cannot be reached and
    is counted, not failed; it is listed again next time, which costs one
    read. One row's failure costs that row when it is an ``IntegrationError``
    or a ``PrivacyViolationError``, the two ``backfill_decisions`` catches; any
    other exception ends this tick, and the next one starts the list over.
    Ids and counts only in the log, and ``retired`` counts only what this run
    retitled and trashed -- a page found archived or deleted is counted as
    that.

    Not covered, because nothing records them: a *changed* page whose update
    failed (retried at the row's next change or by the backfill), and the
    page or issue of an action item a person deleted when
    ``trash_notion_page`` / ``close_jira_issue`` could not get through --
    the item's row is gone by then. Both need a record of what is owed,
    which is a table, not this task.
    """
    rows = notion_backfill._decision_pages_to_retire(None)
    if not rows:
        return 0
    stats = notion_backfill.Stats()
    notion_backfill.backfill_decisions(rows, stats)
    log.info(
        "extraction_decision_pages_retired",
        listed=len(rows),
        retired=stats.retired,
        archived=stats.archived,
        gone=stats.gone,
        failed=stats.failed,
        not_connected=stats.not_connected,
    )
    return stats.retired


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


CLEANUP_BATCH = 100
"""How many queued calendar events one ``drain_calendar_cleanup`` run takes."""

CLEANUP_MAX_ATTEMPTS = 5
"""Transient failures before a queued event is given up on and logged."""


@on_user_deleted("extraction")
def forget_user_calendar_events(user_id: str) -> None:
    """Before an account goes (#582, #588): every due-date event B put on that
    person's own calendar, removed now.

    Now, not queued: the events can only be removed with the person's own
    Google grant (``user_integrations``), and that row goes with ``users``
    right after this hook. Events already queued for them by a meeting's
    expiry are removed here too, for the same reason. Never raises -- an
    expired token or an unreachable Google is logged and the deletion goes on,
    as #582 requires; the ``users`` cascade then takes B's rows either way.
    Safe to run twice. Logs ids and counts only.
    """
    try:
        with session_scope() as session, _calendars(session) as calendar_for:
            events = list(
                session.scalars(select(ExtCalendarEvent).where(ExtCalendarEvent.user_id == user_id))
            )
            queued = list(
                session.scalars(
                    select(ExtCalendarCleanup).where(ExtCalendarCleanup.user_id == user_id)
                )
            )
            ids = [e.event_id for e in events if e.event_id] + [q.event_id for q in queued]
            removed, failed = _remove_events(calendar_for, user_id, ids)
            for row in [*events, *queued]:
                session.delete(row)
        if failed:
            # Best effort (privacy.md section 4): the account goes on, and these
            # events stay on that calendar. Loud, because nothing can retry.
            log.warning(
                "extraction_user_calendar_events_left",
                user_id=user_id,
                failed=failed,
                client_configured=_google_client_configured(),
            )
        log.info(
            "extraction_user_calendar_events_removed",
            user_id=user_id,
            removed=removed,
            failed=failed,
        )
    except PrivacyViolationError:
        raise  # nothing here sends content, so this would be a real defect
    except Exception as exc:  # noqa: BLE001 -- an account deletion must not stop on this
        log.warning(
            "extraction_user_calendar_events_failed", user_id=user_id, error=type(exc).__name__
        )


def _google_client_configured() -> bool:
    """Whether this deployment can refresh anyone's Google grant at all."""
    return all(get_core_settings().google_integration_credentials)


def _remove_events(
    calendar_for: calendar_sync.CalendarFor, user_id: str, event_ids: Sequence[str]
) -> tuple[int, int]:
    """Remove ``event_ids`` from ``user_id``'s calendar; (removed, failed).
    An event already gone counts as removed (``CalendarClient.delete_event``)."""
    if not event_ids:
        return 0, 0
    try:
        connection = calendar_for(user_id)
    except IntegrationError:
        return 0, len(event_ids)
    if connection is None:
        return 0, len(event_ids)
    client, calendar_id = connection
    removed = failed = 0
    for event_id in event_ids:
        try:
            client.delete_event(calendar_id, event_id)
            removed += 1
        except IntegrationError:
            failed += 1
    return removed, failed


@on_meeting_deleted("extraction")
def queue_meeting_calendar_events(meeting_id: str) -> None:
    """Before an expired meeting goes (#581, #588): its items' due-date events,
    copied into ``ext_calendar_cleanup`` for ``drain_calendar_cleanup``.

    Only a copy -- no call to Google here. The hook runs once per meeting
    inside the retention sweep, and a slow or failing calendar must not hold
    the sweep up. A database error is raised on purpose: the sweep then keeps
    the meeting and tries again, rather than deleting it with its events
    unrecorded. Safe to run twice (the queue is unique per user and event).
    """
    with session_scope() as session:
        rows = session.execute(
            select(ExtCalendarEvent.user_id, ExtCalendarEvent.event_id).where(
                ExtCalendarEvent.meeting_id == meeting_id,
                ExtCalendarEvent.event_id.is_not(None),
            )
        ).all()
        if rows:
            session.execute(
                service._insert_if_absent_into(session, ExtCalendarCleanup)
                .values([{"user_id": user, "event_id": event} for user, event in rows])
                .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
            )
    log.info("extraction_meeting_calendar_events_queued", meeting_id=meeting_id, events=len(rows))


@shared_task(name="autune.extraction.periodic.drain_calendar_cleanup")
@periodic(timedelta(minutes=10))
def drain_calendar_cleanup() -> int:
    """Take queued due-date events off their owners' calendars (#588).

    Each goes with its owner's own grant. Removed, or already gone: the row
    goes. A transient failure keeps it for the next run, up to
    ``CLEANUP_MAX_ATTEMPTS``. Nothing can remove it -- the owner disconnected
    their calendar, or the grant is refused -- and the row goes too, logged:
    keeping it would retry forever. Returns how many were removed.
    """
    if not _google_client_configured():
        # Without the deployment's Google client no grant can be refreshed, and
        # every row would read as "no grant" and be dropped. Keep them for when
        # it is configured, and say so loudly (lsh2217, review of #595).
        log.warning("extraction_calendar_cleanup_no_client")
        return 0
    removed = 0
    with session_scope() as session, _calendars(session) as calendar_for:
        rows = list(
            session.scalars(
                select(ExtCalendarCleanup).order_by(ExtCalendarCleanup.id).limit(CLEANUP_BATCH)
            )
        )
        for row in rows:
            try:
                connection = calendar_for(row.user_id)
                if connection is None:
                    log.info("extraction_calendar_cleanup_no_grant", user_id=row.user_id)
                    session.delete(row)
                    continue
                client, calendar_id = connection
                client.delete_event(calendar_id, row.event_id)
                session.delete(row)
                removed += 1
            except TransientIntegrationError:
                row.attempts += 1
                if row.attempts >= CLEANUP_MAX_ATTEMPTS:
                    log.warning("extraction_calendar_cleanup_gave_up", user_id=row.user_id)
                    session.delete(row)
            except IntegrationError as exc:
                log.warning(
                    "extraction_calendar_cleanup_refused",
                    user_id=row.user_id,
                    error=type(exc).__name__,
                )
                session.delete(row)
            except Exception as exc:  # noqa: BLE001 -- one row must not block the queue
                # Counted like a transient failure: left uncaught it would roll
                # back the batch, and the row, first by id, would block every
                # run after it (mminjae97, review of #595).
                row.attempts += 1
                log.warning(
                    "extraction_calendar_cleanup_failed",
                    user_id=row.user_id,
                    error=type(exc).__name__,
                )
                if row.attempts >= CLEANUP_MAX_ATTEMPTS:
                    session.delete(row)
    log.info("extraction_calendar_cleanup_drained", taken=len(rows), removed=removed)
    return removed


@shared_task(name="autune.extraction.sync_decision", acks_late=True)
def sync_decision(decision_id: str) -> None:
    """Step 7 for one decision a person just confirmed: its Notion page, once.
    And for one that stopped being confirmed, or was deleted, while it had a
    page: that page is retired (``service.sync_decision_to_notion``, #669).

    ``sync_action_item``'s rules, for the team's decision database
    (``decision_db_id`` in its Notion config). A team that connected Notion for
    action items only has no ``decision_db_id``, and is skipped rather than failed.
    No self-retry, for the reason ``sync_action_item`` gives.
    """
    with session_scope() as session:
        decision = session.get(ExtDecision, decision_id)
        # A deleted decision is found through its ref: the row outlives it, and
        # names the meeting whose team's Notion holds the page to retire.
        if decision is not None:
            meeting_id: str | None = decision.meeting_id
        else:
            ref = session.get(ExtDecisionRef, (decision_id, "notion"))
            meeting_id = ref.meeting_id if ref is not None else None
        meeting = session.get(Meeting, meeting_id) if meeting_id else None
        if meeting is None:
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


@shared_task(name="autune.extraction.update_confirmation_dm", acks_late=True)
def update_confirmation_dm(utterance_id: str) -> bool:
    """Carry a corrected line into the confirmation DM that quoted it (#586).

    A PII report masks a stored line again; the DM sent before it still quotes
    the old text. This rebuilds the DM from the line as stored now and replaces
    the message in place (``chat.update``) through the team's Slack bot, the
    same outbound check as any send reading the new blocks. Only a DM whose
    place was kept can be corrected -- one sent before that cannot. A Slack
    failure is logged and the next run finds the DM again; a privacy refusal is
    raised, never swallowed. Returns whether the DM was updated.
    """
    with session_scope() as session:
        row = session.get(ExtConfirmation, utterance_id)
        said = session.get(Utterance, utterance_id)
        meeting = session.get(Meeting, row.meeting_id) if row is not None else None
        if row is None or said is None or meeting is None or not row.dm_channel or not row.dm_ts:
            return False
        config = load_integration(session, meeting.team_id, "slack")
        if config is None or not config.secret:
            return False
        text, blocks = build_confirmation_dm(
            utterance_id=utterance_id,
            quoted_text=said.text,
            answer_url=service.answer_url(row.meeting_id),
            buttons=get_core_settings().slack_buttons,
        )
        try:
            SlackClient(config.require_secret()).update_message(
                row.dm_channel, row.dm_ts, text, blocks
            )
        except IntegrationError as exc:
            log.warning(
                "extraction_dm_correction_failed",
                utterance_id=utterance_id,
                error=type(exc).__name__,
            )
            return False
        row.dm_digest = service.source_digest([said.text])
    log.info("extraction_dm_corrected", utterance_id=utterance_id)
    return True


@on_speech_deleted("extraction")
def forget_deleted_speech(user_id: str, utterance_ids: Sequence[str]) -> None:
    """Before a person's own speech is deleted (#582, #587): the items and
    decisions drawn from it keep the work and drop the words
    (``service.forget_speech``), and their copies in Notion, Jira and the
    calendar are queued to follow -- a confirmed row's, and those of an item
    moved back to 확인 필요 that still has them (#657).

    The database part raises on failure, so A's deletion stops rather than
    leaving the words behind in B. The copies outside are queued after the
    commit and a failure to queue is only logged: the person's speech must not
    stay because a broker was down. Safe to repeat. Ids and counts only.

    B commits before A deletes the utterances, in its own transaction: if A's
    deletion then fails, B has already dropped the words. That errs toward
    deleting more, which is the side to err on (mkkim68, review of #601).
    """
    with session_scope() as session:
        done = service.forget_speech(session, utterance_ids)
    try:
        for action_item_id in done.changed_items:
            sync_action_item.delay(action_item_id)
            sync_action_item_jira.delay(action_item_id)
            sync_action_item_calendar.delay(action_item_id)
        for decision_id in done.changed_decisions:
            sync_decision.delay(decision_id)
    except Exception as exc:  # noqa: BLE001 -- the deletion must go on; the rows changed
        log.warning(
            "extraction_speech_resync_not_queued", user_id=user_id, error=type(exc).__name__
        )
    log.info(
        "extraction_speech_forgotten",
        user_id=user_id,
        utterances=len(utterance_ids),
        drafts_deleted=len(done.deleted_items),
        items_changed=len(done.changed_items),
        decisions_changed=len(done.changed_decisions),
    )


@shared_task(name="autune.extraction.periodic.publish_action_progress")
@periodic(ACTION_PROGRESS_PUBLISH_EVERY)
def publish_action_progress() -> None:
    """Every ten minutes, each team's action items as counts per meeting -- a
    ``TeamActionProgress`` for E's real completion rate (#605).

    A snapshot republished whether or not anything changed, like
    ``publish_team_agendas`` and for its reasons: the board's edits run in the
    API process, which has no Celery app to publish from, and the next snapshot
    corrects a lost one. A team with a meeting in the window but nothing
    confirmed gets an empty snapshot, which E reads as a fact. Only counts are
    logged.
    """
    now = datetime.now(UTC)
    with session_scope() as session:
        snapshots = [
            service.team_action_progress(session, team_id, now=now)
            for team_id in service.teams_with_recent_meetings(session, now=now)
        ]
    for snapshot in snapshots:
        publish(EXTRACTION_ACTION_PROGRESS, snapshot.model_dump(mode="json"))
        log.info(
            "extraction_action_progress_published",
            team_id=snapshot.team_id,
            meetings=len(snapshot.meetings),
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
