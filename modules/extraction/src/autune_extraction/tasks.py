"""Celery tasks for module B.

Discovered automatically by apps/worker. Tasks parse, delegate to ``service``,
and publish. Every task must be safe to run twice — see
docs/architecture/async-pipeline.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
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
from autune_core.integrations_config import IntegrationConfig
from autune_core.jira_connection import JiraAccess
from autune_core.oauth.atlassian import JiraReconnectRequiredError
from autune_core.settings import get_settings as get_core_settings
from autune_integrations import (
    CalendarClient,
    IntegrationError,
    JiraClient,
    NotionClient,
    PermanentIntegrationError,
    ReconnectRequiredError,
    SlackClient,
    TransientIntegrationError,
    refresh_access_token,
)
from autune_integrations.errors import SlackRecipientNotLinkedError

from . import (
    calendar_sync,
    jira_sync,
    notion_backfill,
    notion_setup,
    project_send,
    projects,
    service,
    sync_state,
)
from .config import get_settings, require_loadable
from .confirmations import build_confirmation_dm
from .models import (
    ExtActionItem,
    ExtCalendarCleanup,
    ExtCalendarEvent,
    ExtCalendarPoll,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtExternalCleanup,
    ExtExternalRef,
    ExtProjectRefreshOwed,
    ExtProjectSendCleanup,
)
from .pipeline.base import give_roster
from .pipeline.registry import get_classifier, get_nli, get_resolver, get_summarizer

log = get_logger(__name__)

# Before anything of module B is served or run: a configuration B refuses
# (an unacknowledged cloud model, #392) stops the process that imports this,
# instead of surfacing on the first request or the first meeting. The worker
# imports this module; the API reaches it through ``router``, which imports it
# too -- one call, and a test for each of the two ways in.
require_loadable()


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
    Notion, Jira and the calendar for an item, Notion for a decision. The
    project minutes that carried them are rewritten by the run itself, once,
    at its end (``_extract``). A failure to queue
    is logged: the rows are already right, and the next edit sends them."""
    try:
        for action_item_id in corrections.changed_items:
            sync_item_copies.delay(action_item_id)
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
        # A written summary of lines this run no longer reads -- corrected,
        # re-masked, their speaker's consent withdrawn -- goes with the run
        # that noticed, not when a new one happens to replace it. Here and not
        # only in ``summarize_meeting``: that task is not queued at all with
        # ``summary_impl=none``, and may fail (#782 review).
        service.drop_stale_summary(session, meeting_id)
        # Which of the team's projects each row is about, by what was said.
        # Rows a person placed keep their project.
        projects.assign_meeting(session, meeting_id)
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
    # Its own task, so a provider that is down costs the 요약 tab its paragraph
    # and never this run its rows (#421 v2). Only when a summarizer is on --
    # asked of the setting, not by building the summarizer: one switched on
    # without a key raises there, and this run's rows are already committed
    # (#782 review). The task is where that is found and logged.
    if get_settings().summary_impl != "none":
        try:
            summarize_meeting.delay(meeting_id)
        except Exception as exc:  # noqa: BLE001 -- queuing only; the next run asks again
            log.warning("extraction_summary_not_queued", error=type(exc).__name__)
    # The project minutes that already went out, brought in line with what
    # this run left confirmed: a corrected line, and also a confirmed decision
    # the rebuild no longer has, which no correction names (#787 review). A
    # meeting that sent nothing costs one query; a copy that already says the
    # minutes is not written to.
    refresh_project_minutes(meeting_id)


@shared_task(name="autune.extraction.summarize_meeting", acks_late=True)
def summarize_meeting(meeting_id: str) -> bool:
    """The 요약 tab's written summary (#421 v2): the meeting's consented lines to
    ``summary_impl``'s model, the answer stored with the digest of those lines.

    Skipped when no summarizer is on, when the meeting has no lines, and when
    the stored summary was written from exactly these lines -- a re-extraction
    that changed no line does not ask again. Reads, then calls the model with no
    session open, then writes: a transaction is never held across a call that
    takes seconds. A failed or unusable answer leaves the tab as v1 built it and
    is logged by meeting id; nothing about it fails the meeting.

    A stored summary of other lines than the meeting has now is deleted before
    the model is asked, so none of the ways this can end without a new summary
    -- no lines left, no summarizer, a failed call, an unusable answer -- leaves
    the old one in the table. And the answer is stored only if the lines are
    still the ones it was written from (``service.store_meeting_summary``):
    speech deleted while the model was answering is not written back.

    Returns whether a summary was written.
    """
    try:
        summarizer = get_summarizer()
    except ValueError as exc:
        # Switched on without what it needs (a key). Said here, by name of the
        # error only; the stale summary below still goes.
        log.error("extraction_summary_not_configured", error=type(exc).__name__)
        summarizer = None
    with session_scope() as session:
        lines = service.summary_lines(session, meeting_id)
        if service.summary_is_current(session, meeting_id, lines):
            return False
        service.drop_stale_summary(session, meeting_id, lines)
        if summarizer is None or not lines:
            return False
        roster = service.team_roster(session, meeting_id)
        board = service.summary_board(session, meeting_id)
    give_roster(summarizer, roster)
    try:
        written = summarizer.summarize(lines, board=board)
    except PrivacyViolationError:
        log.warning("extraction_summary_blocked_by_privacy_guard", meeting_id=meeting_id)
        return False
    except Exception as exc:  # noqa: BLE001 -- the tab keeps v1; logged by id, never the text
        log.warning("extraction_summary_failed", meeting_id=meeting_id, error=type(exc).__name__)
        return False
    if written is None:
        return False
    with session_scope() as session:
        # Written from the lines read above. If they changed while the model
        # was answering -- a line corrected, or deleted by its speaker -- it is
        # not stored; the run that follows the change asks again.
        stored = service.store_meeting_summary(
            session,
            meeting_id,
            overview=written.overview,
            points=written.points,
            model_version=written.model_version,
            lines=lines,
        )
    if stored is None:
        log.info("extraction_summary_discarded_lines_changed", meeting_id=meeting_id)
        return False
    log.info("extraction_summary_stored", meeting_id=meeting_id, points=len(written.points))
    return True


