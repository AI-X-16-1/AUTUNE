"""Celery tasks for module B.

Discovered automatically by apps/worker. Tasks parse, delegate to ``service``,
and publish. Every task must be safe to run twice — see
docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from celery import shared_task

from autune_contracts import EXTRACTION_COMPLETED, TranscriptReady, validate_major_version
from autune_core import (
    Meeting,
    PrivacyViolationError,
    get_logger,
    load_integration,
    publish,
    session_scope,
)
from autune_integrations import IntegrationError, NotionClient

from . import service
from .models import ExtActionItem, ExtDecision
from .pipeline.registry import get_classifier, get_nli

log = get_logger(__name__)


@shared_task(name="autune.extraction.on_transcript_ready", acks_late=True)
def on_transcript_ready(payload: dict) -> None:
    """Consume TranscriptReady from module A: classify, then write what it found.

    Decisions grouped, draft items for commitments, and a record of every
    ambiguous agreement -- not yet asked about, because nothing can send the DM
    (#70, #30).

    ``shared_task`` binds to whichever Celery app is running, so this module
    never imports apps/worker.

    Classification, then NLI, happen between two sessions, never inside one --
    both are model inference, and a transaction held around them holds a
    connection and its locks for all of that time (``service.classify_utterances``,
    ``service.verify_utterances``). The first session only reads which
    utterances belong to a speaker who consented (privacy.md section 5);
    nobody else's speech reaches either model. The writes that follow share
    one transaction: classifications, decisions and draft items all come from
    the same NLI-verified predictions, and a meeting holding one run's labels
    and another run's items is not a state anything downstream should be able
    to read.

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