def _project_clients(
    session: Session, team_id: str, targets: Sequence[str]
) -> project_send.Clients:
    """The team's tools for its project minutes, each only when chosen and
    connected. Jira's access is asked for only when Jira was chosen, since it
    may refresh a token, and with its project checked: a deleted project is
    not written to."""
    clients = project_send.Clients()
    if "notion" in targets:
        notion = load_integration(session, team_id, "notion")
        database_id = notion_setup.database_id(session, team_id, notion, "minutes_db_id")
        if notion is not None and notion.secret and database_id:
            clients.notion = (NotionClient(notion.secret), database_id)
    if "slack" in targets:
        slack = load_integration(session, team_id, "slack")
        channel = slack.config.get("channel") if slack is not None else None
        if slack is not None and slack.secret and channel:
            clients.slack = (SlackClient(slack.secret), str(channel))
    if "jira" in targets:
        try:
            access = jira_access(team_id, check_project=True)
        except AutuneError:
            access = None  # a refused grant: reported as not connected
        if access is not None:
            clients.jira = (
                JiraClient.for_cloud(access.access_token, access.cloud_id),
                access.project_key,
            )
    return clients


def _close(clients: project_send.Clients) -> None:
    for pair in (clients.notion, clients.slack, clients.jira):
        if pair is not None:
            pair[0].close()


def send_project_minutes(
    session: Session, meeting_id: str, targets: Sequence[str]
) -> tuple[list[project_send.Sent], int]:
    """The 요약 tab's "프로젝트별로 보내기" (2026-10-04): the team's tools built
    here, where every client is, and the minutes sent by ``project_send``. Runs
    in the request -- a person pressed the button and waits to see what went.
    A tool the team has not connected is reported as such, not tried."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return [], 0
    clients = _project_clients(session, meeting.team_id, targets)
    try:
        return project_send.send(session, meeting_id, targets, clients)
    finally:
        _close(clients)


def refresh_project_minutes(meeting_id: str) -> bool:
    """The copies of a meeting's project minutes, brought in line with what is
    confirmed now (``project_send.refresh``): after speech was deleted, a
    decision taken back, an item deleted or edited, a line masked again.

    Called directly, in whatever process made the change -- the API has no
    Celery app to queue on -- and never raising: the change is committed
    already and must not fail on a tool. A meeting that sent nothing costs one
    query. Ids and counts only in the log.

    Not best effort, though (#787 review). When any copy is left behind -- its
    tool failed, or is not connected -- or the refresh itself broke, the
    meeting is recorded in ``ext_project_refresh_owed`` and
    ``retry_project_minutes_refresh`` comes back to it; when every copy is in
    line the record goes. Returns whether every copy is in line now."""
    try:
        with session_scope() as session:
            targets = project_send.sent_targets(session, meeting_id)
            team_id = project_send.meeting_team(session, meeting_id)
            if not targets or team_id is None:
                project_send.settle_refresh(session, meeting_id)
                return True
            clients = _project_clients(session, team_id, targets)
            try:
                sent = project_send.refresh(session, meeting_id, clients)
            finally:
                _close(clients)
            if project_send.in_line(sent):
                project_send.settle_refresh(session, meeting_id)
                return True
            project_send.owe_refresh(session, [meeting_id])
            log.warning(
                "extraction_project_minutes_refresh_owed",
                meeting_id=meeting_id,
                behind=sum(1 for s in sent if s.outcome in ("failed", "not_connected")),
            )
            return False
    except Exception as exc:  # noqa: BLE001 -- the change itself is committed
        log.warning(
            "extraction_project_minutes_refresh_failed",
            meeting_id=meeting_id,
            error=type(exc).__name__,
        )
    # The refresh broke before it could say what it left behind, and its
    # session rolled back with it: the record is made in one of its own.
    try:
        with session_scope() as session:
            if project_send.meeting_team(session, meeting_id) is not None:
                project_send.owe_refresh(session, [meeting_id])
    except Exception as exc:  # noqa: BLE001 -- nothing left to fall back on but the log
        log.error(
            "extraction_project_minutes_refresh_not_recorded",
            meeting_id=meeting_id,
            error=type(exc).__name__,
        )
    return False


@shared_task(name="autune.extraction.refresh_project_minutes", acks_late=True)
def refresh_project_minutes_queued(meeting_id: str) -> bool:
    """``refresh_project_minutes`` on a worker, for a caller that must not wait
    on Notion, Slack and Jira -- a person deleting their own speech
    (``forget_deleted_speech``)."""
    return refresh_project_minutes(meeting_id)


PROJECT_REFRESH_BATCH = 50
"""How many owed meetings one ``retry_project_minutes_refresh`` run takes."""

PROJECT_REFRESH_MAX_ATTEMPTS = 144
"""Retries before an owed refresh is given up on: a day of them, ten minutes
apart. Longer than ``CLEANUP_MAX_ATTEMPTS`` on purpose. What is owed here can be
a sentence its speaker deleted, a tool is more often down for an afternoon
than for good, and a retry asks nothing of the copies already in line. Giving
up loses only the retrying: the copy's digest still says it is behind, so the
next change to the meeting rewrites it."""


@shared_task(name="autune.extraction.periodic.retry_project_minutes_refresh")
@periodic(timedelta(minutes=10))
def retry_project_minutes_refresh() -> int:
    """Refresh again the project minutes a refresh left behind (#787 review).
    Returns how many meetings are in line now.

    One meeting at a time, each in ``refresh_project_minutes``' own
    transaction. In line: its record is gone. Still behind: the count goes up,
    and at ``PROJECT_REFRESH_MAX_ATTEMPTS`` it is given up on, said loudly.
    Ids and counts only."""
    with session_scope() as session:
        owed = list(
            session.scalars(
                select(ExtProjectRefreshOwed.meeting_id)
                .order_by(ExtProjectRefreshOwed.created_at, ExtProjectRefreshOwed.meeting_id)
                .limit(PROJECT_REFRESH_BATCH)
            )
        )
    done = 0
    for meeting_id in owed:
        if refresh_project_minutes(meeting_id):
            done += 1
            continue
        with session_scope() as session:
            row = session.get(ExtProjectRefreshOwed, meeting_id)
            if row is None:
                continue
            row.attempts += 1
            if row.attempts >= PROJECT_REFRESH_MAX_ATTEMPTS:
                log.error(
                    "extraction_project_minutes_refresh_given_up",
                    meeting_id=meeting_id,
                    attempts=row.attempts,
                )
                session.delete(row)
    log.info("extraction_project_minutes_refresh_retried", owed=len(owed), in_line=done)
    return done


def _refresh_minutes_for(item_ids: Iterable[str] = (), decision_ids: Iterable[str] = ()) -> None:
    """``refresh_project_minutes`` for the meetings of these rows that sent
    minutes. Best effort, like it. No rows, no query."""
    items, decisions = list(item_ids), list(decision_ids)
    if not items and not decisions:
        return
    try:
        with session_scope() as session:
            meetings = project_send.meetings_with_sends(session, items, decisions)
    except Exception as exc:  # noqa: BLE001 -- the change itself is committed
        log.warning("extraction_project_minutes_refresh_failed", error=type(exc).__name__)
        return
    for meeting_id in sorted(meetings):
        refresh_project_minutes(meeting_id)


PROJECT_CLEANUP_BATCH = 100
"""How many queued minutes copies one ``drain_project_send_cleanup`` run takes."""


@shared_task(name="autune.extraction.periodic.drain_project_send_cleanup")
@periodic(timedelta(minutes=10))
def drain_project_send_cleanup() -> int:
    """Retract the minutes copies whose meeting or project was deleted (#787
    review), with each team's own connection. Returns how many went.

    Retracted, or already gone: the row goes. A failure, or a tool no longer
    connected, keeps it for the next run, up to ``CLEANUP_MAX_ATTEMPTS``; then
    it is given up on, said loudly -- a person can still remove the copy in the
    tool. Ids and counts only."""
    done = 0
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ExtProjectSendCleanup)
                .order_by(ExtProjectSendCleanup.id)
                .limit(PROJECT_CLEANUP_BATCH)
            )
        )
        by_team: dict[str, list[ExtProjectSendCleanup]] = {}
        for row in rows:
            by_team.setdefault(row.team_id, []).append(row)
        for team_id, owed in by_team.items():
            clients = _project_clients(session, team_id, sorted({r.target for r in owed}))
            try:
                for row in owed:
                    try:
                        outcome = project_send.retract(row.target, row.external_id, clients)
                    except Exception as exc:  # noqa: BLE001 -- one copy costs that copy
                        log.warning(
                            "extraction_project_copy_retract_failed",
                            team_id=team_id,
                            target=row.target,
                            error=type(exc).__name__,
                        )
                        outcome = "failed"
                    if outcome in ("retracted", "gone"):
                        session.delete(row)
                        done += 1
                        continue
                    row.attempts += 1
                    if row.attempts >= CLEANUP_MAX_ATTEMPTS:
                        log.error(
                            "extraction_project_copy_given_up",
                            team_id=team_id,
                            target=row.target,
                            reason=outcome,
                        )
                        session.delete(row)
            finally:
                _close(clients)
    log.info("extraction_project_copies_retracted", listed=len(rows), retracted=done)
    return done


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


@shared_task(name="autune.extraction.periodic.send_weekly_digests")
@periodic(timedelta(minutes=10))
def send_weekly_digests() -> list[str]:
    """Monday's digest of each person's own open items, by Slack DM to that
    person alone (the user, 2026-10-04; ``service.weekly_digests_to_send``).
    Returns the user ids a digest went to.

    The shape of ``remind_due_items``: each digest claimed and sent in its own
    transaction, a team without Slack or a person without a linked account
    skipped and looked at again next run, an unexpected error that one
    digest's, and a privacy refusal never swallowed -- raised after the rest
    are sent. Every ten minutes; outside a Monday's sending hours in Korea it
    finds nothing owed.
    """
    if not get_settings().weekly_digest:
        return []
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        owed = service.weekly_digests_to_send(session, now=now)
        secrets: dict[str, str | None] = {}
        for team_id in sorted({d.team_id for d in owed}):
            config = load_integration(session, team_id, "slack")
            secrets[team_id] = config.require_secret() if config is not None else None

    sent: list[str] = []
    refused: list[str] = []
    not_linked = 0
    for digest in owed:
        secret = secrets[digest.team_id]
        if secret is None:
            continue
        try:
            with session_scope() as session:
                went = service.send_weekly_digest(session, SlackClient(secret), digest, now=now)
        except PrivacyViolationError:
            refused.append(digest.user_id)
            continue
        except SlackRecipientNotLinkedError:
            not_linked += 1
            continue
        except Exception as exc:  # noqa: BLE001 -- one digest's; logged by type, ids only
            log.warning(
                "extraction_weekly_digest_failed",
                user_id=digest.user_id,
                team_id=digest.team_id,
                reason=type(exc).__name__,
            )
            continue
        if went:
            sent.append(digest.user_id)
    if owed:
        log.info(
            "extraction_weekly_digests_sent",
            owed=len(owed),
            sent=len(sent),
            not_linked=not_linked,
        )
    if refused:
        raise PrivacyViolationError(
            f"weekly digest refused by the outbound check for {len(refused)} person(s): "
            f"{', '.join(refused)}"
        )
    return sent


@shared_task(name="autune.extraction.periodic.remind_due_items")
@periodic(timedelta(minutes=10))
def remind_due_items() -> list[str]:
    """Tell each assignee, once, that an item of theirs is due tomorrow or has
    passed its date (``reminders``). Returns the item ids a message went for.

    A direct message to the assignee alone, through the Slack bot of the
    team that held the meeting, to the account they linked. Nobody else is
    told. Each reminder is claimed and sent in its own transaction
    (``send_due_reminder``): a failed send takes the claim back and the next
    run tries again, and a claim another run holds sends nothing. A send the
    outbound check refused keeps its claim (``settle_refused_due_reminder``),
    so the refusal is reported once rather than every run. A team
    with no Slack connection and an assignee who has not linked a Slack
    account are skipped and looked at again next time, while the reminder
    is still owed. **A privacy violation is never swallowed, and never
    buried**: the others are still sent, then it is raised with the item
    ids -- whatever else a later reminder runs into. An error this loop did
    not expect (a database error, an item deleted under the claim) is that
    one reminder's: logged by type, and the loop goes on, so it cannot end
    the run before the violations already collected are raised.

    Every ten minutes, in Korea's daytime only: the first run after nine
    sends the day's reminders and the rest find nothing owed.
    """
    if not get_settings().due_reminders:
        return []
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        owed = service.due_reminders_to_send(session, now=now)
        secrets: dict[str, str | None] = {}
        for team_id in sorted({r.team_id for r in owed}):
            config = load_integration(session, team_id, "slack")
            secrets[team_id] = config.require_secret() if config is not None else None

    sent: list[str] = []
    violations: list[str] = []
    not_linked = 0
    for reminder in owed:
        secret = secrets[reminder.team_id]
        if secret is None:
            continue
        try:
            with session_scope() as session:
                went = service.send_due_reminder(session, SlackClient(secret), reminder, now=now)
        except PrivacyViolationError:
            violations.append(reminder.action_item_id)
            # Reported once: the claim is kept, in its own transaction, so the
            # next run does not refuse the same text again (review of #751).
            try:
                with session_scope() as session:
                    service.settle_refused_due_reminder(session, reminder, now=now)
            except Exception as exc:  # noqa: BLE001 -- the violation is still raised
                log.warning(
                    "extraction_due_reminder_refusal_not_kept",
                    action_item_id=reminder.action_item_id,
                    reason=type(exc).__name__,
                )
            continue
        except SlackRecipientNotLinkedError:
            # Counted, not logged one by one: it is the same person every run
            # until they link an account or the reminder stops being owed.
            not_linked += 1
            continue
        except IntegrationError as exc:
            log.warning(
                "extraction_due_reminder_send_failed",
                action_item_id=reminder.action_item_id,
                reason=type(exc).__name__,
            )
            continue
        except Exception as exc:  # noqa: BLE001 -- one reminder's, see the docstring
            # The type only: a database error carries its parameters.
            # No soft time limit is set on this task today; one added later
            # would raise ``SoftTimeLimitExceeded`` into this clause, and would
            # have to be let through.
            log.warning(
                "extraction_due_reminder_failed",
                action_item_id=reminder.action_item_id,
                reason=type(exc).__name__,
            )
            continue
        if went:
            sent.append(reminder.action_item_id)

    if owed:
        log.info(
            "extraction_due_reminders_sent",
            owed=len(owed),
            sent=len(sent),
            not_linked=not_linked,
            violations=len(violations),
        )
    if violations:
        raise PrivacyViolationError(
            f"unmasked value in {len(violations)} due reminder(s): {', '.join(violations)}"
        )
    return sent


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
def sync_action_item(action_item_id: str) -> str:
    """Step 7 for one item past confirmation: create its Notion page the
    first time, update the same page every edit after (#30, #342).

    Returns what happened, for ``sync_after_confirmation`` to keep or clear
    the board's failure: ``COPY_SENT``, ``COPY_GONE`` (no such item) or
    ``COPY_NOT_CONNECTED``.

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
            return COPY_GONE
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
            return COPY_NOT_CONNECTED
        service.sync_action_item_to_notion(
            session,
            NotionClient(config.secret),
            action_item_id=action_item_id,
            database_id=database_id,
            property_names=config.config.get("action_properties"),
        )
    return COPY_SENT


COPY_SENT, COPY_GONE, COPY_NOT_CONNECTED = "sent", "gone", "not_connected"
COPY_NOT_NEEDED = "not_needed"
"""What a sync of one item to a tool did. ``COPY_NOT_CONNECTED`` is a skip, and
it is not "the copy went": a team that disconnected Notion or Jira may still
have the item's page or issue there, and a person who disconnected their
calendar still has the item's event on it, saying what it said -- nothing this
run did changed that. So a failure standing for the copy is kept, not cleared
(mkkim68, reviews of #774 and #823; the user's call, 2026-10-05).
``COPY_NOT_NEEDED`` is the calendar's "this item gets no event": nothing is
outside, and nothing stands to be kept."""


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

    **Each of the three is on its own, whatever it raises** (mkkim68, review of
    #774). The named errors above are the ones a tool or the outbound check
    gives; anything else -- a database error, a claim that lost a race --
    used to leave this function at the first copy, so the two after it were
    never tried and nothing was recorded for any of them. On the paths that
    run this without a person watching (a corrected line, deleted speech)
    that meant a deleted sentence could stay in Jira or on a calendar with no
    mark on the card. Now the copy that broke is recorded as failed, by the
    error's class only, and the next one runs.
    """
    try:
        went = sync_action_item(action_item_id)
    except IntegrationError as exc:
        log.warning("extraction_notion_sync_failed", action_item_id=action_item_id)
        _sync_failed(action_item_id, sync_state.NOTION, exc)
    except PrivacyViolationError as exc:
        log.warning(
            "extraction_notion_sync_blocked_by_privacy_guard", action_item_id=action_item_id
        )
        _sync_failed(action_item_id, sync_state.NOTION, exc)
    except Exception as exc:  # noqa: BLE001 -- one copy never costs the others theirs
        _sync_broke(action_item_id, sync_state.NOTION, exc)
    else:
        if went != COPY_NOT_CONNECTED:
            _sync_went(action_item_id, sync_state.NOTION)
    # Separately, so a Notion failure never costs the calendar its event and
    # the other way round.
    try:
        on_calendar = sync_action_item_calendar(action_item_id)
    except IntegrationError as exc:
        log.warning("extraction_calendar_sync_failed", action_item_id=action_item_id)
        _sync_failed(action_item_id, sync_state.CALENDAR, exc)
    except PrivacyViolationError as exc:
        log.warning(
            "extraction_calendar_sync_blocked_by_privacy_guard", action_item_id=action_item_id
        )
        _sync_failed(action_item_id, sync_state.CALENDAR, exc)
    except Exception as exc:  # noqa: BLE001 -- one copy never costs the others theirs
        _sync_broke(action_item_id, sync_state.CALENDAR, exc)
    else:
        if on_calendar != COPY_NOT_CONNECTED:
            _sync_went(action_item_id, sync_state.CALENDAR)
    # And Jira on its own too. ``AutuneError`` covers a refused Jira grant
    # (``JiraReconnectRequiredError``) as well as integration errors.
    try:
        outcome = sync_action_item_jira(action_item_id)
    except AutuneError as exc:
        log.warning("extraction_jira_sync_failed", action_item_id=action_item_id, error=exc.code)
        _sync_failed(action_item_id, sync_state.JIRA, exc)
    except Exception as exc:  # noqa: BLE001 -- one copy never costs the others theirs
        _sync_broke(action_item_id, sync_state.JIRA, exc)
    else:
        if outcome == JIRA_NEEDS_RECONNECT:
            # Skipped, but not because there was nothing to send: the grant was
            # refused once (raised above, the first time) and ``jira_access``
            # answers ``None`` from then on. Clearing here let one "다시 시도"
            # take the red mark off an item Jira never got (PARKJAEKYUNG0525,
            # review of #754).
            _record_failure(action_item_id, sync_state.JIRA, sync_state.RECONNECT)
        elif outcome != COPY_NOT_CONNECTED:
            _sync_went(action_item_id, sync_state.JIRA)
    # The project minutes it is in, when they went out: put back, edited.
    _refresh_minutes_for(item_ids=[action_item_id])


@shared_task(name="autune.extraction.sync_item_copies", acks_late=True)
def sync_item_copies(action_item_id: str) -> None:
    """``sync_after_confirmation`` on the worker: an item's Notion page, Jira
    issue and calendar event, each failure kept and each success clearing it
    (#680).

    For the paths that change an item outside a person's edit -- a corrected
    line (``_follow_corrections``) and deleted speech
    (``forget_deleted_speech``). They used to queue the three syncs directly,
    so a failure there was neither recorded nor cleared (PARKJAEKYUNG0525,
    review of #754). One task, the three in turn, as an edit runs them."""
    sync_after_confirmation(action_item_id)


def _sync_failed(action_item_id: str, system: str, exc: BaseException) -> None:
    """Keep that this copy failed, by kind (#680), so the board can say so.

    In its own transaction: the failed attempt's is already rolled back. Only
    the class of ``exc`` is read. **Never raises** -- this is bookkeeping after
    a failure that has already been handled and logged, and a database
    hiccup here must not turn into a crashed background task; it is logged by
    type and the card simply goes on saying nothing."""
    _record_failure(action_item_id, system, sync_state.kind_of(exc))


def _sync_broke(action_item_id: str, system: str, exc: BaseException) -> None:
    """A copy ended in something no tool raises: said loudly, by the error's
    class and never its message, and kept as a failure like any other so the
    card shows it and "다시 시도" can run it again. Never raises."""
    log.error(
        "extraction_copy_sync_broke",
        action_item_id=action_item_id,
        system=system,
        error=type(exc).__name__,
    )
    _sync_failed(action_item_id, system, exc)


def _record_failure(action_item_id: str, system: str, kind: str) -> None:
    """``_sync_failed``'s write, for a kind known without an exception. Never
    raises, for the reason ``_sync_failed`` gives."""
    try:
        with session_scope() as session:
            sync_state.record_failure(session, action_item_id, system, kind)
    except Exception as error:  # noqa: BLE001 -- see ``_sync_failed``
        log.warning(
            "extraction_sync_failure_not_recorded",
            action_item_id=action_item_id,
            system=system,
            reason=type(error).__name__,
        )


def _sync_went(action_item_id: str, system: str) -> None:
    """The copy went through, or there was nothing to copy: whatever failure
    stood for it is over. Never raises, for the reason ``_sync_failed`` gives."""
    try:
        with session_scope() as session:
            sync_state.clear_failure(session, action_item_id, system)
    except Exception as error:  # noqa: BLE001 -- see ``_sync_failed``
        log.warning(
            "extraction_sync_failure_not_cleared",
            action_item_id=action_item_id,
            system=system,
            reason=type(error).__name__,
        )


JIRA_SENT, JIRA_SKIPPED, JIRA_NEEDS_RECONNECT = "sent", "skipped", "needs_reconnect"
"""What ``sync_action_item_jira`` did, for ``sync_after_confirmation`` to keep
or clear the board's failure: a skip because the team's grant needs a person to
reconnect is not a skip because there was nothing to send (review of #754).
``JIRA_SKIPPED`` is the item being gone; a team with no Jira connection or no
project chosen answers ``COPY_NOT_CONNECTED``, as the Notion sync does."""


@shared_task(name="autune.extraction.sync_action_item_jira", acks_late=True)
def sync_action_item_jira(action_item_id: str) -> str:
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
            return JIRA_SKIPPED
        # ``check_project``: a project deleted in Jira comes back as no project,
        # recorded for the screen to ask for a new one (#458).
        access = jira_access(meeting.team_id, check_project=True)
        config = load_integration(session, meeting.team_id, jira_sync.JIRA)
        if access is None or not access.project_key:
            if config is not None and config.config.get("needs_reconnect"):
                log.info("extraction_jira_needs_reconnect", action_item_id=action_item_id)
                return JIRA_NEEDS_RECONNECT
            log.info("extraction_jira_not_connected", action_item_id=action_item_id)
            return COPY_NOT_CONNECTED
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
    return JIRA_SENT


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

    A grant recorded as issued to another client (core keeps ``client_id``
    beside it since the review of #700) raises the same error **without**
    asking Google: the answer is known, and a refresh with the wrong client
    is one refused call per sync for as long as the person stays connected.
    A grant from before the client was recorded is tried as it always was.
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
        issued_to = config.config.get("client_id")
        if issued_to and issued_to != client_id:
            raise ReconnectRequiredError(
                "the calendar grant was issued to another Google client; connect again"
            )
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
def sync_action_item_calendar(action_item_id: str) -> str:
    """Step 7's calendar half (#435): the item's due date on its assignee's own
    calendar -- ``calendar_sync.sync_due_date_to_calendar``.

    Skipped, not failed, for an assignee who has not connected a calendar and
    for a deployment without Google client credentials (``_calendars``). Like
    the Notion sync it does not retry itself: a timed-out create may have made
    the event, and a retry would make a second.

    Returns what happened, as the Notion and Jira syncs do (mkkim68, review of
    #823): ``COPY_SENT`` when the event was written; ``COPY_NOT_NEEDED`` when
    the item has no event and gets none -- no date, no assignee with an
    account, not confirmed, or a calendar that was never connected;
    ``COPY_NOT_CONNECTED`` when **an event of this item is still on its
    assignee's calendar and could not be reached** -- they disconnected, or
    the deployment lost its Google client. That last one is read off the row:
    the sync deletes the row whenever the event's owner is no longer the
    item's, so a row left after a sync that wrote nothing is an event on the
    right person's calendar that still says what it said.
    """
    with session_scope() as session, _calendars(session) as calendar_for:
        written = calendar_sync.sync_due_date_to_calendar(
            session, calendar_for, action_item_id=action_item_id
        )
        if written is not None:
            return COPY_SENT
        if session.get(ExtCalendarEvent, action_item_id) is not None:
            log.info("extraction_calendar_event_unreachable", action_item_id=action_item_id)
            return COPY_NOT_CONNECTED
        return COPY_NOT_NEEDED


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
    ``trash_notion_page`` / ``close_jira_issue`` could not get through,
    which is ``drain_external_cleanup``'s (#692).
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
    deletion. Jira closes instead (``close_jira_issue``): Jira has no trash.

    A page this cannot trash -- Notion did not answer, or the team's Notion is
    not connected right now -- is owed to ``ext_external_cleanup`` before the
    item's ref goes with it, and ``drain_external_cleanup`` tries again (#692).
    """
    owed: tuple[str, str] | None = None
    try:
        with session_scope() as session:
            ref = session.get(ExtExternalRef, (action_item_id, "notion"))
            item = session.get(ExtActionItem, action_item_id)
            meeting = session.get(Meeting, item.meeting_id) if item is not None else None
            if ref is None or not ref.external_id or meeting is None:
                return
            owed = (meeting.team_id, str(ref.external_id))
            config = load_integration(session, meeting.team_id, "notion")
            if config is None or not config.secret:
                log.info("extraction_notion_trash_not_connected", action_item_id=action_item_id)
                _owe_external_cleanup(meeting.team_id, "notion", owed[1], site=None)
                return
            client = NotionClient(config.secret)
            try:
                service.trash_item_page(client, owed[1], config.config.get("action_properties"))
            finally:
                client.close()
            log.info("extraction_notion_trashed_with_item", action_item_id=action_item_id)
    except Exception as exc:  # noqa: BLE001 -- a deletion must not fail on Notion
        log.warning(
            "extraction_notion_trash_failed",
            action_item_id=action_item_id,
            error=type(exc).__name__,
        )
        if owed is not None:
            _owe_external_cleanup(owed[0], "notion", owed[1], site=None)


def close_jira_issue(action_item_id: str) -> None:
    """Before the board deletes an item: its Jira issue closed with a note
    (``jira_sync.close_for_deleted_item``). Runs in the deleting request, best
    effort -- an unreachable Jira never blocks a deletion.

    An issue this cannot close -- Jira did not answer, or the team's
    connection is missing or needs a person to reconnect -- is owed to
    ``ext_external_cleanup`` with the site its key is from, and
    ``drain_external_cleanup`` tries again (#692)."""
    owed: tuple[str, str, str | None] | None = None
    try:
        with session_scope() as session:
            ref = session.get(ExtExternalRef, (action_item_id, jira_sync.JIRA))
            item = session.get(ExtActionItem, action_item_id)
            meeting = session.get(Meeting, item.meeting_id) if item is not None else None
            if meeting is None or ref is None or not ref.external_id:
                return
            if not ref.site:
                # A ref from before #458 names no site, and its key could be
                # anyone's issue on the site connected now; ``close_for_deleted_item``
                # leaves it alone, so there is nothing to owe (mkkim68, review of #764).
                log.info("extraction_jira_close_no_site", action_item_id=action_item_id)
                return
            owed = (meeting.team_id, str(ref.external_id), ref.site)
            access = jira_access(meeting.team_id)
            if access is None:
                log.info("extraction_jira_close_not_connected", action_item_id=action_item_id)
                _owe_external_cleanup(meeting.team_id, jira_sync.JIRA, owed[1], site=owed[2])
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
        if owed is not None:
            _owe_external_cleanup(owed[0], jira_sync.JIRA, owed[1], site=owed[2])


def _owe_external_cleanup(team_id: str, system: str, external_id: str, *, site: str | None) -> None:
    """Record a page or issue the deleting request could not clean up, in its own
    transaction -- the deletion's may still roll back, and the item's ref is about
    to go either way. Ids only. Never raises: losing this record is what #692
    was, and it must not also cost the person their deletion."""
    try:
        with session_scope() as session:
            session.execute(
                service._insert_if_absent_into(session, ExtExternalCleanup)
                .values(team_id=team_id, system=system, external_id=external_id, site=site)
                .on_conflict_do_nothing(index_elements=["team_id", "system", "external_id"])
            )
        log.info("extraction_external_cleanup_owed", team_id=team_id, system=system)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        log.warning(
            "extraction_external_cleanup_not_recorded",
            team_id=team_id,
            system=system,
            error=type(exc).__name__,
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
    copied into ``ext_calendar_cleanup`` for ``drain_calendar_cleanup``, and its
    project minutes' copies into ``ext_project_send_cleanup`` for
    ``drain_project_send_cleanup`` (#787 review).

    Only a copy -- no call to Google here. The hook runs once per meeting
    inside the retention sweep, and a slow or failing calendar must not hold
    the sweep up. A database error is raised on purpose: the sweep then keeps
    the meeting and tries again, rather than deleting it with its events
    unrecorded. Safe to run twice (the queue is unique per user and event).
    """
    with session_scope() as session:
        minutes = project_send.queue_meeting(session, meeting_id)
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
    log.info(
        "extraction_meeting_calendar_events_queued",
        meeting_id=meeting_id,
        events=len(rows),
        minutes_copies=minutes,
    )


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


@shared_task(name="autune.extraction.periodic.drain_external_cleanup")
@periodic(timedelta(minutes=10))
def drain_external_cleanup() -> int:
    """Trash the Notion pages and close the Jira issues of deleted items whose
    cleanup the deleting request could not do (#692). Returns how many went.

    The same as at deletion: a page to Notion's trash, an issue closed with
    ``jira_sync.DELETED_NOTE``. Done, or already gone (Notion's 404, Jira's
    404): the row goes. A team whose Notion or Jira is not connected now, or
    whose Jira needs a person to reconnect, is skipped and kept, with no
    attempt counted -- connecting again is what lets it through. A key from
    another Jira site than the team's now -- or from none -- may name someone else's issue: dropped,
    logged. A transient failure is counted, up to ``CLEANUP_MAX_ATTEMPTS``; a
    refusal drops the row, logged, as ``drain_calendar_cleanup`` does.

    At most ``CLEANUP_BATCH`` rows are tried per run; rows passed over for a
    missing connection do not count toward it. A connection that cannot be
    read this run (a refresh that failed for now, a secret that would not
    decrypt) passes that team over the same way, so one team never rolls the
    whole run back.
    """
    done = 0
    tried = 0
    jira_for: dict[str, JiraAccess | None] = {}
    notion_for: dict[str, IntegrationConfig | None] = {}
    with session_scope() as session:
        # A team whose connection cannot be read this run -- a token refresh
        # that timed out or got a 5xx, a secret that would not decrypt -- is
        # passed over like an unconnected one, its rows kept and no attempt
        # counted. Raised here, it would end the run and roll back every row
        # already cleaned: closed issues noted twice next run, counted
        # attempts lost, every team behind it kept waiting (PARK, review of
        # #764).
        def notion_config(team_id: str) -> IntegrationConfig | None:
            if team_id not in notion_for:
                try:
                    config = load_integration(session, team_id, "notion")
                except Exception as exc:  # noqa: BLE001 -- that team only, this run only
                    _connection_unread(team_id, "notion", exc)
                    config = None
                notion_for[team_id] = config if config is not None and config.secret else None
            return notion_for[team_id]

        def jira(team_id: str) -> JiraAccess | None:
            if team_id not in jira_for:
                try:
                    jira_for[team_id] = jira_access(team_id)
                except JiraReconnectRequiredError:
                    jira_for[team_id] = None
                except Exception as exc:  # noqa: BLE001 -- that team only, this run only
                    _connection_unread(team_id, "jira", exc)
                    jira_for[team_id] = None
            return jira_for[team_id]

        # Rows of a team not connected now are passed over, not counted, so
        # the batch walks past them by id: a hundred of them first in line
        # must not keep every other team's cleanup waiting (mkkim68, review
        # of #764). Locked rows are skipped, so two runs that overlap never
        # trash or close the same thing twice.
        after = 0
        while tried < CLEANUP_BATCH:
            rows = list(
                session.scalars(
                    select(ExtExternalCleanup)
                    .where(ExtExternalCleanup.id > after)
                    .order_by(ExtExternalCleanup.id)
                    .limit(CLEANUP_BATCH)
                    .with_for_update(skip_locked=True)
                )
            )
            if not rows:
                break
            after = rows[-1].id
            for row in rows:
                if tried >= CLEANUP_BATCH:
                    break
                notion: IntegrationConfig | None = None
                access: JiraAccess | None = None
                if row.system == "notion":
                    notion = notion_config(row.team_id)
                    if notion is None:
                        continue
                else:
                    access = jira(row.team_id)
                    if access is None:
                        continue
                tried += 1
                done += _clean_up_one(session, row, notion, access)
    log.info("extraction_external_cleanup_drained", tried=tried, done=done)
    return done


def _connection_unread(team_id: str, system: str, exc: Exception) -> None:
    log.warning(
        "extraction_external_cleanup_connection_unread",
        team_id=team_id,
        system=system,
        error=type(exc).__name__,
    )


def _clean_up_one(
    session: Session,
    row: ExtExternalCleanup,
    notion_config: IntegrationConfig | None,
    access: JiraAccess | None,
) -> int:
    """One owed page or issue, with the team's connection already in hand; 1
    when it went. The rules are ``drain_external_cleanup``'s."""
    ids = {"team_id": row.team_id, "system": row.system, "external_id": row.external_id}
    try:
        if row.system == "notion":
            assert notion_config is not None and notion_config.secret
            notion = NotionClient(notion_config.secret)
            try:
                # Retitled first, as at deletion (#768).
                service.trash_item_page(
                    notion, row.external_id, notion_config.config.get("action_properties")
                )
            finally:
                notion.close()
        else:
            assert access is not None
            # Equal or nothing: a key without a site could be anyone's issue
            # on the site connected now (mkkim68, review of #764).
            if row.site != access.cloud_id:
                log.info("extraction_external_cleanup_other_site", **ids)
                session.delete(row)
                return 0
            jira = JiraClient.for_cloud(access.access_token, access.cloud_id)
            try:
                jira_sync.close_issue(jira, row.external_id)
            finally:
                jira.close()
        session.delete(row)
        return 1
    except TransientIntegrationError:
        row.attempts += 1
        if row.attempts >= CLEANUP_MAX_ATTEMPTS:
            # The page id or issue key is logged, so a person can finish by hand.
            log.warning("extraction_external_cleanup_gave_up", **ids)
            session.delete(row)
    except IntegrationError as exc:
        log.warning("extraction_external_cleanup_refused", **ids, error=type(exc).__name__)
        session.delete(row)
    except Exception as exc:  # noqa: BLE001 -- one row must not block the queue
        row.attempts += 1
        log.warning("extraction_external_cleanup_failed", **ids, error=type(exc).__name__)
        if row.attempts >= CLEANUP_MAX_ATTEMPTS:
            session.delete(row)
    return 0


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
    # The project minutes it is in, when they went out: taken back (#669), edited.
    _refresh_minutes_for(decision_ids=[decision_id])


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
    (``service.forget_speech``), the project minutes that carried them are
    owed a rewrite and queued for it (``refresh_project_minutes``), and their copies in Notion, Jira
    and the calendar are queued to follow -- a confirmed row's, and those of an item
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
        # Owed from the same commit that drops the words: whatever happens to
        # the queue or the tools after this, the retry knows (#787 review).
        minutes_owed = project_send.owe_refresh(
            session,
            project_send.meetings_with_sends(session, done.changed_items, done.changed_decisions),
        )
    try:
        # Queued, not run here: the person deleting their speech does not wait
        # on three tools, and a refresh that cannot be queued is still owed.
        for meeting_id in minutes_owed:
            refresh_project_minutes_queued.delay(meeting_id)
        for action_item_id in done.changed_items:
            sync_item_copies.delay(action_item_id)
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
