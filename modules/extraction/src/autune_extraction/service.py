"""Business logic for module B: Structured Extraction.

Owner: 강민구. See docs/modules/extraction.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ext_*`` tables.
Never imports another module.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import ColumnElement, Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, selectinload

from autune_contracts.enums import ActionStatus, UtteranceKind
from autune_contracts.extraction import (
    ACTION_PROGRESS_WINDOW,
    AGENDA_TITLE_MAX,
    JIRA_ISSUE_URL,
    ActionItem,
    AgendaIssue,
    AmbiguousAgreement,
    Classification,
    Decision,
    ExtractionResult,
    MeetingActionProgress,
    TeamActionProgress,
    TeamAgenda,
)
from autune_contracts.transcript import Utterance as TranscriptUtterance
from autune_core import (
    Meeting,
    Participant,
    TeamMember,
    User,
    Utterance,
    get_logger,
    load_integration,
    session_scope,
)
from autune_core.errors import NotFoundError, ValidationError
from autune_core.settings import get_settings as get_core_settings
from autune_core.user_integrations import users_linked_to_slack_member
from autune_integrations import (
    PermanentIntegrationError,
    PostedMessage,
    SlackApi,
    assert_personal_delivery,
)
from autune_integrations.privacy import find_unmasked

from . import reminders
from .config import get_settings
from .confirmations import (
    CONFIRMATION_TIMEOUT,
    WEAK_ASSENT,
    ConfirmationResponse,
    build_confirmation_dm,
)
from .decisions import (
    DEFAULT_MAX_GAP,
    ClassifiedUtterance,
    decision_id,
    group_decisions,
    needs_write_up,
)
from .edit_cost import EditCost
from .models import (
    ExtActionItem,
    ExtActionItemRelated,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionRelated,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtDueReminder,
    ExtDueReminderOptOut,
    ExtEditEvent,
    ExtExternalRef,
    ExtExtractionRun,
    ExtMeetingNote,
)
from .noun_form import tidy
from .pipeline.base import (
    Classifier,
    NliModel,
    ReferenceResolver,
    Resolution,
    ResolutionRequest,
)
from .pipeline.related import related_ids
from .pipeline.resolver import MAX_CONTEXT_AFTER, MAX_CONTEXT_UTTERANCES
from .schemas import (
    ActionItemCreate,
    ActionItemDetail,
    ActionItemRead,
    ActionItemUpdate,
    Assignable,
    CarriedOver,
    CarriedOverItem,
    DecisionCreate,
    DecisionDetail,
    DecisionReviewUpdate,
    EditHistoryEntry,
    ExternalRefRead,
    MeetingReview,
    MeetingSummary,
    MyConfirmation,
    Outbound,
    OutboundBlocked,
    OutboundDecision,
    ReviewAmbiguous,
    ReviewDecision,
    SourceUtterance,
    SummaryDecision,
)
from .slots import KST, Assignee, assignee_of, meeting_day, parse_due

log = get_logger(__name__)


def send_confirmation_dm(
    slack: SlackApi,
    *,
    speaker_id: str,
    recipient_id: str,
    utterance_id: str,
    quoted_text: str,
    answer_url: str,
) -> PostedMessage:
    """Ask one speaker whether their own weak assent was a commitment.

    ``speaker_id`` and ``recipient_id`` are both taken rather than one, so the
    guard has two values to compare. Passing the same value twice reads as
    redundant right up until somebody adds a "notify the meeting owner too"
    parameter, and then it is the thing that refuses.

    The utterance quoted here is the recipient's own speech, delivered to nobody
    else. ``assert_personal_delivery`` states that as a precondition instead of
    leaving it to whoever next edits the call site — invariant 11, and
    ``docs/architecture/privacy.md`` section 3.

    Masking is checked by the client, which reads the blocks as well as the
    fallback text. It did not always: this function carried its own
    ``check_outbound`` on the quotation until #74 taught the shared guard to read
    a whole request body.
    """
    assert_personal_delivery(subject_id=speaker_id, recipient_id=recipient_id, is_direct=True)

    text, blocks = build_confirmation_dm(
        utterance_id=utterance_id,
        quoted_text=quoted_text,
        answer_url=answer_url,
        buttons=get_core_settings().slack_buttons,
    )
    sent = slack.send_dm_message(recipient_id, text, blocks)

    # Ids only. The utterance is meeting content and a log line is a store.
    log.info("extraction_confirmation_sent", utterance_id=utterance_id)
    return sent


def ask_for_confirmation(
    session: Session,
    slack: SlackApi,
    *,
    meeting_id: str,
    speaker_id: str,
    recipient_id: str,
    utterance_id: str,
    quoted_text: str,
    answer_url: str,
    reason: str = WEAK_ASSENT,
) -> ExtConfirmation | None:
    """Open a confirmation and send its DM, in that order — or send nothing.

    The row goes in first. Delivery is at-least-once and Slack can fail after
    accepting the call, so a send that is not preceded by a row can leave a
    question asked with no deadline running — the state where nothing ever
    resolves it. The reverse, a row whose DM failed, is visible and retryable:
    the send and the claim are in one transaction, so a failed send takes the
    ``sent_at`` back with it and the next run claims the row again.

    Returns ``None`` when the clock was already running, having sent nothing.
    The question is out; a second DM for it would be the same question asked
    twice, which reads to the speaker as the first one not having counted.

    ``send_confirmation_dm`` stays callable on its own for the privacy guards it
    carries; this is the entry point that also starts the clock.
    """
    row = open_confirmation(
        session, meeting_id=meeting_id, utterance_id=utterance_id, reason=reason
    )
    if row is None:
        # Ids only, and no send. Another run holds this question.
        log.info("extraction_confirmation_already_asked", utterance_id=utterance_id)
        return None
    sent = send_confirmation_dm(
        slack,
        speaker_id=speaker_id,
        recipient_id=recipient_id,
        utterance_id=utterance_id,
        quoted_text=quoted_text,
        answer_url=answer_url,
    )
    # Where it landed and what it quoted, so a later correction of the line can
    # be carried into the DM itself (#586).
    row.dm_channel, row.dm_ts = sent.channel or None, sent.ts or None
    row.dm_digest = source_digest([quoted_text])
    return row


def open_confirmation(
    session: Session, *, meeting_id: str, utterance_id: str, reason: str = WEAK_ASSENT
) -> ExtConfirmation | None:
    """Start the clock, once — and say whether this call is the one that did.

    Returns the row when this call started the clock, and ``None`` when it was
    already running. ``None`` is not a failure: it means another run has the
    question, and the caller must not send a second DM for it.

    A re-send keeps the original ``sent_at``. The deadline measures how long the
    speaker has had the question in front of them, and a Celery retry means they
    had it the whole time — restarting it would give the model another day to
    look undecided for free.

    A row the pipeline recorded without asking (``sent_at`` empty) gets its
    clock started here, when the question is actually put -- not when the
    ambiguity was found, or a question sent days later would arrive expired.

    **Both statements are decided by the database**, the same rule
    ``record_ambiguous_agreements`` follows and for the same reason (#153,
    #198). Reading the row and then deciding was wrong two ways once anything
    sends: two runs that both read no row raced on the primary key, and two runs
    that both read ``sent_at`` empty each wrote their own timestamp and each sent
    a DM — one question, two messages, and the deadline the later of the two. So
    the insert skips a row that is already there, and the update carries
    ``sent_at IS NULL`` in its own WHERE. The second run blocks on the first's
    row, and when it is released the update matches nothing.

    Deliberately not a place to reset an answer: someone who has already replied
    keeps their reply, and gets no second DM.
    """
    session.execute(
        _insert_if_absent(session)
        .values(
            utterance_id=utterance_id,
            meeting_id=meeting_id,
            reason=reason,
            sent_at=None,
        )
        .on_conflict_do_nothing(index_elements=["utterance_id"])
    )
    return session.scalars(
        update(ExtConfirmation)
        .where(
            ExtConfirmation.utterance_id == utterance_id,
            ExtConfirmation.sent_at.is_(None),
        )
        .values(sent_at=datetime.now(UTC))
        .returning(ExtConfirmation)
        .execution_options(synchronize_session="fetch")
    ).one_or_none()


WEB_ANSWERS: dict[str, UtteranceKind] = {
    "commitment": UtteranceKind.COMMITMENT,
    "decision": UtteranceKind.DECISION,
    "not_commitment": UtteranceKind.CONCERN,
}
"""The three answers the web offers, resolved as the DM's buttons resolve them
(``confirmations.ACTION_IDS``)."""

_ANSWER_OF = {kind.value: answer for answer, kind in WEB_ANSWERS.items()}


def answer_url(meeting_id: str) -> str:
    """Where a speaker answers their own open questions: the meeting's 액션 tab."""
    return f"{get_core_settings().web_base_url.rstrip('/')}/meetings/{meeting_id}/actions"


def _own_confirmations(session: Session, meeting_id: str, user_id: str) -> Select[Any]:
    return (
        select(ExtConfirmation, Utterance.text)
        .join(Utterance, Utterance.id == ExtConfirmation.utterance_id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(
            ExtConfirmation.meeting_id == meeting_id,
            Participant.user_id == user_id,
            Participant.consented.is_(True),
        )
        .order_by(Utterance.start_sec, Utterance.id)
    )


def my_confirmations(session: Session, meeting_id: str, reader: User) -> list[MyConfirmation]:
    """The meeting's ambiguous agreements the reader said -- theirs to answer and
    nobody else's: the question is about a person's own words, and goes to that
    person only, on the web as in the DM (privacy.md section 3). An unknown
    meeting, or one the reader cannot read, is the 404 any other read gives.
    """
    require_readable_meeting(session, meeting_id, reader)
    return [
        MyConfirmation(
            utterance_id=row.utterance_id,
            text=text,
            answer=_ANSWER_OF.get(row.resolved_kind or ""),  # type: ignore[arg-type]
        )
        for row, text in session.execute(_own_confirmations(session, meeting_id, reader.id))
    ]


def answer_confirmation(
    session: Session, utterance_id: str, reader: User, answer: str
) -> MyConfirmation:
    """The reader's answer to one of their own questions, given on the web.

    The same path a click on the DM takes (``resolve_confirmation``): a
    commitment makes the draft at once, any other answer takes an untouched one
    back, and the later answer wins. A question nobody had put yet -- the
    speaker never linked Slack, or the DM had not gone out -- is put by opening
    the page, so its clock starts here (``open_confirmation``). Anyone but the
    speaker gets a 404, the same as a question that does not exist.
    """
    row = session.get(ExtConfirmation, utterance_id)
    if row is None:
        raise NotFoundError("confirmation", utterance_id)
    require_readable_meeting(session, row.meeting_id, reader)
    own = session.execute(
        _own_confirmations(session, row.meeting_id, reader.id).where(
            ExtConfirmation.utterance_id == utterance_id
        )
    ).first()
    if own is None:
        raise NotFoundError("confirmation", utterance_id)
    if row.sent_at is None:
        open_confirmation(session, meeting_id=row.meeting_id, utterance_id=utterance_id)
    resolve_confirmation(
        session,
        ConfirmationResponse(
            utterance_id=utterance_id, resolved_kind=WEB_ANSWERS[answer], responder_id=reader.id
        ),
    )
    return MyConfirmation(utterance_id=utterance_id, text=own[1], answer=answer)  # type: ignore[arg-type]


def dms_to_correct(session: Session, *, meeting_id: str, spoken: Mapping[str, str]) -> list[str]:
    """Utterance ids whose confirmation DM quotes a line that has since been
    corrected (#586): sent with a place kept, and the line no longer hashing to
    what the DM quoted."""
    stale: list[str] = []
    for row in session.scalars(
        select(ExtConfirmation).where(
            ExtConfirmation.meeting_id == meeting_id, ExtConfirmation.dm_ts.is_not(None)
        )
    ):
        text = spoken.get(row.utterance_id)
        if text is not None and row.dm_digest != source_digest([text]):
            stale.append(row.utterance_id)
    return stale


def apply_confirmation_response(response: ConfirmationResponse) -> None:
    """Record what the speaker answered. The seam the Slack handler delegates to.

    Opens its own session because the handler has none — a Slack action arrives
    outside any request or task that owns one.
    """
    with session_scope() as session:
        answered = resolve_confirmation(session, response)
    if answered is not None and response.is_commitment:
        # After the commit, so the job finds the draft. Imported here: tasks
        # imports this module. The summary is written only now, for an answer
        # of "약속입니다" -- never for the DM (decided with the user, 2026-10-01).
        from . import tasks

        tasks.summarise_confirmed_draft.delay(response.utterance_id)


def answer_from_slack(response: ConfirmationResponse) -> None:
    """A click on the DM's buttons (``slack.py``), recorded only when the
    speaker made it.

    Slack signs the request, not the person. The click counts when the Slack
    account that made it is the one the speaker linked (#255) and it came from
    the workspace the meeting's team installed Autune into -- the same "only
    the speaker" the web path checks in ``answer_confirmation`` (#610 review).
    Anything else is logged by id and dropped, like an orphaned click.
    """
    with session_scope() as session:
        allowed = _clicked_by_the_speaker(session, response)
    if not allowed:
        log.info("extraction_confirmation_not_the_speaker", utterance_id=response.utterance_id)
        return
    apply_confirmation_response(response)


def _clicked_by_the_speaker(session: Session, response: ConfirmationResponse) -> bool:
    row = session.get(ExtConfirmation, response.utterance_id)
    if row is None:
        # ``resolve_confirmation`` logs and ignores an orphaned click.
        return True
    speaker = session.scalar(
        select(Participant.user_id)
        .join(Utterance, Utterance.participant_id == Participant.id)
        .where(Utterance.id == response.utterance_id)
    )
    if speaker is None or speaker not in users_linked_to_slack_member(
        session, response.responder_id
    ):
        return False
    team_id = session.scalar(select(Meeting.team_id).where(Meeting.id == row.meeting_id))
    slack = load_integration(session, team_id, "slack") if team_id else None
    return (
        slack is not None
        and bool(response.workspace_id)
        and slack.config.get("workspace_id") == response.workspace_id
    )


def resolve_confirmation(
    session: Session, response: ConfirmationResponse
) -> ExtConfirmation | None:
    """Write one answer down, or decline to.

    Idempotent by assignment rather than by a guard: Slack retries a click it has
    not heard back from within three seconds, and writing the same kind twice
    leaves the row where it already was. A person who changes their mind and
    clicks a different button is the same code path, and the later answer wins —
    which is what a person expects a button to do.

    **A missing row is ignored, not created.** A click can only exist because a
    DM went out, so no row means the meeting was deleted underneath it. Creating
    one here would write meeting-scoped data back after the cascade that was
    meant to remove it.

    **The row is locked for the rest of the transaction.** The draft below is
    looked up before it is inserted, and nothing unique stops a second insert
    for the same utterance. A click landing while a rerun rebuilds the
    meeting's items could not see the rerun's uncommitted item and would make
    its own, and E would count the promise twice (#529 review);
    ``build_action_items`` takes the same locks, so one waits for the other. A
    Slack retry already waited behind the first answer's update of this row,
    but only because autoflush sends that update before the lookup -- the lock
    says it outright.
    """
    row = session.get(ExtConfirmation, response.utterance_id, with_for_update=True)
    if row is None:
        log.info("extraction_confirmation_orphaned", utterance_id=response.utterance_id)
        return None
    if row.sent_at is None:
        # A click needs a DM, and this row's never went out. Recording it would
        # be an answer to a question nobody asked.
        log.info("extraction_confirmation_unasked", utterance_id=response.utterance_id)
        return None

    row.resolved_kind = response.resolved_kind.value
    row.responded_at = datetime.now(UTC)

    # Ids and a label. The utterance itself is meeting content.
    log.info(
        "extraction_confirmation_received",
        utterance_id=response.utterance_id,
        resolved_kind=response.resolved_kind.value,
    )
    # The later answer wins here too: a commitment makes a draft, any other answer
    # takes an untouched one back. ``ext_classifications`` is not rewritten -- it
    # records what the model said, ``resolved_kind`` records what the speaker
    # said, and the two stay comparable (#10's evaluation reads both).
    if response.is_commitment:
        draft_confirmed_commitment(session, row)
    else:
        withdraw_confirmed_draft(session, row)
    return row


def draft_confirmed_commitment(
    session: Session, confirmation: ExtConfirmation, resolution: Resolution | None = None
) -> ExtActionItem | None:
    """The draft item for an agreement its speaker confirmed was a commitment.

    Slot-filled from the stored utterance exactly like a model-classified
    commitment -- the speaker is the assignee, the first date phrase the due
    date, the utterance's own text the description -- and it starts in *needs
    confirmation* like every model item: the speaker said the words were a
    promise, and a team still accepts the item before it leaves for Notion or a
    calendar (ADR 0006).

    ``confidence`` is 1.0, the same rule as a hand-added item: the speaker's
    answer is the certainty, and a model score would put an item its own author
    confirmed under S15's "below the candidate line".

    **Returns the existing item when there is one** for this utterance, so a
    Slack retry, a changed-then-restored answer and a rerun all land on one item.
    Returns ``None`` -- and writes nothing -- when the utterance is gone, blank,
    or its speaker did not consent to analysis (privacy.md section 5: excluded
    speech is not stored, so there is nothing to quote and no one to assign).

    ``resolution`` is a summary already written for it -- a rerun has one
    (``confirmed_summaries``). Without one the description is the line, tidied,
    and ``summarise_confirmed_draft`` replaces it a moment later.
    """
    existing = session.scalar(
        select(ExtActionItem)
        .join(ExtActionItemSource, ExtActionItemSource.action_item_id == ExtActionItem.id)
        .where(ExtActionItemSource.utterance_id == confirmation.utterance_id)
    )
    if existing is not None:
        return existing

    utterance = session.get(Utterance, confirmation.utterance_id)
    if utterance is None or not utterance.text.strip():
        return None
    participant = (
        session.get(Participant, utterance.participant_id)
        if utterance.participant_id is not None
        else None
    )
    if participant is None or not participant.consented:
        return None

    meeting = session.get(Meeting, confirmation.meeting_id)
    day = meeting_day(meeting.started_at if meeting is not None else None)
    user_id = participant.user_id
    known = {user_id} if user_id is not None and session.get(User, user_id) is not None else set()
    assignee = assignee_of(user_id, utterance.speaker_label, known=known)
    due = parse_due(utterance.text, day)
    written = resolution.text if resolution is not None else utterance.text
    item = ExtActionItem(
        meeting_id=confirmation.meeting_id,
        description=tidy(written),
        description_resolved=written != utterance.text,
        assignee_id=assignee.user_id,
        assignee_label=assignee.label,
        due_date=due.date if due is not None else None,
        due_text=due.text if due is not None else None,
        status=ActionStatus.NEEDS_CONFIRMATION.value,
        confidence=1.0,
        origin="model",
        sources=[ExtActionItemSource(utterance_id=confirmation.utterance_id)],
        related=_cited(session, confirmation.meeting_id, confirmation.utterance_id, resolution),
        source_digest=stored_digest(session, [confirmation.utterance_id]),
    )
    session.add(item)
    session.flush()
    log.info(
        "extraction_confirmed_commitment_drafted",
        action_item_id=item.id,
        utterance_id=confirmation.utterance_id,
    )
    return item


def _untouched_drafts(session: Session, utterance_id: str) -> list[ExtActionItem]:
    """The draft a speaker's answer made for this utterance, while nobody has
    touched it: still in *needs confirmation*, no edit recorded against it."""
    return list(
        session.scalars(
            select(ExtActionItem)
            .join(ExtActionItemSource, ExtActionItemSource.action_item_id == ExtActionItem.id)
            .where(
                ExtActionItemSource.utterance_id == utterance_id,
                ExtActionItem.status == ActionStatus.NEEDS_CONFIRMATION.value,
                ExtActionItem.origin == "model",
                ~select(ExtEditEvent.id)
                .where(ExtEditEvent.action_item_id == ExtActionItem.id)
                .exists(),
            )
        ).all()
    )


def _cited(
    session: Session, meeting_id: str, utterance_id: str, resolution: Resolution | None
) -> list[ExtActionItemRelated]:
    """The lines a summary says it used, as rows for the drawer -- only this
    meeting's consenting ones, never the utterance itself."""
    if resolution is None or not resolution.used:
        return []
    citable = consented_utterance_ids(session, meeting_id)
    return [
        ExtActionItemRelated(utterance_id=u)
        for u in dict.fromkeys(resolution.used)
        if u in citable and u != utterance_id
    ]


def confirmed_draft_window(session: Session, utterance_id: str) -> list[ClassifiedUtterance] | None:
    """What ``summarise_confirmed_draft`` writes a summary from: the meeting,
    with this utterance as the one commitment (``chat_draft_window``). ``None``
    when there is nothing to summarise -- the answer is no longer "commitment",
    or the draft is gone or a person has touched it.
    """
    row = session.get(ExtConfirmation, utterance_id)
    if row is None or row.resolved_kind != UtteranceKind.COMMITMENT.value:
        return None
    if not _untouched_drafts(session, utterance_id):
        return None
    return chat_draft_window(session, row.meeting_id, utterance_id)


def apply_confirmed_summary(session: Session, utterance_id: str, resolution: Resolution) -> bool:
    """Put the summary on the draft a speaker's "약속입니다" made, if that is
    still the answer and the draft is still untouched.

    Decided with the user (2026-10-01): the DM shows the speaker's own line, and
    the summary appears only once they confirm -- on the board, as the draft's
    description, the line beneath it. The confirmation row is locked first, the
    order ``resolve_confirmation`` and ``build_action_items`` take, so a changed
    answer or a rerun in between is seen. Writes no edit event: a person did not
    correct anything. Returns whether it was applied.
    """
    row = session.get(ExtConfirmation, utterance_id, with_for_update=True)
    if row is None or row.resolved_kind != UtteranceKind.COMMITMENT.value:
        return False
    drafts = _untouched_drafts(session, utterance_id)
    if not drafts:
        return False
    said = session.get(Utterance, utterance_id)
    for draft in drafts:
        draft.description = tidy(resolution.text)
        draft.description_resolved = said is not None and resolution.text != said.text
        # Keep the row of a line the draft already cites: a new equal row is
        # inserted before the old one is deleted, and breaks the unique key.
        kept = {r.utterance_id: r for r in draft.related}
        cited = _cited(session, draft.meeting_id, utterance_id, resolution)
        draft.related = [kept.get(r.utterance_id, r) for r in cited]
    session.flush()
    return True


def confirmed_commitment_ids(session: Session, meeting_id: str) -> set[str]:
    """The meeting's ambiguous agreements whose speakers answered "commitment"."""
    return set(
        session.scalars(
            select(ExtConfirmation.utterance_id).where(
                ExtConfirmation.meeting_id == meeting_id,
                ExtConfirmation.resolved_kind == UtteranceKind.COMMITMENT.value,
            )
        )
    )


def confirmed_summaries(
    resolver: ReferenceResolver,
    classified: Sequence[ClassifiedUtterance],
    confirmed: Collection[str],
) -> dict[str, Resolution]:
    """Summaries for the agreements a meeting's speakers confirmed as
    commitments (``confirmed_commitment_ids``), for a rerun: it rebuilds their
    drafts (``draft_confirmed_commitment``), and each keeps a summary rather
    than falling back to the line. Only the confirmed ones are summarised --
    most agreements never are. Model inference: call it outside a session.
    """
    if not confirmed:
        return {}
    marked = [
        replace(u, kind=UtteranceKind.AMBIGUOUS if u.id in confirmed and u.text else None)
        for u in classified
    ]
    return resolve_commitment_summaries(resolver, marked, kind=UtteranceKind.AMBIGUOUS)


SPEECH_DELETED_TEXT = "삭제된 발화에서 만든 항목"
"""What replaces a line that was the deleted speech itself (#587)."""


@dataclass(frozen=True)
class SpeechForgotten:
    """What ``forget_speech`` did, by id: drafts deleted, and the items and
    decisions whose text changed and whose copies outside must follow."""

    deleted_items: tuple[str, ...] = ()
    changed_items: tuple[str, ...] = ()
    changed_decisions: tuple[str, ...] = ()


def _person_wrote_description(session: Session, action_item_id: str) -> bool:
    """Whether a person ever edited this item's description -- or edited it
    before edits named their fields (#109), when it cannot be told: then the
    text may be theirs, and it is kept."""
    for fields in session.scalars(
        select(ExtEditEvent.fields).where(
            ExtEditEvent.action_item_id == action_item_id, ExtEditEvent.kind == "edited"
        )
    ):
        if fields is None or "description" in fields.split(","):
            return True
    return False


def forget_speech(session: Session, utterance_ids: Collection[str]) -> SpeechForgotten:
    """A person deleted their own speech (#587): the work stays, their words go.

    Decided with the user (2026-10-01). For every item and decision drawn from
    ``utterance_ids``:

    - an unconfirmed draft the model or the chat made is deleted -- nobody has
      accepted it, and it is only the deleted words restated. A draft that was
      confirmed once and moved back is not that: it has a copy outside, and
      deleting its row would leave the words there with nothing left to find
      them by. It is kept and treated as the next bullet says (#657);
    - a confirmed item whose description is the line itself (not a model's
      summary, not a person's writing) reads ``SPEECH_DELETED_TEXT``, and its
      ``due_text`` -- a fragment of the line -- is cleared; a summary or a
      person's text is the team's record and stays;
    - a decision loses ``original_statement`` (what B would send D next is
      then ``statement``), and a model statement that is the line tidied (no
      cited lines, so not a write-up) reads ``SPEECH_DELETED_TEXT`` too.

    Nothing is republished here: copies C, D and E already received through
    ``ExtractionResult`` are theirs, and stay until they act on the same signal
    (#601 review).

    Every row that stays forgets its ``source_digest``. The digest was taken
    over all of its lines; once one is gone the lines that are left hash
    differently, and ``apply_source_corrections`` would read that as a corrected
    line -- rewriting the text from what is left, flagging a person's writing
    and re-syncing the copies outside. With no digest the next run records a
    baseline and changes nothing (#607 review).

    Runs before the utterances are deleted, while the sources still name them.
    Safe to repeat. Writes no edit event: no person corrected anything.
    """
    ids = set(utterance_ids)
    if not ids:
        return SpeechForgotten()
    items = session.scalars(
        select(ExtActionItem)
        .join(ExtActionItemSource, ExtActionItemSource.action_item_id == ExtActionItem.id)
        .where(ExtActionItemSource.utterance_id.in_(ids))
        .distinct()
    ).all()
    deleted: list[str] = []
    changed: list[str] = []
    for item in items:
        drafted = item.origin in ("model", "chat")
        follows = copies_follow(session, item)
        if drafted and not follows:
            deleted.append(item.id)
            session.delete(item)
            continue
        item.source_digest = None
        touched = False
        if (
            drafted
            and not item.description_resolved
            and item.description != SPEECH_DELETED_TEXT
            and not _person_wrote_description(session, item.id)
        ):
            item.description = SPEECH_DELETED_TEXT
            touched = True
        if item.due_text is not None:
            item.due_text = None
            touched = True
        if touched and follows:
            changed.append(item.id)

    decisions = session.scalars(
        select(ExtDecision)
        .join(ExtDecisionSource, ExtDecisionSource.decision_id == ExtDecision.id)
        .where(ExtDecisionSource.utterance_id.in_(ids))
        .distinct()
    ).all()
    confirmed = set(
        session.scalars(
            select(ExtDecisionReview.decision_id).where(
                ExtDecisionReview.decision_id.in_([d.id for d in decisions]),
                ExtDecisionReview.status == "confirmed",
            )
        )
    )
    paged = decisions_with_a_page(session, [d.id for d in decisions])
    changed_decisions: list[str] = []
    for decision in decisions:
        decision.source_digest = None
        touched = False
        if decision.original_statement is not None:
            decision.original_statement = None
            touched = True
        if (
            decision.origin == "model"
            and not decision.related
            and decision.statement != SPEECH_DELETED_TEXT
        ):
            decision.statement = SPEECH_DELETED_TEXT
            touched = True
        # A confirmed decision's page follows the text; a put-back one that
        # still has its page is queued so that page is retired (#669).
        if touched and (decision.id in confirmed or decision.id in paged):
            changed_decisions.append(decision.id)
    session.flush()
    return SpeechForgotten(tuple(deleted), tuple(changed), tuple(changed_decisions))


def withdraw_confirmed_draft(session: Session, confirmation: ExtConfirmation) -> int:
    """Take back the draft this utterance made, if nobody has touched it since.

    A speaker who answered *commitment* and then *not a commitment* has changed
    their mind, and the item they made should go with the first answer. Only an
    untouched draft goes: one still in *needs confirmation*, with no edit
    recorded against it. Once a person has moved or corrected it, it is theirs,
    and a later click on a DM does not get to delete their work.

    Writes no ``ext_edit_events`` row: that table counts a *person's* corrections
    (ADR 0006), and this is neither. Returns how many items were removed.
    """
    drafts = _untouched_drafts(session, confirmation.utterance_id)
    for draft in drafts:
        session.delete(draft)
    if drafts:
        session.flush()
    return len(drafts)


def ambiguous_agreements_for_meeting(
    session: Session, meeting_id: str, *, now: datetime | None = None
) -> list[AmbiguousAgreement]:
    """This meeting's ambiguous agreements as the contract E reads.

    ``confirmation_sent`` is false for a row the pipeline recorded without being
    able to ask -- the speaker unmapped, the workspace app missing. That is every
    row until #70 and #30 give the pipeline a way to send.

    ``now`` is a parameter so a caller can ask what the outcome was at publish
    time rather than at read time.
    """
    rows = session.scalars(
        select(ExtConfirmation)
        .where(ExtConfirmation.meeting_id == meeting_id)
        .order_by(ExtConfirmation.sent_at, ExtConfirmation.utterance_id)
    ).all()

    return [
        AmbiguousAgreement(
            utterance_id=row.utterance_id,
            reason=row.reason,
            confirmation_sent=row.confirmation_sent,
        )
        for row in rows
    ]


AGENT_ORIGINS = ("chat", "followup")
"""``ExtActionItem.origin`` values for items the agent layer adds (#561)."""


def create_action_item(
    session: Session, payload: ActionItemCreate, *, origin: str = "user"
) -> ExtActionItem:
    """Add an item the model missed.

    ``confidence`` is 1.0 and ``origin`` is ``user``: a person typing an item is
    the certainty, and the origin is what edit cost is measured against.

    Counted as an edit. An item the model missed costs the user more than one it
    got wrong -- they have to notice the absence, which is the failure recall
    makes likely and the one editing cannot fix by itself.

    ``origin`` is one of ``AGENT_ORIGINS`` when the agent layer adds the item
    after a person approved it (``tools``). That is not a person finding what
    the model missed, so it records no ``created`` event; a later edit or
    deletion is counted like any other.

    **Every foreign key on this row is checked before anything is written.** An
    unknown meeting is a 404 and a source that is not one of *this* meeting's
    utterances is a 422 naming the field. Without the check the first reached
    the client as a 500 from the foreign key, found by a local end-to-end run on
    2026-09-19, and the second was worse when the utterance did exist: an item
    on one meeting citing another meeting's utterance, whose words
    ``GET /action-items/{id}`` then quotes on this meeting's board.

    ``assignee_id`` must name a member of the meeting's team
    (``require_assignable``). That also covers what the check it replaced
    was for: the field is a foreign key, so a deleted account or a typo
    would otherwise reach ``session.flush()`` as a 500 rather than a 422
    naming the field.
    """
    if origin != "user" and origin not in AGENT_ORIGINS:
        raise ValueError(f"not an origin create_action_item makes: {origin!r}")
    if session.get(Meeting, payload.meeting_id) is None:
        raise NotFoundError("meeting", payload.meeting_id)
    if payload.assignee_id is not None:
        require_assignable(session, payload.meeting_id, payload.assignee_id)
    wanted = set(payload.source_utterance_ids)
    if wanted:
        found = set(
            session.scalars(
                select(Utterance.id).where(
                    Utterance.meeting_id == payload.meeting_id, Utterance.id.in_(wanted)
                )
            )
        )
        if found != wanted:
            raise ValidationError(
                "source_utterance_ids must be utterances of this meeting",
                field="source_utterance_ids",
            )
    item = ExtActionItem(
        meeting_id=payload.meeting_id,
        description=payload.description,
        assignee_id=payload.assignee_id,
        assignee_label=payload.assignee_label,
        due_date=payload.due_date,
        status=ActionStatus.NEEDS_CONFIRMATION.value,
        confidence=1.0,
        origin=origin,
    )
    item.sources = [
        ExtActionItemSource(utterance_id=utterance_id)
        for utterance_id in dict.fromkeys(payload.source_utterance_ids)
    ]
    item.source_digest = stored_digest(session, list(dict.fromkeys(payload.source_utterance_ids)))
    session.add(item)
    session.flush()

    if origin == "user":
        _record_edit(session, meeting_id=item.meeting_id, action_item_id=item.id, kind="created")
    return item


def chat_draft_window(
    session: Session, meeting_id: str, utterance_id: str
) -> list[ClassifiedUtterance] | None:
    """The meeting as ``resolve_commitment_summaries`` reads it, with
    ``utterance_id`` as the one commitment -- for an item the chat drafts from
    that utterance (``tools.add_action_item``).

    ``None`` when the utterance is not this meeting's, or its speaker did not
    consent: an item cannot be drafted from speech that is not analysed at all
    (privacy.md section 5). Every other non-consenting line is blanked, exactly
    as ``classify_utterances`` blanks it, so the summary's window and its
    candidates skip it the same way the pipeline's do.
    """
    consented = consented_utterance_ids(session, meeting_id)
    if utterance_id not in consented:
        return None
    transcript = stored_transcript(session, meeting_id)
    if not any(u.id == utterance_id for u in transcript):
        return None
    return [
        ClassifiedUtterance(
            id=u.id,
            kind=UtteranceKind.COMMITMENT if u.id == utterance_id else None,
            confidence=1.0,
            text=u.text if u.id in consented else "",
            speaker=u.speaker,
        )
        for u in transcript
    ]


def create_chat_item(
    session: Session,
    *,
    meeting_id: str,
    utterance_id: str,
    resolution: Resolution,
    assignee_id: str | None = None,
    due_date: date | None = None,
) -> ExtActionItem:
    """An item the chat drafted from one utterance, after its summary was
    written (``chat_draft_window``, ``resolve_commitment_summaries``).

    Built the way ``build_action_items`` builds a model item -- the summary
    tidied into the description, the utterance as the source, the lines the
    summary cited kept beside it, the speaker and the first date phrase unless
    the chat named an assignee or a date -- but ``origin`` is ``chat``: a rerun
    leaves it alone, and edit cost does not count it as one the model missed (no
    ``created`` event). It waits for confirmation; until then a person sees the
    original utterances under the summary (``originals_hidden``).
    """
    said = session.get(Utterance, utterance_id)
    if said is None or said.meeting_id != meeting_id:
        raise ValidationError(
            "utterance_id must be an utterance of this meeting", field="utterance_id"
        )
    if assignee_id is not None:
        require_assignable(session, meeting_id, assignee_id)
    meeting = session.get(Meeting, meeting_id)
    if assignee_id is None:
        speaker_id = (
            session.scalar(select(Participant.user_id).where(Participant.id == said.participant_id))
            if said.participant_id
            else None
        )
        known = {speaker_id} if speaker_id and session.get(User, speaker_id) else set()
        assignee = assignee_of(speaker_id, said.speaker_label, known=known)
    else:
        assignee = Assignee(user_id=assignee_id, label=None)
    due = (
        None
        if due_date is not None
        else parse_due(said.text, meeting_day(meeting.started_at if meeting else None))
    )
    # Only lines the model could have been shown: this meeting's, from a
    # consenting speaker. A model that names any other id is not believed.
    citable = consented_utterance_ids(session, meeting_id) if resolution.used else set()
    item = ExtActionItem(
        meeting_id=meeting_id,
        description=tidy(resolution.text),
        description_resolved=resolution.text != said.text,
        assignee_id=assignee.user_id,
        assignee_label=assignee.label,
        due_date=due_date if due_date is not None else (due.date if due else None),
        due_text=None if due_date is not None else (due.text if due else None),
        status=ActionStatus.NEEDS_CONFIRMATION.value,
        confidence=1.0,
        origin="chat",
        sources=[ExtActionItemSource(utterance_id=utterance_id)],
        source_digest=source_digest([said.text]),
        related=[
            ExtActionItemRelated(utterance_id=u)
            for u in dict.fromkeys(resolution.used)
            if u in citable and u != utterance_id
        ],
    )
    session.add(item)
    session.flush()
    return item


def originals_hidden(item: ExtActionItem) -> bool:
    """Whether the screens and the agent's tools leave out the utterances an
    item came from.

    A chat-drafted item shows them only while it waits for confirmation --
    decided with the user, 2026-10-01: a person checks the summary against them,
    and once it is confirmed the summary alone stands. Hidden, not deleted: the
    rows stay, so D and E still count the item's source and the meeting's
    deletion still takes them with it. What leaves for Notion, Jira or a
    calendar was only ever the summary.
    """
    return item.origin == "chat" and item.status != ActionStatus.NEEDS_CONFIRMATION.value


def read_model(
    item: ExtActionItem,
    *,
    assignee_name: str | None = None,
    summary: str | None = None,
    sync_refs: list[ExternalRefRead] | None = None,
    assignee_departed: bool = False,
    meeting_title: str | None = None,
) -> ActionItemRead:
    """One item as this module's own screens read it.

    ``assignee_departed`` comes from ``departed_assignees``; see
    ``ActionItemRead.needs_reassignment`` for what it changes.

    Built here rather than by ``from_attributes`` on the schema because five of
    its fields are not columns: the source ids live in the link table, whether
    the item is a candidate depends on a setting the row knows nothing about,
    the assignee's name is not stored at all -- see ``assignee_name`` on
    ``ActionItemRead`` -- the summary is computed from utterances this row does
    not carry -- see ``action_item_summaries`` -- and where the item stands
    with an outside system is read from a table keyed on it rather than owned
    by it -- see ``action_item_external_refs``. Callers with more than one item
    look all three up in a batch (``list_action_items``) rather than let this
    query per row; ``None`` means the same as empty for any of them.

    Deciding *candidate* on the server is the point of this function. The
    threshold belongs to the classifier that produced the confidence, and a
    browser comparing against a number it happens to hold is one deploy away from
    disagreeing with the server about what the meeting produced. While
    ``candidate_confidence`` is unset -- its default until #10 measures one --
    nothing is a candidate, because there is no honest line to draw yet.

    **Confidence alone is not enough, once the item leaves**
    ``needs_confirmation``. A low-confidence item's ``confidence`` column never
    changes -- confirming it moves ``status``, not the number the model gave
    it -- so scoring only on confidence would put it back in front of the
    person on every later visit to the review screen, one column after they
    already confirmed it there (#295). Candidate is therefore *this module's
    own open question, not yet answered*: model-made, and still in
    ``needs_confirmation``. Leaving that status for any other column is the
    same line ``router.update_action_item`` reads before queueing a Notion
    sync.
    """
    threshold = get_settings().candidate_confidence
    is_candidate = (
        threshold is not None
        and item.confidence < threshold
        and item.status == ActionStatus.NEEDS_CONFIRMATION.value
    )
    hidden = originals_hidden(item)
    source_ids = [] if hidden else live_source_ids(item)
    return ActionItemRead(
        id=item.id,
        meeting_id=item.meeting_id,
        meeting_title=meeting_title,
        description=item.description,
        description_resolved=item.description_resolved,
        assignee_id=None if assignee_departed else item.assignee_id,
        assignee_label=item.assignee_label,
        assignee_name=None if assignee_departed else assignee_name,
        due_date=item.due_date,
        due_text=item.due_text,
        status=item.status,
        confidence=item.confidence,
        origin=item.origin,
        source_utterance_ids=source_ids,
        deleted_source_count=0 if hidden else len(item.sources) - len(source_ids),
        needs_reassignment=assignee_departed and item.status in _OPEN_STATUSES,
        needs_recheck=bool(item.needs_recheck),
        is_candidate=is_candidate,
        summary=summary,
        sync_refs=sync_refs or [],
    )


_OPEN_STATUSES = frozenset({ActionStatus.TODO.value, ActionStatus.IN_PROGRESS.value})


def live_source_ids(item: ExtActionItem) -> list[str]:
    """The item's source utterances that still exist, in insertion order.

    A link row outlives its utterance with ``utterance_id`` NULL (see
    ``ExtActionItemSource``); those are counted, never listed.
    """
    return [source.utterance_id for source in item.sources if source.utterance_id is not None]


def departed_assignees(session: Session, items: Sequence[ExtActionItem]) -> set[str]:
    """Ids of the items whose assignee is not a member of the meeting's team.

    Reads the shared ``team_members`` table and never writes it (invariant 4).
    One query for the whole list. Same rule as module D's
    ``_current_team_member_ids``, keyed on the item's meeting rather than a
    thread.
    """
    ids = [item.id for item in items if item.assignee_id is not None]
    if not ids:
        return set()
    member = (
        select(TeamMember.id)
        .where(
            TeamMember.team_id == Meeting.team_id,
            TeamMember.user_id == ExtActionItem.assignee_id,
        )
        .exists()
    )
    rows = session.scalars(
        select(ExtActionItem.id)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(ExtActionItem.id.in_(ids), ~member)
    )
    return set(rows)


def read_one(session: Session, item: ExtActionItem) -> ActionItemRead:
    """``read_model`` for a single item a route just wrote, with its assignee
    looked up. No summary or sync refs -- the routes that write never sent
    them."""
    name = assignee_names(session, [item]).get(item.assignee_id) if item.assignee_id else None
    return read_model(
        item,
        assignee_name=name,
        assignee_departed=item.id in departed_assignees(session, [item]),
        meeting_title=meeting_titles(session, [item]).get(item.meeting_id),
    )


def meeting_titles(session: Session, items: Sequence[ExtActionItem]) -> dict[str, str]:
    """The title of each meeting ``items`` came from, in one query. Reads the
    shared ``meetings`` table and never writes it (invariant 4)."""
    ids = {item.meeting_id for item in items}
    if not ids:
        return {}
    rows = session.execute(select(Meeting.id, Meeting.title).where(Meeting.id.in_(ids)))
    return dict(rows.tuples().all())


def assignee_names(session: Session, items: Sequence[ExtActionItem]) -> dict[str, str]:
    """Display names for every assignee in ``items``, read fresh -- never stored.

    A name is the one piece of a person's identity this module is allowed to
    show (invariant 11 restricts speaking ratio, not who a task is for), and it
    changes with the account, not with the item -- storing it would go stale
    the first time somebody's display name did. One query for the whole list,
    not one per row.
    """
    ids = {item.assignee_id for item in items if item.assignee_id is not None}
    if not ids:
        return {}
    rows = session.execute(select(User.id, User.display_name).where(User.id.in_(ids)))
    return {user_id: display_name for user_id, display_name in rows}


# --- who may read what (#189) ---------------------------------------------------


def within_retention(now: datetime | None = None) -> ColumnElement[bool]:
    """A meeting B may still read or write: not past ``expires_at``.

    A meeting past its retention window stays in the table until A's sweep
    takes it -- longer when one of its deletion hooks fails -- and until then
    nothing of it may be shown (invariant 11; #656). D asks the same question
    with ``visible_meeting_clauses``. Every read of B's that reaches a
    meeting asks this one, so there is one place to get it wrong.
    """
    moment = now if now is not None else datetime.now(UTC)
    return or_(Meeting.expires_at.is_(None), Meeting.expires_at > moment)


def live_meeting(
    session: Session, meeting_id: str, *, now: datetime | None = None
) -> Meeting | None:
    """The meeting, or ``None`` when there is none or it is past retention --
    one answer for both, the way an unknown id and somebody else's get one."""
    return session.scalar(select(Meeting).where(Meeting.id == meeting_id, within_retention(now)))


def is_team_member(session: Session, team_id: str, user_id: str) -> bool:
    """Whether ``user_id`` is on ``team_id`` -- for a route that names the team
    itself (S28 settings, #496) rather than one of its meetings."""
    return _is_team_member(session, user_id=user_id, team_id=team_id)


def _is_team_member(session: Session, *, user_id: str, team_id: str) -> bool:
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.user_id == user_id, TeamMember.team_id == team_id
            )
        )
        is not None
    )


def require_assignable(session: Session, meeting_id: str, assignee_id: str) -> None:
    """Raise unless ``assignee_id`` is a member of the team that held the meeting.

    Every write that names an assignee asks this -- an item added by hand,
    an edit, a chat draft, the agent's reassignment -- so the rule lives
    here and not in whichever screen sent the request (review of #737).
    Until then the routes checked only that the account existed: anyone on
    a team could assign its items to any account whose id they held, and
    the read side had to clear it again (ADR 0007).

    **One answer for an account that does not exist and one that is not on
    the team**: telling them apart would let a member learn which ids are
    accounts. A member who has since left is still cleared at read time;
    this stops the assignment being made, not what happens after.
    """
    team_id = session.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id))
    if team_id is None or not _is_team_member(session, user_id=assignee_id, team_id=team_id):
        raise ValidationError(
            "assignee_id must be a member of the meeting's team", field="assignee_id"
        )


def _refuse(kind: str, ident: str, reader: User, reason: str) -> NotFoundError:
    # Ids only: a description or a decision statement is meeting content.
    log.info("extraction_read_refused", kind=kind, ident=ident, reader_id=reader.id, reason=reason)
    return NotFoundError(kind, ident)


def team_ids_of(session: Session, user_id: str) -> list[str]:
    """The teams this person is a member of, in a fixed order."""
    return list(
        session.scalars(
            select(TeamMember.team_id)
            .where(TeamMember.user_id == user_id)
            .order_by(TeamMember.team_id)
        )
    )


def _require_member_of_meeting(
    session: Session, meeting_id: str, reader: User, *, kind: str, ident: str
) -> None:
    # A meeting past retention is refused as one that is not there (#656).
    team_id = session.scalar(
        select(Meeting.team_id).where(Meeting.id == meeting_id, within_retention())
    )
    if team_id is None:
        raise _refuse(kind, ident, reader, "no_such_meeting")
    if not _is_team_member(session, user_id=reader.id, team_id=team_id):
        raise _refuse(kind, ident, reader, "not_a_member")


def require_readable_meeting(session: Session, meeting_id: str, reader: User) -> None:
    """Raise unless ``reader`` belongs to the team that held this meeting.

    A token proves who is asking, not whose meetings they may read. **An unknown
    id and somebody else's get the same answer**, a ``NotFoundError`` and never
    a 403: a 403 confirms the id exists. Same rule as module C (#276) and D
    (#470); the log keeps the reason. Writing is the same check -- anyone on the
    team may correct its meetings' results, as the review screen assumes.
    """
    _require_member_of_meeting(session, meeting_id, reader, kind="meeting", ident=meeting_id)


def readable_action_item(session: Session, action_item_id: str, reader: User) -> ExtActionItem:
    """The item, if ``reader`` is on the team of its meeting; otherwise the same
    404 an unknown id gets, naming the item and not its meeting."""
    item = session.get(ExtActionItem, action_item_id)
    if item is None:
        raise _refuse("action item", action_item_id, reader, "no_such_item")
    _require_member_of_meeting(
        session, item.meeting_id, reader, kind="action item", ident=action_item_id
    )
    return item


def readable_decision(session: Session, decision_id: str, reader: User) -> ExtDecision:
    """As ``readable_action_item``, for a decision."""
    decision = session.get(ExtDecision, decision_id)
    if decision is None:
        raise _refuse("decision", decision_id, reader, "no_such_decision")
    _require_member_of_meeting(
        session, decision.meeting_id, reader, kind="decision", ident=decision_id
    )
    return decision


def list_action_items(
    session: Session,
    *,
    meeting_id: str | None = None,
    assignee_id: str | None = None,
    status: ActionStatus | None = None,
    due_before: date | None = None,
    visible_to: str | None = None,
) -> list[ActionItemRead]:
    """The items S17 and S05 put on screen. Every filter is optional and they AND.

    ``visible_to`` is a user id: only items from meetings of that user's teams
    come back. The route always passes it (#189); B's own callers, which already
    hold a meeting or a team, do not. A meeting outside the caller's teams
    therefore lists nothing, the same answer as a meeting that does not exist.

    A meeting past its retention window lists nothing either, for every
    caller (``within_retention``, #656).

    ``due_before`` is strict: an item due on that day is not before it. That
    makes "overdue" one argument -- today's date -- instead of yesterday's, and
    an item with no due date is never before anything, so it drops out of any
    date filter rather than reading as overdue.

    Sources are loaded in the same round trip. The card counts them, so a lazy
    load would be one more query per card.
    """
    query = (
        select(ExtActionItem)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(within_retention())
        .options(selectinload(ExtActionItem.sources))
        .order_by(ExtActionItem.created_at, ExtActionItem.id)
    )
    if meeting_id is not None:
        query = query.where(ExtActionItem.meeting_id == meeting_id)
    if assignee_id is not None:
        query = query.where(ExtActionItem.assignee_id == assignee_id)
    if status is not None:
        query = query.where(ExtActionItem.status == status.value)
    if due_before is not None:
        query = query.where(ExtActionItem.due_date < due_before)
    if visible_to is not None:
        query = query.where(
            Meeting.team_id.in_(select(TeamMember.team_id).where(TeamMember.user_id == visible_to))
        )

    items = list(session.scalars(query))
    names = assignee_names(session, items)
    departed = departed_assignees(session, items)
    summaries = action_item_summaries(session, items)
    refs = action_item_external_refs(session, [item.id for item in items])
    titles = meeting_titles(session, items)
    return [
        read_model(
            item,
            assignee_name=names.get(item.assignee_id) if item.assignee_id else None,
            summary=summaries.get(item.id),
            sync_refs=refs.get(item.id, []),
            assignee_departed=item.id in departed,
            meeting_title=titles.get(item.meeting_id),
        )
        for item in items
    ]


CARRIED_OVER_SHOWN = 10
"""How many carried-over items the popup lists. It counts all of them; past
ten, a list stops being read and the board is the place to work through it."""


def assignable_members(session: Session, meeting_id: str) -> list[Assignable]:
    """The people an item of this meeting can be assigned to: the members of
    the meeting's team, by name.

    The team's and nobody else's, because that is who ``_calendar_owner`` and
    the Jira sync will act for -- an account that is not on the team is
    cleared at read time (ADR 0007), so offering it would offer an
    assignment that does not hold. The caller has checked the reader."""
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return []
    rows = session.execute(
        select(User.id, User.display_name)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == meeting.team_id)
        .order_by(User.display_name, User.id)
    ).all()
    return [Assignable(user_id=user_id, name=name) for user_id, name in rows]


def carried_over(session: Session, meeting_id: str, *, today: date | None = None) -> CarriedOver:
    """The open items earlier meetings of this meeting's team left (WBS 4.8).

    Open is *to do* or *in progress*: confirmed work nobody has finished. An
    item still in *needs confirmation* is a draft of its own meeting's review,
    and a finished one is not carried anywhere. "Earlier" is by when the
    meeting was held, or uploaded when nobody recorded a start -- so reviewing
    an old meeting today does not show it the work of the weeks after it.

    Reads only what the board already shows the same team: descriptions,
    assignees and dates, never an utterance. An earlier meeting past its
    retention window carries nothing over (``within_retention``, #656).
    """
    meeting = live_meeting(session, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    held = func.coalesce(Meeting.started_at, Meeting.created_at)
    this_held = meeting.started_at or meeting.created_at
    earlier = {
        row.id: row
        for row in session.scalars(
            select(Meeting).where(
                Meeting.team_id == meeting.team_id,
                Meeting.id != meeting_id,
                held < this_held,
                within_retention(),
            )
        )
    }
    if not earlier:
        return CarriedOver(open=0, overdue=0, items=[])

    day = today or date.today()
    rows = list(
        session.scalars(
            select(ExtActionItem)
            .options(selectinload(ExtActionItem.sources))
            .where(
                ExtActionItem.meeting_id.in_(earlier),
                ExtActionItem.status.in_([ActionStatus.TODO.value, ActionStatus.IN_PROGRESS.value]),
            )
        )
    )

    def late(item: ExtActionItem) -> bool:
        return item.due_date is not None and item.due_date < day

    rows.sort(key=lambda i: (not late(i), i.due_date or date.max, i.created_at, i.id))
    shown = rows[:CARRIED_OVER_SHOWN]
    names = assignee_names(session, shown)
    departed = departed_assignees(session, shown)
    summaries = action_item_summaries(session, shown)
    refs = action_item_external_refs(session, [item.id for item in shown])
    return CarriedOver(
        open=len(rows),
        overdue=sum(1 for item in rows if late(item)),
        items=[
            CarriedOverItem(
                **read_model(
                    item,
                    assignee_name=names.get(item.assignee_id) if item.assignee_id else None,
                    summary=summaries.get(item.id),
                    sync_refs=refs.get(item.id, []),
                    assignee_departed=item.id in departed,
                    meeting_title=earlier[item.meeting_id].title,
                ).model_dump(),
                meeting_started_at=earlier[item.meeting_id].started_at,
            )
            for item in shown
        ],
    )


SHOWN_CONTEXT = 3
"""How many lines before a source a drawer shows. Fewer than the resolver reads
(``MAX_CONTEXT_UTTERANCES``): that one needs a window a model can resolve
against, this one a person's glance at what the sentence was about."""


def context_before(session: Session, source_ids: Sequence[str]) -> list[SourceUtterance]:
    """The lines said just before the first of these utterances, in spoken order.

    Only consenting speakers' and only non-blank ones: an excluded speaker's turn
    is not stored as text (privacy.md section 5), and one row of it reaching a
    screen would be the leak ``resolve_commitment_references`` was fixed for
    (#366). Reads ``utterances``, which module A owns. Empty when there are no
    sources -- a hand-added item has nothing to be "before".
    """
    if not source_ids:
        return []
    first = session.execute(
        select(Utterance.meeting_id, Utterance.start_sec, Utterance.id)
        .where(Utterance.id.in_(source_ids))
        .order_by(Utterance.start_sec, Utterance.id)
        .limit(1)
    ).first()
    if first is None:
        return []
    meeting_id, start, first_id = first
    rows = session.execute(
        select(Utterance.id, Utterance.text)
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(
            Utterance.meeting_id == meeting_id,
            Participant.consented.is_(True),
            Utterance.id.not_in(source_ids),
            func.length(func.trim(Utterance.text)) > 0,
            or_(
                Utterance.start_sec < start,
                and_(Utterance.start_sec == start, Utterance.id < first_id),
            ),
        )
        .order_by(Utterance.start_sec.desc(), Utterance.id.desc())
        .limit(SHOWN_CONTEXT)
    ).all()
    return [SourceUtterance(id=uid, text=text) for uid, text in reversed(rows)]


def related_utterances(session: Session, item_id: str) -> list[SourceUtterance]:
    """The lines an item's summary says it was written from, in spoken order.

    Only consenting speakers' and non-blank lines, the filter every read of the
    transcript draws: consent can be withdrawn after the summary was written, and
    the line must disappear from the screen when it does.
    """
    rows = session.execute(
        select(Utterance.id, Utterance.text)
        .join(ExtActionItemRelated, ExtActionItemRelated.utterance_id == Utterance.id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(
            ExtActionItemRelated.action_item_id == item_id,
            Participant.consented.is_(True),
            func.length(func.trim(Utterance.text)) > 0,
        )
        .order_by(Utterance.start_sec, Utterance.id)
    ).all()
    return [SourceUtterance(id=uid, text=text) for uid, text in rows]


def read_detail(session: Session, item: ExtActionItem) -> ActionItemDetail:
    """One item with the text of the utterances it was drawn from.

    The only route in this module that returns the full *set* of sources
    verbatim. It is here and not on the list because the drawer is the one
    screen that shows every quotation, and it shows one item's at a time --
    ``summary`` is the exception already allowed onto the list, one chosen
    line rather than the whole evidence. See ``ActionItemDetail``.
    """
    names = assignee_names(session, [item])
    name = names.get(item.assignee_id) if item.assignee_id else None
    summary = action_item_summaries(session, [item]).get(item.id)
    refs = action_item_external_refs(session, [item.id]).get(item.id, [])
    departed = item.id in departed_assignees(session, [item])
    hidden = originals_hidden(item)
    return ActionItemDetail(
        **read_model(
            item,
            assignee_name=name,
            summary=summary,
            sync_refs=refs,
            assignee_departed=departed,
            meeting_title=meeting_titles(session, [item]).get(item.meeting_id),
        ).model_dump(),
        sources=[] if hidden else source_utterances(session, item.id),
        context=[]
        if hidden
        else context_before(session, [s.utterance_id for s in item.sources if s.utterance_id]),
        related=[] if hidden else related_utterances(session, item.id),
        history=edit_history(session, item.id),
    )


def action_item_external_refs(
    session: Session, action_item_ids: Collection[str]
) -> dict[str, list[ExternalRefRead]]:
    """Where each item stands with each outside system it has been claimed for.

    A claimed-but-unfinished row (``url is None``) is still reported: S18 needs
    to be able to say "sending" or "failed" rather than only "sent" or nothing.
    One query for the whole list, not one per row.
    """
    if not action_item_ids:
        return {}
    refs = session.scalars(
        select(ExtExternalRef)
        .where(ExtExternalRef.action_item_id.in_(action_item_ids))
        .order_by(ExtExternalRef.created_at)
    )
    by_item: dict[str, list[ExternalRefRead]] = {}
    for ref in refs:
        by_item.setdefault(ref.action_item_id, []).append(
            ExternalRefRead(system=ref.system, url=ref.url, external_id=ref.external_id)  # type: ignore[arg-type]
        )
    return by_item


SUMMARY_MAX_CHARS = 80
"""How much of the longest source utterance ``_summary_of`` keeps. Long enough
to read as a sentence, short enough that a card of them does not become the
transcript it is standing in for."""


def _truncate(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    stripped = text.strip()
    return stripped if len(stripped) <= limit else stripped[: limit - 1].rstrip() + "…"


def _summary_texts(session: Session, utterance_ids: Collection[str]) -> dict[str, str]:
    """``Utterance.text`` for a batch of ids, read once for a whole list."""
    if not utterance_ids:
        return {}
    rows = session.execute(
        select(Utterance.id, Utterance.text).where(Utterance.id.in_(utterance_ids))
    )
    return {utterance_id: text for utterance_id, text in rows}


def action_item_summaries(session: Session, items: Sequence[ExtActionItem]) -> dict[str, str]:
    """A one-line preview of each item's sources, for the ones ``description``
    alone does not already say.

    **Rule-based, not a model.** The longest source utterance, truncated --
    which utterance actually carries the point is a real question (#325), and
    this is the cheap first answer while that is unbuilt: exactly the same
    reasoning ``decisions._substance`` already uses for the settling row.
    Wrong here is visible and checked against the drawer's full quotation, not
    generated prose a reader has no way to verify.

    **Only when there is more than one source.** With a single source
    ``description`` already is that utterance's text (``slots`` builds it that
    way), and repeating it as ``summary`` would be a second copy of the same
    line, not a new one.
    """
    live = {item.id: live_source_ids(item) for item in items}
    multi = [item for item in items if len(live[item.id]) > 1]
    texts = _summary_texts(session, {uid for item in multi for uid in live[item.id]})
    summaries: dict[str, str] = {}
    for item in multi:
        candidates = [texts[uid] for uid in live[item.id] if uid in texts]
        if candidates:
            summaries[item.id] = _truncate(max(candidates, key=len))
    return summaries


def decision_summaries(session: Session, decisions: Sequence[ExtDecision]) -> dict[str, str]:
    """A one-line preview of what a decision's source utterances said.

    Same rule as ``action_item_summaries``: the longest source, truncated, and
    for the same reason. Computed for every decision with at least one source,
    unlike action items -- a decision's ``statement`` is assembled or reworded
    (#305), so even a single-source decision benefits from seeing what was
    literally said beside it.
    """
    texts = _summary_texts(
        session, {source.utterance_id for decision in decisions for source in decision.sources}
    )
    summaries: dict[str, str] = {}
    for decision in decisions:
        candidates = [
            texts[source.utterance_id]
            for source in decision.sources
            if source.utterance_id in texts
        ]
        if candidates:
            summaries[decision.id] = _truncate(max(candidates, key=len))
    return summaries


def source_utterances(session: Session, action_item_id: str) -> list[SourceUtterance]:
    """The item's evidence, in the order it was spoken.

    Reads ``utterances``, which module A owns and this module may only read. An
    utterance that has been deleted takes its link row with it (the foreign key
    cascades), so a missing quotation means the speech is gone, not that the
    join failed.
    """
    rows = session.execute(
        select(Utterance.id, Utterance.text)
        .join(ExtActionItemSource, ExtActionItemSource.utterance_id == Utterance.id)
        .where(ExtActionItemSource.action_item_id == action_item_id)
        .order_by(Utterance.start_sec, Utterance.id)
    ).all()
    return [SourceUtterance(id=utterance_id, text=text) for utterance_id, text in rows]


def update_action_item(
    session: Session, item: ExtActionItem, payload: ActionItemUpdate
) -> ExtActionItem:
    """Apply a correction. A request that changes nothing costs nothing.

    An empty body records no edit rather than one, because edit cost counts what
    the user had to fix and a no-op is not that. A double-submitted form would
    otherwise inflate the metric the product is trying to lower.

    A new ``assignee_id`` gets the same check ``create_action_item`` gives it
    -- a member of the meeting's team -- and clearing it (``None``) needs no
    check at all.
    """
    changes = payload.changes()
    if not changes:
        return item

    if "assignee_id" in changes and payload.assignee_id is not None:
        require_assignable(session, item.meeting_id, payload.assignee_id)

    for field, value in changes.items():
        setattr(item, field, value.value if isinstance(value, ActionStatus) else value)
    # A person has looked at it and changed it: whatever a corrected source asked
    # them to check, they have now had in front of them (#586).
    item.needs_recheck = False
    if "due_date" in changes:
        # The phrase explained the date the model read. A date a person set is
        # not explained by it, and keeping it would hold on to what they
        # corrected (#109).
        item.due_text = None

    _record_edit(
        session,
        meeting_id=item.meeting_id,
        action_item_id=item.id,
        kind="edited",
        fields=list(changes),
    )
    return item


def delete_action_item(session: Session, item: ExtActionItem) -> None:
    """Remove an item the model got wrong. The row is gone, not flagged.

    ``privacy.md`` allows no soft deletes and no tombstones holding content, and
    the metric does not need one: the event records that a deletion happened,
    which is the whole of what edit cost asks. Its ``action_item_id`` clears
    itself when the row goes.
    """
    meeting_id = item.meeting_id
    session.delete(item)
    session.flush()

    _record_edit(session, meeting_id=meeting_id, action_item_id=None, kind="deleted")


def _record_edit(
    session: Session,
    *,
    meeting_id: str,
    action_item_id: str | None,
    kind: str,
    fields: Sequence[str] = (),
) -> None:
    """One correction, counted and not attributed.

    No user id is passed in because none is stored. ADR 0003 forbids per-person
    metrics, and "who corrected the model most" is the same shape of data as a
    speaking ratio. ``fields`` names what an edit changed and never holds a
    value (#109).
    """
    session.add(
        ExtEditEvent(
            meeting_id=meeting_id,
            action_item_id=action_item_id,
            kind=kind,
            fields=",".join(sorted(fields)) or None,
        )
    )


def edit_history(session: Session, action_item_id: str) -> list[EditHistoryEntry]:
    """What happened to one item, oldest first, for the drawer (S18, #109):
    added by a person, and each edit with the fields it changed. No values and
    no people -- see ``ExtEditEvent``. An item the model extracted and nobody
    touched has no entries."""
    rows = session.execute(
        select(ExtEditEvent.kind, ExtEditEvent.fields, ExtEditEvent.created_at)
        .where(ExtEditEvent.action_item_id == action_item_id)
        .order_by(ExtEditEvent.created_at, ExtEditEvent.id)
    )
    return [
        EditHistoryEntry(kind=kind, fields=fields.split(",") if fields else [], at=at)
        for kind, fields, at in rows
    ]


def edit_cost_for_meeting(session: Session, meeting_id: str) -> EditCost:
    """The meeting's correction cost, counted from its rows.

    ``model_items`` counts what the model proposed *including* items since
    deleted, which is why it comes from the events rather than the surviving
    rows. Counting only survivors would score a meeting better the more of its
    items were wrong.
    """
    edits = list(
        session.execute(
            select(ExtEditEvent.kind, ExtEditEvent.action_item_id).where(
                ExtEditEvent.meeting_id == meeting_id
            )
        )
    )

    surviving_model_items = session.execute(
        select(func.count())
        .select_from(ExtActionItem)
        .where(ExtActionItem.meeting_id == meeting_id, ExtActionItem.origin == "model")
    ).scalar_one()

    deleted = sum(1 for kind, _ in edits if kind == "deleted")
    added = sum(1 for kind, _ in edits if kind == "created")
    edited_ids = {item_id for kind, item_id in edits if kind == "edited" and item_id}

    return EditCost(
        model_items=surviving_model_items + deleted,
        edited_items=len(edited_ids) + deleted,
        added_items=added,
        edits=len(edits),
    )


# --- decisions ---------------------------------------------------------------


def build_decisions(
    session: Session,
    *,
    meeting_id: str,
    utterances: Sequence[ClassifiedUtterance],
    max_gap: int = DEFAULT_MAX_GAP,
    summaries: Mapping[str, Resolution] | None = None,
) -> list[ExtDecision]:
    """Rebuild this meeting's decisions from its classified utterances.

    ``summaries`` maps a ``dec_`` id to a model's summary of it
    (``resolve_decision_summaries``, run before any session): its text, tidied and
    followed by the owner and deadline, replaces the line a person sees and that
    leaves, and the lines it says it used are stored in ``ext_decision_related``.
    **``original_statement`` is always the assembled sentence, never the summary**
    -- it is what module D is sent.

    ``utterances`` is every utterance of the meeting in ``start_sec`` order; see
    ``group_decisions`` for why the non-decision ones have to be there.

    The meeting's own row is read for its date, the way ``build_action_items``
    does: a statement carries the deadline the meeting set, and "이번 주 금요일"
    is a different Friday every week. A meeting with no start time keeps the
    phrase as said rather than resolving it against today.

    **Rebuilding upserts by id, which is derived from the meeting and the
    utterances a decision was settled in** (``decisions.decision_id``). A
    rebuild over the same labels and the same utterance ids gives the same
    ``dec_`` id, so a decision whose sources are unchanged is the same row
    across a rebuild -- updated in place, not deleted and reinserted -- and
    D's lineage keeps pointing at rows that exist (#171) without help from
    this function. A decision whose sources changed gets a different id --
    including every decision after module A reprocesses a recording, since
    that mints new ``utt_`` ids (#194) -- and is a genuinely new row; the one
    its old id named is deleted. This is still a rebuild rather than a merge:
    matching an old decision to a reworded new one is the same-decision
    question, and #25 gave that to D.

    Sources are only written for a row this call inserts. A surviving id
    proves its sources are the same set in the same order -- that is what
    produced the id -- so there is nothing to update there; only ``statement``
    and ``confidence`` can differ between two rebuilds of the same sources.

    Deleting a decision that is genuinely gone is a real delete, and its
    sources, review and any external ref all go with it -- by name, not left
    to ``ON DELETE CASCADE`` even though ``ext_decision_reviews.decision_id``
    now has that foreign key (#297): SQLite enforces no foreign key unless
    asked, the unit suite runs there, and an id that can repeat across an
    unrelated meeting's rebuild means a row a cascade missed could attach
    itself to a different decision reusing that id. The foreign key still
    holds in Postgres, as a backstop for any path that deletes a decision
    without going through here. ``ExtDecisionRef`` has no foreign key at all
    (its own docstring), so a claim with no page is deleted here by name.
    **A ref that still names a page is kept** (#669): deleting it left the
    page live in Notion, holding the statement, with nothing left to find it
    by. ``sync_decision_to_notion`` retires that page -- the caller queues it
    (``decision_pages_without_a_decision``) and the Notion backfill sweeps
    what is left. If the id comes back first, the ref is not a stale claim:
    the page is there, and the sync retires or updates it by the new
    decision's verdict.

    **The insert half is one statement, not a loop of ORM adds, because two
    reprocesses of the same meeting can be in flight at once** -- a redelivered
    Celery task, a retry after a slow ack, both true to "every task must be
    safe to run twice" (``docs/architecture/async-pipeline.md``). Both would
    compute the same new ``dec_`` id from the same sources and both would see
    it absent from ``existing``; a plain ``session.add`` for each would let the
    second one's flush hit this table's primary key. ``INSERT ... ON CONFLICT
    (id) DO UPDATE`` makes whichever transaction commits second update the
    row the first one just created rather than collide with it, in the same
    statement that inserts the ones that are genuinely new.
    """
    meeting = session.get(Meeting, meeting_id)
    day = meeting_day(meeting.started_at if meeting is not None else None)

    fresh = {
        decision_id(meeting_id, group.source_utterance_ids): group
        for group in group_decisions(utterances, max_gap=max_gap, day=day)
    }
    summaries = summaries or {}
    heard = {u.id for u in utterances}
    shown: dict[str, str] = {}
    cited: dict[str, list[str]] = {}
    for id_, group in fresh.items():
        summary = summaries.get(id_)
        line = group.statement
        if summary is not None and summary.text.strip() and summary.text != group.core_text:
            head = tidy(summary.text.strip())
            line = f"{head} ({group.suffix})" if group.suffix else head
            cited[id_] = [
                u for u in summary.used if u in heard and u not in group.source_utterance_ids
            ]
        shown[id_] = line

    # Only the model's decisions are rebuilt. One a person added is not derived
    # from labels, so no rerun can recompute it (#246).
    model_made = (ExtDecision.meeting_id == meeting_id, ExtDecision.origin == "model")
    existing_ids = set(session.scalars(select(ExtDecision.id).where(*model_made)))

    gone = existing_ids - fresh.keys()
    if gone:
        session.execute(delete(ExtDecisionReview).where(ExtDecisionReview.decision_id.in_(gone)))
        session.execute(
            delete(ExtDecisionRef).where(
                ExtDecisionRef.decision_id.in_(gone), ExtDecisionRef.external_id.is_(None)
            )
        )
        session.execute(delete(ExtDecisionSource).where(ExtDecisionSource.decision_id.in_(gone)))
        session.execute(delete(ExtDecisionRelated).where(ExtDecisionRelated.decision_id.in_(gone)))
        session.execute(delete(ExtDecision).where(ExtDecision.id.in_(gone)))

    if fresh:
        upsert = _insert_if_absent_into(session, ExtDecision).values(
            [
                {
                    "id": id_,
                    "meeting_id": meeting_id,
                    "statement": shown[id_],
                    "original_statement": group.original_statement or group.statement,
                    "confidence": group.confidence,
                    "origin": "model",
                }
                for id_, group in fresh.items()
            ]
        )
        session.execute(
            upsert.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "statement": upsert.excluded.statement,
                    "original_statement": upsert.excluded.original_statement,
                    "confidence": upsert.excluded.confidence,
                },
            )
        )

        # Sources are only written once, for a row this statement actually
        # inserted -- a surviving id's sources are the same set that produced
        # it, so there is nothing to write there. ``ON CONFLICT DO NOTHING``
        # on the same race this function's whole upsert exists for: a second
        # concurrent insert of the same new decision would otherwise collide
        # on ``uq_ext_decision_sources`` instead of the primary key.
        new_ids = fresh.keys() - existing_ids
        if new_ids:
            session.execute(
                _insert_if_absent_into(session, ExtDecisionSource)
                .values(
                    [
                        {"decision_id": id_, "utterance_id": utterance_id, "position": position}
                        for id_ in new_ids
                        for position, utterance_id in enumerate(fresh[id_].source_utterance_ids)
                    ]
                )
                .on_conflict_do_nothing(index_elements=["decision_id", "utterance_id"])
            )

        # The lines a summary used can differ between two runs over the same
        # sources, so they are replaced, not kept: the summary they belong to was.
        session.execute(delete(ExtDecisionRelated).where(ExtDecisionRelated.decision_id.in_(fresh)))
        related_rows = [
            {"decision_id": id_, "utterance_id": utterance_id}
            for id_, lines in cited.items()
            for utterance_id in lines
        ]
        if related_rows:
            session.execute(
                _insert_if_absent_into(session, ExtDecisionRelated)
                .values(related_rows)
                .on_conflict_do_nothing(index_elements=["decision_id", "utterance_id"])
            )

    # Ordered by ``fresh``, not a fresh query's ``created_at`` -- a batch
    # upsert gives every row in it the same server-side timestamp, so an
    # order-by on that column would settle ties by id instead of the
    # utterance position the caller actually cares about.
    rows = {
        row.id: row
        for row in session.scalars(
            select(ExtDecision).options(selectinload(ExtDecision.sources)).where(*model_made)
        )
    }
    decisions = [rows[id_] for id_ in fresh]
    session.flush()

    # Ids only. A statement is meeting content and a log line is a store.
    log.info("extraction_decisions_built", meeting_id=meeting_id, count=len(decisions))
    return decisions


def decisions_for_meeting(session: Session, meeting_id: str) -> list[Decision]:
    """This meeting's decisions as the contract D reads.

    ``Decision`` is imported from ``autune_contracts.extraction`` rather than the
    package root: the root lists it in ``__all__`` but never imports it, so
    ``from autune_contracts import Decision`` raises (#98). Fixing that is a
    ``packages/`` change, which module B does not own.

    Sources come back in meeting order because the order carries the argument --
    the proposal first, the sentence that settles it last.

    **A decision a person rejected is not in it.** ``delete_decision`` keeps a
    model decision's row and marks its review rejected, so a rerun cannot bring
    the same ``dec_`` id back; without this filter that row still reached D's
    lineage and E's report as a decision, through ``ExtractionResult`` and ``GET
    /results`` -- a person said "this was not decided" and every module but the
    outbound list kept counting it. Raised in review of #247.

    Pending decisions stay: whether D and E hear a decision before anybody has
    looked at it is the open question 2 on #246, not something this read decides.

    **A person's rewording is what goes out**, here as in
    ``review_for_meeting`` and ``outbound_for_meeting``. Reading the review for
    the status and not for the sentence sent the model's wording to D and E while
    Notion and Slack got the corrected one -- one decision, two texts, and the
    one the person rejected as wrong is the one a lineage would be built on.
    Raised in review of #247.
    """
    reviews = {
        review.decision_id: review
        for review in session.scalars(
            select(ExtDecisionReview).where(ExtDecisionReview.meeting_id == meeting_id)
        )
    }
    rows = session.scalars(
        select(ExtDecision)
        .where(ExtDecision.meeting_id == meeting_id)
        .order_by(ExtDecision.created_at, ExtDecision.id)
    ).all()

    return [
        Decision(
            id=row.id,
            statement=_lineage_statement(row, reviews.get(row.id)),
            source_utterance_ids=[
                source.utterance_id for source in sorted(row.sources, key=lambda s: s.position)
            ],
            confidence=row.confidence,
        )
        for row in rows
        if (review := reviews.get(row.id)) is None or review.status != "rejected"
    ]


def _lineage_statement(decision: ExtDecision, review: ExtDecisionReview | None) -> str:
    """What module D is sent: the person's wording if they reworded it, otherwise
    the sentence *as assembled from what was said* -- not the tidied or summarised
    line the screen shows.

    D embeds this and compares it with earlier statements against a threshold
    tuned on that shape (``context.config``); a rewrite made to read well on a
    screen would move every score. A person's own wording is deliberate and is
    sent as they wrote it. A decision they typed has no original and falls back to
    its statement.
    """
    if review is not None and review.statement:
        return review.statement
    return decision.original_statement or decision.statement


def _confirmed_statement(decision: ExtDecision, review: ExtDecisionReview | None) -> str:
    """What the meeting settled, in the wording that stands: the person's if they
    reworded it, the model's otherwise. One definition, read by every surface."""
    return review.statement if review is not None and review.statement else decision.statement


# --- the meeting's result ----------------------------------------------------


def result_for_meeting(session: Session, meeting_id: str) -> ExtractionResult:
    """Everything this meeting produced, as the contract D and E read it.

    Built from what is stored rather than kept from the last run, so it includes
    every correction a person has made since: an item added by hand is in it,
    one deleted is not. That is also what ``autune.extraction.completed`` should
    carry when #31 publishes it, and why the builder is here rather than inside a
    route.

    ``classifications`` comes from ``ext_classifications``, which the pipeline
    writes (``store_classifications``); a meeting that has not been classified
    has none, and an empty list is the truth about it.
    """
    items = session.scalars(
        select(ExtActionItem)
        .options(selectinload(ExtActionItem.sources))
        .where(ExtActionItem.meeting_id == meeting_id)
        .order_by(ExtActionItem.created_at, ExtActionItem.id)
    ).all()

    return ExtractionResult(
        meeting_id=meeting_id,
        action_items=[contract_action_item(item) for item in items],
        decisions=decisions_for_meeting(session, meeting_id),
        classifications=classifications_for_meeting(session, meeting_id),
        ambiguous_agreements=ambiguous_agreements_for_meeting(session, meeting_id),
    )


def contract_action_item(item: ExtActionItem) -> ActionItem:
    """One row as the contract describes an item to other modules.

    Narrower than ``ActionItemRead``. ``origin`` and ``is_candidate`` are about
    how this module's own screens present an item and are not in the contract;
    adding them there would be a contract change (invariant 5), not an edit here.

    ``external_refs`` stays at its default. ``ext_external_refs`` is created by
    the Notion sync (#30), and nothing has been synced before it. Jira was
    dropped from the product (#82).
    """
    return ActionItem(
        id=item.id,
        description=item.description,
        assignee_id=item.assignee_id,
        assignee_label=item.assignee_label,
        due_date=item.due_date,
        source_utterance_ids=live_source_ids(item),
        status=ActionStatus(item.status),
        confidence=item.confidence,
    )


# --- step 1: classification --------------------------------------------------


def consented_utterance_ids(session: Session, meeting_id: str) -> set[str]:
    """This meeting's utterances whose speaker consented to analysis.

    ``Participant.consented`` is False for a speaker whose speech is excluded
    from analysis entirely, and privacy.md section 5 says excluded speech is not
    stored rather than hidden. An utterance with no participant behind it is out
    as well: whether its speaker consented is unknown, and unknown is not yes.
    Module C draws the same line (#163).

    A read on a shared table, in a short session of its own, so the classifier
    never runs inside a transaction.
    """
    return set(
        session.scalars(
            select(Utterance.id)
            .join(Participant, Participant.id == Utterance.participant_id)
            .where(Utterance.meeting_id == meeting_id, Participant.consented.is_(True))
        ).all()
    )


def team_roster(session: Session, meeting_id: str) -> list[str]:
    """Display names of the members of the team that held this meeting -- what
    an outbound classifier replaces before sending (#411). Read only."""
    return list(
        session.scalars(
            select(User.display_name)
            .join(TeamMember, TeamMember.user_id == User.id)
            .join(Meeting, Meeting.team_id == TeamMember.team_id)
            .where(Meeting.id == meeting_id)
            .order_by(User.id)
        )
    )


# --- consent that changes after extraction (#518) ------------------------------


def consent_key(consented: Iterable[str]) -> str:
    """A digest of the utterance ids an extraction was allowed to read, in a
    fixed order, for ``ExtExtractionRun``."""
    return hashlib.sha256("\n".join(sorted(consented)).encode()).hexdigest()


def record_extraction(session: Session, *, meeting_id: str, consented: Iterable[str]) -> None:
    """Remember which speech this extraction read, in the extraction's own
    transaction, so a later consent change can be told apart from none.

    An upsert: a redelivered task and the consent sweep can both write it.
    """
    now = datetime.now(UTC)
    key = consent_key(consented)
    session.execute(
        _insert_if_absent_into(session, ExtExtractionRun)
        .values(meeting_id=meeting_id, consent_key=key, extracted_at=now)
        .on_conflict_do_update(
            index_elements=["meeting_id"], set_={"consent_key": key, "extracted_at": now}
        )
    )


def meetings_with_changed_consent(session: Session) -> list[str]:
    """Meetings whose consenting speech is not what their last extraction read.

    Compares each ``ExtExtractionRun.consent_key`` with the key of the
    utterances ``consented_utterance_ids`` would give now, for every recorded
    meeting in one query. A meeting that has no row is not here: it has not
    been extracted yet, and extracting it is ``on_transcript_ready``'s job.
    """
    recorded = {
        meeting_id: key
        for meeting_id, key in session.execute(
            select(ExtExtractionRun.meeting_id, ExtExtractionRun.consent_key)
        )
    }
    if not recorded:
        return []
    consented: dict[str, list[str]] = {meeting_id: [] for meeting_id in recorded}
    for meeting_id, utterance_id in session.execute(
        select(Utterance.meeting_id, Utterance.id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(Utterance.meeting_id.in_(recorded), Participant.consented.is_(True))
    ):
        consented[meeting_id].append(utterance_id)
    return sorted(
        meeting_id
        for meeting_id, key in recorded.items()
        if consent_key(consented[meeting_id]) != key
    )


def stored_transcript(session: Session, meeting_id: str) -> list[TranscriptUtterance]:
    """The meeting's utterances as ``TranscriptReady`` carries them, read back
    from the shared tables module A wrote -- for a re-extraction that has no
    event to read them from.

    The text is what A stored, masked before its first write (invariant 11), so
    it is the same text the event would carry. ``speaker_id`` and ``role`` come
    from the participant behind each line, as A builds them when it publishes.
    """
    people = {
        participant_id: (user_id, role)
        for participant_id, user_id, role in session.execute(
            select(Participant.id, Participant.user_id, Participant.role).where(
                Participant.meeting_id == meeting_id
            )
        )
    }
    rows = session.scalars(
        select(Utterance)
        .where(Utterance.meeting_id == meeting_id)
        .order_by(Utterance.start_sec, Utterance.id)
    )
    return [
        TranscriptUtterance(
            id=row.id,
            speaker=row.speaker_label,
            speaker_id=people.get(row.participant_id, (None, None))[0]
            if row.participant_id
            else None,
            role=people.get(row.participant_id, (None, None))[1] if row.participant_id else None,
            start=row.start_sec,
            end=row.end_sec,
            text=row.text,
            confidence=row.confidence,
        )
        for row in rows
    ]


# --- a speaker identified after extraction (#360) ------------------------------


FILL_WINDOW = timedelta(days=30)
"""How far back ``fill_identified_assignees`` looks. A speaker is identified in
the days after a meeting; a label still unresolved after a month is one nobody
is going to resolve, and rescanning it every ten minutes forever buys nothing."""

FILL_CAP = 200
"""Items filled per run at most, newest first. The first run after a deploy may
find a backlog; the rest waits ten minutes rather than one run holding locks on
all of it."""


def fill_identified_assignees(
    session: Session, *, now: datetime | None = None
) -> list[ExtActionItem]:
    """Give an item its speaker's account once A has identified the speaker.

    A commitment by an unidentified speaker is drafted with only the label
    ("Speaker 2", ``slots.assignee_of``). When somebody later confirms who that
    was, A fills ``participants.user_id``, and nothing announces it -- #360
    settled on consumers reading it back rather than on a new event. This
    finds the model's items still holding only the label their source was
    spoken under, from the last ``FILL_WINDOW``, whose source utterances all
    belong to one identified, consenting participant, and sets that account as
    the assignee, clearing the label, as a fresh extraction would.

    **A person's choice is never overwritten.** An item is left alone when:

    - a person's edit of it names an assignee field, or names no fields at all
      -- rows written before ``ext_edit_events.fields`` existed are NULL, and
      may have been exactly that edit (lsh2217's review of #536);
    - its label is no longer the speaker label it was drafted with -- a person
      typed a name there;
    - its assignee or its label changed between the read and the write: the
      update carries both as they were read.

    **Write-once.** A filled item holds an account, as a person-assigned one
    does, and nothing here follows a later re-identification of the speaker
    (A can move a label from X to Y): the item then shows X, and a person
    reassigns it on the board. Following it would need a record of which
    assignees this wrote, and would move work a person may already have
    accepted as X's.

    No ``ext_edit_events`` row: that table counts a person's corrections
    (ADR 0006), and this is neither. Returns the items it changed.
    """
    moment = now or datetime.now(UTC)
    maybe_edited = (
        select(ExtEditEvent.id)
        .where(
            ExtEditEvent.action_item_id == ExtActionItem.id,
            ExtEditEvent.kind == "edited",
            or_(ExtEditEvent.fields.is_(None), ExtEditEvent.fields.like("%assignee%")),
        )
        .exists()
    )
    rows = session.execute(
        select(ExtActionItem.id, ExtActionItem.assignee_label, Participant.user_id)
        .join(ExtActionItemSource, ExtActionItemSource.action_item_id == ExtActionItem.id)
        .join(Utterance, Utterance.id == ExtActionItemSource.utterance_id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .join(User, User.id == Participant.user_id)
        .where(
            ExtActionItem.assignee_id.is_(None),
            ExtActionItem.assignee_label == Utterance.speaker_label,
            ExtActionItem.origin == "model",
            ExtActionItem.created_at >= moment - FILL_WINDOW,
            Participant.consented.is_(True),
            ~maybe_edited,
        )
        .order_by(ExtActionItem.created_at.desc(), ExtActionItem.id)
    ).all()
    speakers: dict[str, tuple[str, set[str]]] = {}
    for item_id, label, user_id in rows:
        speakers.setdefault(item_id, (label, set()))[1].add(user_id)

    filled: list[ExtActionItem] = []
    for item_id, (label, users) in list(speakers.items())[:FILL_CAP]:
        if len(users) != 1:
            # Model items have one source today; one with sources by two
            # people was never the speaker's alone, and a person decides.
            continue
        (user_id,) = users
        changed = session.scalar(
            update(ExtActionItem)
            .where(
                ExtActionItem.id == item_id,
                ExtActionItem.assignee_id.is_(None),
                ExtActionItem.assignee_label == label,
            )
            .values(assignee_id=user_id, assignee_label=None)
            .returning(ExtActionItem.id)
        )
        if changed is None:
            continue
        item = session.get(ExtActionItem, item_id, populate_existing=True)
        if item is not None:
            filled.append(item)
    return filled


def classify_utterances(
    classifier: Classifier,
    utterances: Sequence[TranscriptUtterance],
    *,
    consented: Collection[str],
) -> list[ClassifiedUtterance]:
    """Every utterance of a meeting, in spoken order, with the classifier's answer.

    Takes no session, on purpose. Classifying a meeting is the heaviest thing
    this module does -- about two minutes of CPU for a 45-minute meeting (#112)
    -- and a transaction held open around it holds its locks and a pooled
    connection for all of that time. The caller classifies first and opens a
    session only to write.

    Sorted by ``start`` because everything downstream reads meeting order:
    ``group_decisions`` counts its gap in utterances, and a payload is not
    promised to arrive sorted. The id breaks ties so two utterances starting at
    the same instant come out the same way on every run.

    Every utterance is returned, the ones the model calls none included --
    ``kind`` is ``None`` for those. They are what the decision gap is counted
    in; only ``store_classifications`` leaves them out.

    **Only ids in ``consented`` reach the classifier** (see
    ``consented_utterance_ids``). The rest stay in the sequence as a turn with
    no kind and no text: someone else spoke there, so the gap between two
    decisions is still counted in it, but what they said is never read,
    classified or stored. Dropping them instead would shorten every gap they sat
    in and weld two decisions into one. Keyword-only and required, so a caller
    cannot forget to filter by leaving it out.
    """
    ordered = sorted(utterances, key=lambda u: (u.start, u.id))
    analysed = [utterance for utterance in ordered if utterance.id in consented]
    predictions = classifier.classify([utterance.text for utterance in analysed])
    if len(predictions) != len(analysed):
        # The Protocol promises one per input, in order; zipping a short list
        # would label the wrong utterances without an error.
        raise ValueError(
            f"asked for {len(analysed)} predictions, the classifier returned {len(predictions)}"
        )
    answer = dict(zip((utterance.id for utterance in analysed), predictions, strict=True))
    return [
        ClassifiedUtterance(
            id=utterance.id,
            kind=prediction.kind,
            confidence=prediction.confidence,
            text=utterance.text,
            speaker=utterance.speaker,
        )
        if (prediction := answer.get(utterance.id)) is not None
        # No consent, so nothing of theirs is read -- not the text, and not who
        # they are. The turn is a gap of the right length and nothing more.
        else ClassifiedUtterance(id=utterance.id, kind=None, confidence=0.0, text="")
        for utterance in ordered
    ]


# --- step 4: NLI verification --------------------------------------------------


COMMITMENT_HYPOTHESIS = "화자가 이 일을 하겠다고 약속했다"
"""The one hypothesis every ``ambiguous`` utterance is checked against
(#12). Not per-utterance: #12 names this exact sentence, and a hypothesis
that changed per input would make two utterances' NLI scores incomparable."""

_NLI_CHECKED_KINDS = (UtteranceKind.AMBIGUOUS,)
"""What step 4 re-checks -- **promotion only**, not demotion. A first version
of this also moved a ``commitment`` row the other way, to ``ambiguous``, on a
non-entailed score; mkkim68's review of #330 found the demotion side unsafe
to ship yet and it was narrowed to this:

Demoting a ``commitment`` currently deletes it from the person's view with
nothing to replace it. ``build_action_items`` stops drafting a card for it
(it is no longer ``commitment``), and step 6 -- the confirmation DM
``ambiguous`` rows are supposed to get -- does not exist yet (blocked on
#70, #30; see ``tasks.py``'s own TODO). The item does not move to a
different queue; it disappears from both. Promotion has no such gap: an
``ambiguous`` row gaining a card is pure addition, nothing was showing for
it before.

This also cuts against ADR 0006's own ranking -- recall over precision,
because a wrong item costs a click and a missing one costs re-reading the
whole meeting -- on evidence that does not clear the bar for that trade:
#172's measured accuracy (dev 0.8185, held-out **XNLI** test 0.8273) is
zero-shot-transferred to meeting Korean and this module's own hypothesis
sentence, neither of which #172 measured.

Revisit demotion once step 6 exists (so a demoted row lands somewhere a
person can still see it) or once #10 measures this hypothesis on meeting
speech specifically."""


def verify_utterances(
    nli: NliModel, classified: Sequence[ClassifiedUtterance]
) -> list[ClassifiedUtterance]:
    """Step 4: NLI over ambiguous agreement (#12).

    Takes no session, on purpose -- same reason as ``classify_utterances``:
    this is model inference, and the caller runs it before opening a
    transaction, not inside one.

    Entailment moves an ``ambiguous`` row to ``commitment`` -- the speaker
    actually promised it, so it belongs in ``build_action_items`` and not in
    a confirmation DM. Neutral or contradiction leaves it ``ambiguous``,
    unchanged but for ``nli_verified=True``: #12's confirmation DM is the
    answer for weak assent that NLI also could not read as a promise. A
    ``commitment`` row is never re-checked -- see ``_NLI_CHECKED_KINDS``.

    ``confidence`` moves with a promotion, to NLI's own entailment
    probability, rather than the 5-way classifier's now-stale confidence in
    ``ambiguous``: a caller reading confidence afterward (ADR 0006's
    candidate threshold, ``build_action_items``) should read how sure the
    *last* model to look at this row was, not the first. A row NLI leaves
    ``ambiguous`` keeps the classifier's own confidence -- nothing about the
    5-way classifier's ambiguous-probability became stale, since the kind
    did not change. **This means a ``commitment`` row's ``confidence`` is
    the classifier's own (5-way softmax) unless it was promoted here, in
    which case it is NLI's (3-way softmax) -- the two are not the same
    distribution and are not directly comparable. #10's eventual
    ``candidate_confidence`` measurement has to either treat them
    separately or establish that one calibrates against the other; neither
    is done today, and the threshold stays unset (blank by default) until
    it is.**

    Every other kind, and anything the classifier called none, passes through
    with its original ``ClassifiedUtterance`` untouched.
    """
    targets = [u for u in classified if u.kind in _NLI_CHECKED_KINDS]
    if not targets:
        return list(classified)

    pairs = [(u.text, COMMITMENT_HYPOTHESIS) for u in targets]
    scores = nli.classify(pairs)
    if len(scores) != len(targets):
        # The Protocol promises one per input, in order; zipping a short list
        # would verify the wrong utterances without an error.
        raise ValueError(f"asked for {len(targets)} NLI results, the model returned {len(scores)}")

    promoted: dict[str, ClassifiedUtterance] = {
        utterance.id: replace(
            utterance, kind=UtteranceKind.COMMITMENT, confidence=score.entailment, nli_verified=True
        )
        for utterance, score in zip(targets, scores, strict=True)
        if score.label == "entailment"
    }
    checked = {u.id for u in targets} - promoted.keys()
    return [
        promoted[utterance.id]
        if utterance.id in promoted
        else replace(utterance, nli_verified=True)
        if utterance.id in checked
        else utterance
        for utterance in classified
    ]


def store_classifications(
    session: Session,
    *,
    meeting_id: str,
    utterances: Sequence[ClassifiedUtterance],
    model_version: str,
) -> int:
    """Replace this meeting's classifications. Returns how many rows it wrote.

    Delete-then-insert in the caller's transaction, which ``async-pipeline.md``
    allows for derived results: a redelivered task or a reprocessed meeting ends
    with one set of rows, the last one. A merge would keep a label the new model
    no longer gives, because the utterance it would be keyed on is now none and
    has no row to overwrite it with.

    Only the five kinds are written -- see ``ExtClassification``.
    """
    session.execute(delete(ExtClassification).where(ExtClassification.meeting_id == meeting_id))
    rows = [
        ExtClassification(
            utterance_id=utterance.id,
            meeting_id=meeting_id,
            kind=utterance.kind.value,
            confidence=utterance.confidence,
            model_version=model_version,
            nli_verified=utterance.nli_verified,
        )
        for utterance in utterances
        if utterance.kind is not None
    ]
    session.add_all(rows)
    session.flush()
    return len(rows)


def classifications_for_meeting(session: Session, meeting_id: str) -> list[Classification]:
    """This meeting's classifications as the contract describes them, in spoken
    order.

    Ordered by ``utterances.start_sec`` because the row carries no position and
    the order is what a reader of a meeting needs. The join is to a table this
    module may read and never writes.
    """
    rows = session.scalars(
        select(ExtClassification)
        .join(Utterance, Utterance.id == ExtClassification.utterance_id)
        .where(ExtClassification.meeting_id == meeting_id)
        .order_by(Utterance.start_sec, Utterance.id)
    ).all()
    return [
        Classification(
            utterance_id=row.utterance_id,
            kind=UtteranceKind(row.kind),
            confidence=row.confidence,
            nli_verified=row.nli_verified,
        )
        for row in rows
    ]


# --- step 3: action items from commitments ------------------------------------


def resolve_commitment_references(
    resolver: ReferenceResolver,
    classified: Sequence[ClassifiedUtterance],
) -> dict[str, str]:
    """Each commitment's description, references resolved against the utterances
    around it (#175): "그거 제가 할게요" reads as what "그거" was.

    Runs before any session, the same reason ``classify_utterances`` does -- it
    is model inference, and a transaction held around it holds a connection and
    its locks for the length of it.

    The context for a commitment is up to ``MAX_CONTEXT_UTTERANCES`` utterances
    immediately before it and ``MAX_CONTEXT_AFTER`` immediately after, whatever
    their kind -- an antecedent can live in a ``none`` utterance same as any
    other, and a clarifying exchange can come right after the commitment rather
    than before it. Both directions are available only because this runs over a
    finished transcript, never live. Only masked text ever reaches the resolver
    (privacy.md section 6), the same as everything else module B sends a model.

    **Windowed from ``classified``, never the raw transcript.** Found in review
    of #366: an earlier version cut context from ``utterances`` directly, which
    is neither filtered nor promised sorted. ``classify_utterances`` already
    blanks a non-consenting speaker's turn to ``text=""`` (privacy.md section
    5, "excluded utterances are not stored, not just hidden") and already
    orders every row by ``(start, id)`` regardless of payload order -- reading
    from ``utterances`` instead undid both. A resolver's whole job is copying
    words out of its context into the sentence it returns, so a leak here does
    not stop at the model: it lands in ``ext_action_items.description`` and, on
    confirmation, in Notion. Blank turns are filtered out of the window (``if
    u.text``) rather than skipped over to fill it back up to size -- a shorter
    window is still "the smallest window that resolves a reference" (section
    6); reaching past a non-consenting turn for one more line would not be.

    Returns ``{utterance_id: resolved_text}`` for commitments only. A caller
    reading an id this has no entry for was never a commitment and should keep
    the utterance's own text -- exactly what a resolver would have returned for
    it anyway, since one bad or unresolved reference never drops the request
    (see ``ReferenceResolver``).

    **This generates a sentence, and ``decisions._build`` refuses to.** That is
    not a disagreement inside the module -- a decision's statement is a record
    someone would write in the minutes, and a generated one would be wrong in a
    way the reader could not see. An action item's description is a draft ADR
    0006 has the user finish before it is asserted, sitting in
    ``needs_confirmation`` until they do; the resolver's own fallback rule
    (never fewer answers than requests, one bad reference degrades to the raw
    quote rather than failing the meeting) is what makes a generated sentence an
    acceptable draft here rather than a silent record.
    """
    return {
        utterance_id: resolution.text
        for utterance_id, resolution in resolve_commitment_summaries(resolver, classified).items()
    }


def resolve_commitment_summaries(
    resolver: ReferenceResolver,
    classified: Sequence[ClassifiedUtterance],
    *,
    kind: UtteranceKind = UtteranceKind.COMMITMENT,
) -> dict[str, Resolution]:
    """``resolve_commitment_references`` with the lines each sentence was written from.

    The same window as before -- ``MAX_CONTEXT_UTTERANCES`` lines before a
    commitment and ``MAX_CONTEXT_AFTER`` after, blank turns dropped -- and, for a
    resolver that can say which lines it used (``resolve_with_evidence``), up to
    ``related.MAX_RELATED`` more from the rest of the meeting that are about the
    same thing. Those are candidates the model may cite, never lines anyone is
    shown until it does. A resolver that cannot cite is not handed them: the
    retrieval is skipped, not run for nothing.

    Read from ``classified`` for the reason ``resolve_commitment_references``
    gives: it is already ordered, and an excluded speaker's turn is already
    blank. Both the window and the candidates come out of the same filtered
    sequence, so a line from a speaker who did not consent is in neither.

    ``kind`` names which utterances are summarised; ``confirmed_summaries`` marks
    the agreements their speakers confirmed and passes that mark.
    """
    commitments = [u for u in classified if u.kind is kind]
    if not commitments:
        return {}

    cites = callable(getattr(resolver, "resolve_with_evidence", None))
    lines = [(u.id, u.text) for u in classified if u.text]
    said = dict(lines)
    position = {utterance.id: index for index, utterance in enumerate(classified)}
    requests = []
    for utterance in commitments:
        index = position[utterance.id]
        start = max(0, index - MAX_CONTEXT_UTTERANCES)
        before = [u for u in classified[start:index] if u.text]
        after_end = index + 1 + MAX_CONTEXT_AFTER
        after = [u for u in classified[index + 1 : after_end] if u.text]
        offered = (
            related_ids(utterance.id, lines, exclude={u.id for u in (*before, *after)})
            if cites
            else []
        )
        requests.append(
            ResolutionRequest(
                target=utterance.text,
                context=tuple(u.text for u in before),
                context_after=tuple(u.text for u in after),
                target_id=utterance.id,
                context_ids=tuple(u.id for u in before),
                context_after_ids=tuple(u.id for u in after),
                related=tuple((line_id, said[line_id]) for line_id in offered),
            )
        )

    if cites:
        resolved = resolver.resolve_with_evidence(requests)  # type: ignore[attr-defined]
    else:
        resolved = [Resolution(text) for text in resolver.resolve(requests)]
    return dict(zip((u.id for u in commitments), resolved, strict=True))


def decision_day(session: Session, meeting_id: str) -> date | None:
    """The meeting's date in Korea, for a run that has to read it before any
    decision is built (``resolve_decision_summaries`` runs outside a session)."""
    meeting = session.get(Meeting, meeting_id)
    return meeting_day(meeting.started_at if meeting is not None else None)


def resolve_decision_summaries(
    resolver: ReferenceResolver,
    classified: Sequence[ClassifiedUtterance],
    *,
    meeting_id: str,
    day: date | None = None,
    max_gap: int = DEFAULT_MAX_GAP,
) -> dict[str, Resolution]:
    """A model's write-up of each decision, keyed by its ``dec_`` id.

    Only a resolver that can cite (``resolve_with_evidence``) writes one, and only
    for a decision whose settling turn does not say what was decided
    (``decisions.needs_write_up``); for any other this is empty and the decision
    keeps the assembled, tidied line. Like
    ``resolve_commitment_summaries`` it runs before any session -- it is model
    inference -- and reads only ``classified``: ordered, and with a non-consenting
    speaker's turn already blank.

    What the model is given for a decision: the turn that carries its substance
    (``DecisionGroup.core_text``) as the target; the lines around the whole run of
    decision turns, from ``MAX_CONTEXT_UTTERANCES`` before its first to
    ``MAX_CONTEXT_AFTER`` after its last, the other decision turns included; and up
    to ``related.MAX_RELATED`` more from elsewhere in the meeting. It writes what
    was decided and says which numbered lines it used.
    """
    if not callable(getattr(resolver, "resolve_with_evidence", None)):
        return {}
    groups = [g for g in group_decisions(classified, max_gap=max_gap, day=day) if needs_write_up(g)]
    if not groups:
        return {}

    lines = [(u.id, u.text) for u in classified if u.text]
    said = dict(lines)
    position = {u.id: index for index, u in enumerate(classified)}
    requests = []
    keys = []
    for group in groups:
        here = position[group.substance_id]
        first = min(group.first_position, here)
        last = max(group.last_position, here)
        before = [u for u in classified[max(0, first - MAX_CONTEXT_UTTERANCES) : here] if u.text]
        after = [u for u in classified[here + 1 : last + 1 + MAX_CONTEXT_AFTER] if u.text]
        offered = related_ids(group.substance_id, lines, exclude={u.id for u in (*before, *after)})
        requests.append(
            ResolutionRequest(
                target=group.core_text,
                context=tuple(u.text for u in before),
                context_after=tuple(u.text for u in after),
                target_id=group.substance_id,
                context_ids=tuple(u.id for u in before),
                context_after_ids=tuple(u.id for u in after),
                purpose="decision",
                related=tuple((line_id, said[line_id]) for line_id in offered),
            )
        )
        keys.append(decision_id(meeting_id, group.source_utterance_ids))
    resolved = resolver.resolve_with_evidence(requests)  # type: ignore[attr-defined]
    return dict(zip(keys, resolved, strict=True))


def source_digest(texts: Sequence[str]) -> str:
    """sha256 of the masked texts an item or decision was drawn from, or a
    confirmation DM quotes, in source order -- not reversible, the #518
    consent-key pattern. What a later run compares to notice that a line was
    corrected (#586)."""
    return hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest()


def stored_digest(session: Session, utterance_ids: Sequence[str]) -> str | None:
    """``source_digest`` over the stored text of ``utterance_ids``; ``None`` when
    there are none or one is gone."""
    if not utterance_ids:
        return None
    texts = dict(
        session.execute(select(Utterance.id, Utterance.text).where(Utterance.id.in_(utterance_ids)))
        .tuples()
        .all()
    )
    if len(texts) != len(set(utterance_ids)):
        return None
    return source_digest([texts[u] for u in utterance_ids])


@dataclass(frozen=True)
class SourceCorrections:
    """What ``apply_source_corrections`` changed, by id -- the ones whose copies
    in Notion, Jira and calendars must follow (``copies_follow``)."""

    changed_items: tuple[str, ...] = ()
    changed_decisions: tuple[str, ...] = ()
    flagged: int = 0


def _edited_description(session: Session, action_item_id: str) -> bool:
    for fields in session.scalars(
        select(ExtEditEvent.fields).where(
            ExtEditEvent.action_item_id == action_item_id, ExtEditEvent.kind == "edited"
        )
    ):
        if fields is None or "description" in fields.split(","):
            return True
    return False


def apply_source_corrections(
    session: Session, *, meeting_id: str, spoken: Mapping[str, str]
) -> SourceCorrections:
    """Fix or flag what was drawn from a line that has since been corrected (#586).

    A PII report masks stored lines again and republishes ``TranscriptReady``
    without naming them. ``spoken`` is that payload's text by utterance id. Every
    item and decision of the meeting whose sources now hash differently from
    ``source_digest`` is handled -- **even in a meeting a person has edited**,
    which ``build_action_items`` otherwise leaves alone (ADR 0006):

    - an item whose description is the line itself reads the corrected line,
      tidied; a model's summary is replaced the same way and flagged
      ``needs_recheck`` (a summary is not rewritten here: no model call on a
      correction); a person's own text is only flagged -- B cannot tell which
      words of theirs were the private ones;
    - ``due_text``, a fragment of the line, is read again from the new text;
    - a model decision was rebuilt from the new text in this same run; one a
      person typed, or reworded, is flagged.

    A row with no digest yet records one and changes nothing. Runs after this
    run's rebuild, so what it rebuilt already matches. No edit events: no person
    corrected anything. Returns the rows whose outside copies must follow: the
    confirmed ones, and an item moved back to 확인 필요 that still has a copy
    outside (``copies_follow``).
    """
    meeting = session.get(Meeting, meeting_id)
    day = meeting_day(meeting.started_at if meeting is not None else None)
    changed: list[str] = []
    flagged = 0
    for item in session.scalars(
        select(ExtActionItem)
        .where(ExtActionItem.meeting_id == meeting_id)
        .options(selectinload(ExtActionItem.sources))
    ):
        ids = live_source_ids(item)
        texts = [spoken.get(u) for u in ids]
        if not ids or any(t is None for t in texts):
            continue
        digest = source_digest([t for t in texts if t is not None])
        if item.source_digest == digest:
            continue
        first = item.source_digest is None
        item.source_digest = digest
        if first:
            continue
        line = texts[0] or ""
        if item.origin == "user" or _edited_description(session, item.id):
            item.needs_recheck = True
        elif item.description_resolved:
            item.description = tidy(line)
            item.description_resolved = False
            item.needs_recheck = True
        elif item.origin in ("model", "chat"):
            item.description = tidy(line)
        if item.due_text is not None:
            due = parse_due(line, day)
            item.due_text = due.text if due is not None else None
        flagged += int(item.needs_recheck)
        if copies_follow(session, item):
            changed.append(item.id)

    reviews = {
        review.decision_id: review
        for review in session.scalars(
            select(ExtDecisionReview).where(ExtDecisionReview.meeting_id == meeting_id)
        )
    }
    paged = decisions_with_a_page(session, list(reviews))
    changed_decisions: list[str] = []
    for decision in session.scalars(
        select(ExtDecision)
        .where(ExtDecision.meeting_id == meeting_id)
        .options(selectinload(ExtDecision.sources))
    ):
        ids = [s.utterance_id for s in sorted(decision.sources, key=lambda s: s.position)]
        texts = [spoken.get(u) for u in ids]
        if not ids or any(t is None for t in texts):
            continue
        digest = source_digest([t for t in texts if t is not None])
        if decision.source_digest == digest:
            continue
        first = decision.source_digest is None
        decision.source_digest = digest
        if first:
            continue
        review = reviews.get(decision.id)
        if decision.origin == "user" or (review is not None and review.statement):
            decision.needs_recheck = True
            flagged += 1
        if (review is not None and review.status == "confirmed") or decision.id in paged:
            changed_decisions.append(decision.id)
    session.flush()
    return SourceCorrections(tuple(changed), tuple(changed_decisions), flagged)


def build_action_items(
    session: Session,
    *,
    meeting_id: str,
    utterances: Sequence[TranscriptUtterance],
    classified: Sequence[ClassifiedUtterance],
    resolved: Mapping[str, str] | None = None,
    related: Mapping[str, Sequence[str]] | None = None,
    confirmed: Mapping[str, Resolution] | None = None,
) -> list[ExtActionItem] | None:
    """One draft item per commitment, replacing the model's previous draft.

    Returns ``None``, and changes nothing, once a person has corrected anything
    in this meeting. ADR 0006 makes the output a draft the user finishes, and a
    reprocessed meeting that replaced their finished list with a fresh draft
    would throw their work away -- an edited item reset, a deleted one back.
    ``ext_edit_events`` is the record that they started, and it is only ever
    written by a person.

    Otherwise the meeting's ``origin="model"`` items are deleted and rebuilt,
    the same replace-not-merge rule as the classifications. Items a person
    typed are never touched.

    Each item is filled by ``slots``: the speaker as the assignee, the first
    date phrase as the due date. The description is ``resolved``'s entry for
    the utterance when there is one (#175) and the utterance's own text
    otherwise -- ``resolved`` defaults to empty, so a caller that has not run
    ``resolve_commitment_references`` gets exactly the pre-#175 behaviour. Every
    model item starts in *needs confirmation*.

    **The due date is still read from the utterance's own text, not the
    resolved one.** ``parse_due`` depends on the exact verb ending the speaker
    used, and a resolver rewriting the sentence for a human reader is not
    obliged to preserve it.

    ``related`` maps a commitment's utterance id to the other lines its summary
    was written from (``resolve_commitment_summaries``); they are kept in
    ``ext_action_item_related`` for the drawer. Only ids of this meeting's
    utterances are kept, and an id that is the commitment itself is not a
    "related" line.
    """
    resolved = resolved or {}
    related = related or {}
    edited = session.scalar(
        select(func.count()).select_from(ExtEditEvent).where(ExtEditEvent.meeting_id == meeting_id)
    )
    if edited:
        log.info("extraction_action_items_kept", meeting_id=meeting_id, edits=edited)
        return None

    meeting = session.get(Meeting, meeting_id)
    day = meeting_day(meeting.started_at if meeting is not None else None)
    spoken = {utterance.id: utterance for utterance in utterances}
    # One read of ``users`` for the whole meeting: an id that is not there
    # would fail the foreign key and take every item with it.
    speaker_ids = {u.speaker_id for u in utterances if u.speaker_id is not None}
    known = (
        set(session.scalars(select(User.id).where(User.id.in_(speaker_ids))))
        if speaker_ids
        else set()
    )

    # Locked before anything is deleted, in one order, so a speaker's click on
    # this meeting's DM waits for the rebuild or the rebuild for the click --
    # never both writing a draft (``resolve_confirmation``).
    answers = {
        row.utterance_id: row.resolved_kind
        for row in session.scalars(
            select(ExtConfirmation)
            .where(ExtConfirmation.meeting_id == meeting_id)
            .order_by(ExtConfirmation.utterance_id)
            .with_for_update()
        )
    }

    for stale in session.scalars(
        select(ExtActionItem).where(
            ExtActionItem.meeting_id == meeting_id, ExtActionItem.origin == "model"
        )
    ):
        session.delete(stale)

    items = []
    for utterance in classified:
        if utterance.kind is not UtteranceKind.COMMITMENT:
            continue
        answer = answers.get(utterance.id)
        if answer is not None and answer != UtteranceKind.COMMITMENT.value:
            # Its speaker said it was not a promise. ``withdraw_confirmed_draft``
            # took the item back; a rerun that still reads a commitment must not
            # bring it back (#529 review).
            continue
        said = spoken[utterance.id]
        assignee = assignee_of(said.speaker_id, said.speaker, known=known)
        due = parse_due(said.text, day)
        # ``description_resolved`` is about the resolver's rewrite alone; tidying
        # is a fixed rule, not a model's paraphrase, and the original is beside it.
        rewritten = resolved.get(utterance.id, said.text)
        description = tidy(rewritten)
        cited = [u for u in related.get(utterance.id, ()) if u in spoken and u != utterance.id]
        items.append(
            ExtActionItem(
                meeting_id=meeting_id,
                description=description,
                description_resolved=rewritten != said.text,
                assignee_id=assignee.user_id,
                assignee_label=assignee.label,
                due_date=due.date if due is not None else None,
                due_text=due.text if due is not None else None,
                status=ActionStatus.NEEDS_CONFIRMATION.value,
                confidence=utterance.confidence,
                origin="model",
                sources=[ExtActionItemSource(utterance_id=utterance.id)],
                related=[ExtActionItemRelated(utterance_id=u) for u in cited],
            )
        )
    session.add_all(items)
    session.flush()
    # A draft a speaker's own answer made was one of the rows deleted above.
    # ``ext_confirmations`` is what outlives a rerun, so the draft is derived
    # from it again -- and an utterance the classifier now calls a commitment
    # already has its item, which ``draft_confirmed_commitment`` returns as is.
    for confirmation in session.scalars(
        select(ExtConfirmation).where(
            ExtConfirmation.meeting_id == meeting_id,
            ExtConfirmation.resolved_kind == UtteranceKind.COMMITMENT.value,
        )
    ):
        drafted = draft_confirmed_commitment(
            session, confirmation, (confirmed or {}).get(confirmation.utterance_id)
        )
        if drafted is not None and drafted not in items:
            items.append(drafted)
    return items


# --- steps 4 and 6: ambiguous agreement ----------------------------------------


def record_ambiguous_agreements(
    session: Session, *, meeting_id: str, classified: Sequence[ClassifiedUtterance]
) -> int:
    """A row in ``ext_confirmations`` for every utterance classified ambiguous.

    Written before any DM, with ``sent_at`` empty: the ambiguity exists whether
    or not anyone can be asked about it yet, and E counts it either way
    (``AmbiguousAgreement.confirmation_sent`` is false until one goes out).

    On a rerun, a row whose utterance is no longer ambiguous is removed **only
    if it was never asked** -- then it is derived data, and replacing it is the
    same rule as the classifications. A row a DM went out for stays: the speaker
    has the question in front of them, and may already have answered it.

    **Both writes are one statement each, decided by the database** -- never by
    rows this function read a moment earlier. Two things can change a row in
    between: a sender can put the question to the speaker (``open_confirmation``
    fills ``sent_at``), and a redelivered task (``acks_late``) can record the
    same meeting at the same time. A delete chosen from a stale read would
    remove a question the speaker already has, and their answer would then land
    on nothing; an insert chosen from one would fail on the primary key. So the
    delete carries ``sent_at IS NULL`` in its own WHERE, and the insert skips a
    row that is already there.

    Returns how many of this run's utterances were ambiguous.
    """
    ambiguous = [u.id for u in classified if u.kind is UtteranceKind.AMBIGUOUS]
    session.execute(
        delete(ExtConfirmation)
        .where(
            ExtConfirmation.meeting_id == meeting_id,
            ExtConfirmation.utterance_id.not_in(ambiguous),
            ExtConfirmation.sent_at.is_(None),
        )
        .execution_options(synchronize_session="fetch")
    )
    if ambiguous:
        session.execute(
            _insert_if_absent(session)
            .values(
                [
                    {
                        "utterance_id": utterance_id,
                        "meeting_id": meeting_id,
                        "reason": WEAK_ASSENT,
                        "sent_at": None,
                    }
                    for utterance_id in ambiguous
                ]
            )
            .on_conflict_do_nothing(index_elements=["utterance_id"])
        )
    return len(ambiguous)


def _insert_if_absent(session: Session) -> postgresql.Insert | sqlite.Insert:
    """An ``ext_confirmations`` insert that can take ``ON CONFLICT DO NOTHING``.

    The clause is spelled the same on both databases but built per dialect:
    PostgreSQL is every deployment, SQLite is the unit suite.
    """
    if session.get_bind().dialect.name == "postgresql":
        return postgresql.insert(ExtConfirmation)
    return sqlite.insert(ExtConfirmation)


def unasked_confirmations(session: Session, meeting_id: str) -> list[ExtConfirmation]:
    """The meeting's ambiguous agreements no DM has gone out for.

    What a sender walks once there is one: for each row, resolve the speaker's
    Slack account and call ``ask_for_confirmation``, which starts the clock on
    this same row — or returns ``None``, having found that another sender got
    there first and sent nothing. Nothing calls it yet (#70, #30).

    The list is read outside any lock, so a row here can be claimed between this
    query and the send. That is what the claim is for: the walker does not have
    to be the only one.
    """
    return list(
        session.scalars(
            select(ExtConfirmation)
            .where(ExtConfirmation.meeting_id == meeting_id, ExtConfirmation.sent_at.is_(None))
            .order_by(ExtConfirmation.utterance_id)
        )
    )


@dataclass(frozen=True)
class DueReminder:
    """One reminder owed: which item, which kind, and who it is for. The
    recipient is the item's assignee and nothing else -- there is no field a
    caller could put another person in."""

    action_item_id: str
    meeting_id: str
    team_id: str
    assignee_id: str
    kind: str
    due_date: date
    description: str
    meeting_title: str | None


def due_reminders_to_send(session: Session, *, now: datetime) -> list[DueReminder]:
    """The reminders owed at ``now`` and not yet sent (``reminders``).

    An item is owed one when it is confirmed and not done, has a due date
    that puts it a day ahead or up to ``OVERDUE_DAYS`` behind in Korea's
    calendar, and its assignee is an account on the meeting's team -- the
    same person ``calendar_sync._calendar_owner`` acts for. A typed name has
    nobody to tell; someone who left the team is not told about its work
    (ADR 0007). A meeting past its retention window is left out even before
    the sweep removes it. Nothing at all outside the sending hours.
    """
    if not reminders.sending_hours(now):
        return []
    today = reminders.korean_day(now)
    rows = session.execute(
        select(ExtActionItem, Meeting.team_id, Meeting.title)
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .join(
            TeamMember,
            and_(
                TeamMember.team_id == Meeting.team_id,
                TeamMember.user_id == ExtActionItem.assignee_id,
            ),
        )
        .where(
            ExtActionItem.status.in_([ActionStatus.TODO.value, ActionStatus.IN_PROGRESS.value]),
            ExtActionItem.due_date.is_not(None),
            ExtActionItem.due_date >= today - timedelta(days=reminders.OVERDUE_DAYS),
            ExtActionItem.due_date <= today + timedelta(days=1),
            or_(Meeting.expires_at.is_(None), Meeting.expires_at > now),
            # Somebody who turned them off is not told (review of #751).
            ExtActionItem.assignee_id.not_in(select(ExtDueReminderOptOut.user_id)),
        )
        .order_by(ExtActionItem.due_date, ExtActionItem.id)
    ).all()
    sent = {
        (item_id, kind, due)
        for item_id, kind, due in session.execute(
            select(
                ExtDueReminder.action_item_id, ExtDueReminder.kind, ExtDueReminder.due_date
            ).where(ExtDueReminder.action_item_id.in_([item.id for item, _, _ in rows]))
        )
    }
    owed: list[DueReminder] = []
    for item, team_id, title in rows:
        kind = reminders.kind_for(item.due_date, today)
        if kind is None or (item.id, kind, item.due_date) in sent:
            continue
        owed.append(
            DueReminder(
                action_item_id=item.id,
                meeting_id=item.meeting_id,
                team_id=team_id,
                assignee_id=item.assignee_id,
                kind=kind,
                due_date=item.due_date,
                description=item.description,
                meeting_title=title,
            )
        )
    return owed


def send_due_reminder(
    session: Session, slack: SlackApi, reminder: DueReminder, *, now: datetime
) -> bool:
    """Claim the reminder and send it, in that order -- or send nothing.

    **The item is read again first, and locked.** The list this reminder came
    from was read in another transaction, seconds or more ago. Since then
    the item may have been given to somebody else -- the message would go to
    the person who no longer holds it -- finished, moved to another date, or
    deleted. It is sent only if the item is still open, still due on that
    date, and still assigned to the same account on the meeting's team
    (review of #751). Anything else sends nothing and claims nothing; the
    next run reads the item as it now is.

    The claim is the row in ``ext_due_reminders``, inserted only if absent, so
    two runs cannot both send: the second finds the row and returns false.
    The send is inside the caller's transaction with the claim, so a failed
    send takes the claim back and the next run tries again -- the shape
    ``ask_for_confirmation`` uses for its DM. It goes to the item's assignee,
    whom the reminder carries; there is no other recipient to pass.
    """
    item = session.scalar(
        select(ExtActionItem).where(ExtActionItem.id == reminder.action_item_id).with_for_update()
    )
    if (
        item is None
        or item.status not in (ActionStatus.TODO.value, ActionStatus.IN_PROGRESS.value)
        or item.due_date != reminder.due_date
        or item.assignee_id != reminder.assignee_id
        or not _is_team_member(session, user_id=reminder.assignee_id, team_id=reminder.team_id)
        or not due_reminders_on(session, reminder.assignee_id)
    ):
        return False
    claimed = session.execute(
        _insert_if_absent_into(session, ExtDueReminder)
        .values(
            action_item_id=reminder.action_item_id,
            kind=reminder.kind,
            due_date=reminder.due_date,
            sent_at=now,
        )
        .on_conflict_do_nothing(index_elements=["action_item_id", "kind", "due_date"])
        .returning(ExtDueReminder.action_item_id)
    ).first()
    if claimed is None:
        return False
    slack.send_dm(
        reminder.assignee_id,
        reminders.build_due_reminder(
            reminder.kind,
            # As it reads now, not as it read when the list was made.
            description=item.description,
            due_date=reminder.due_date,
            meeting_title=reminder.meeting_title,
            board_url=answer_url(reminder.meeting_id),
        ),
    )
    return True


def due_reminders_on(session: Session, user_id: str) -> bool:
    """Whether this person gets due-date reminders: yes unless they turned them
    off (``ExtDueReminderOptOut``)."""
    return session.get(ExtDueReminderOptOut, user_id) is None


def set_due_reminders(session: Session, user_id: str, *, on: bool, now: datetime) -> bool:
    """Turn this person's own reminders on or off; what they are now. Only the
    caller's own -- the route passes the signed-in person, and there is no way
    to name another."""
    row = session.get(ExtDueReminderOptOut, user_id)
    if on and row is not None:
        session.delete(row)
    elif not on and row is None:
        session.add(ExtDueReminderOptOut(user_id=user_id, created_at=now))
    session.flush()
    return on


@dataclass(frozen=True)
class PendingQuestion:
    """One ambiguous agreement ready to be asked about: ids only. The quoted
    utterance is read again inside the transaction that sends it."""

    utterance_id: str
    meeting_id: str
    team_id: str
    speaker_id: str


def confirmations_to_ask(session: Session, *, now: datetime | None = None) -> list[PendingQuestion]:
    """Every ambiguous agreement across meetings that a DM can go out for now.

    Not yet asked; recorded within ``CONFIRMATION_TIMEOUT`` -- a question put
    days after the meeting reads as noise, and one found before the sender
    existed is past its window by the same rule; and spoken by an identified
    speaker who consented. The DM quotes the speaker's own words to the
    speaker and nobody else (``send_confirmation_dm``), so a line with no
    account behind it has nobody to go to: it waits, and is asked if the
    speaker is identified inside the window (#360).
    """
    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(
            ExtConfirmation.utterance_id,
            ExtConfirmation.meeting_id,
            Meeting.team_id,
            Participant.user_id,
        )
        .join(Meeting, Meeting.id == ExtConfirmation.meeting_id)
        .join(Utterance, Utterance.id == ExtConfirmation.utterance_id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .join(User, User.id == Participant.user_id)
        .where(
            ExtConfirmation.sent_at.is_(None),
            ExtConfirmation.created_at >= moment - CONFIRMATION_TIMEOUT,
            Participant.consented.is_(True),
        )
        .order_by(ExtConfirmation.meeting_id, ExtConfirmation.utterance_id)
    )
    return [
        PendingQuestion(
            utterance_id=utterance_id, meeting_id=meeting_id, team_id=team_id, speaker_id=user_id
        )
        for utterance_id, meeting_id, team_id, user_id in rows
    ]


# --- review before anything leaves (#246) ------------------------------------


def _suggested(confidence: float) -> bool | None:
    threshold = get_settings().candidate_confidence
    return None if threshold is None else confidence >= threshold


def _review_decision_row(
    decision: ExtDecision,
    review: ExtDecisionReview | None,
    refs: Iterable[ExtDecisionRef],
    summary: str | None,
) -> ReviewDecision:
    """Assemble one row from already-fetched pieces.

    Pure and query-free so both call shapes -- one decision fetched by itself,
    or a meeting's worth fetched in batches -- build the same row the same way
    without either one re-running the other's queries (#296).
    """
    return ReviewDecision(
        id=decision.id,
        statement=_confirmed_statement(decision, review),
        model_statement=decision.statement,
        confidence=decision.confidence,
        origin=decision.origin,  # type: ignore[arg-type]
        needs_recheck=bool(decision.needs_recheck),
        status=review.status if review else "pending",  # type: ignore[arg-type]
        suggested=_suggested(decision.confidence),
        source_utterance_ids=[
            source.utterance_id for source in sorted(decision.sources, key=lambda s: s.position)
        ],
        sync_refs=[
            ExternalRefRead(system=ref.system, url=ref.url, external_id=ref.external_id)  # type: ignore[arg-type]
            for ref in refs
            # A claim with no page under a decision that is not confirmed is
            # a retired page (#669), not a send in flight: nothing to show.
            if ref.external_id is not None or (review is not None and review.status == "confirmed")
        ],
        summary=summary,
    )


def _read_decision(
    session: Session, decision: ExtDecision, *, refs: Sequence[ExtDecisionRef] | None = None
) -> ReviewDecision:
    """One decision, built the way ``review_for_meeting`` builds each of its rows.

    For a caller that already has the one decision it needs -- confirming it,
    or just having created it -- rather than for listing a meeting's decisions.
    ``review_for_meeting`` keeps its own batched version of this construction
    for that case: querying reviews, refs and summaries once for every decision
    in the meeting is the efficient shape there, and calling this helper once
    per decision from inside that loop would turn one query into N (#296).

    ``refs`` lets a caller that already knows the answer -- ``create_decision``,
    whose row cannot have any sync refs yet -- skip that query; left out, it is
    fetched here.
    """
    review = session.get(ExtDecisionReview, decision.id)
    if refs is None:
        refs = list(
            session.scalars(
                select(ExtDecisionRef)
                .where(ExtDecisionRef.decision_id == decision.id)
                .order_by(ExtDecisionRef.created_at)
            )
        )
    summary = decision_summaries(session, [decision]).get(decision.id)
    return _review_decision_row(decision, review, refs, summary)


def decision_related_utterances(session: Session, decision_id_: str) -> list[SourceUtterance]:
    """The lines a decision's write-up says it used, in spoken order, consenting
    speakers' and non-blank only (see ``related_utterances``)."""
    rows = session.execute(
        select(Utterance.id, Utterance.text)
        .join(ExtDecisionRelated, ExtDecisionRelated.utterance_id == Utterance.id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .where(
            ExtDecisionRelated.decision_id == decision_id_,
            Participant.consented.is_(True),
            func.length(func.trim(Utterance.text)) > 0,
        )
        .order_by(Utterance.start_sec, Utterance.id)
    ).all()
    return [SourceUtterance(id=uid, text=text) for uid, text in rows]


def read_decision_detail(session: Session, decision: ExtDecision) -> DecisionDetail:
    """One decision with the utterances it was settled in, in spoken order.

    Reads ``utterances``, which module A owns and this module may only read. An
    utterance that has been deleted takes its link row with it, so a missing
    quotation means the speech is gone.
    """
    row = _read_decision(session, decision)
    quoted = session.execute(
        select(Utterance.id, Utterance.text)
        .join(ExtDecisionSource, ExtDecisionSource.utterance_id == Utterance.id)
        .where(ExtDecisionSource.decision_id == decision.id)
        .order_by(ExtDecisionSource.position)
    ).all()
    return DecisionDetail(
        **row.model_dump(),
        sources=[SourceUtterance(id=uid, text=text) for uid, text in quoted],
        context=context_before(session, [uid for uid, _ in quoted]),
        related=decision_related_utterances(session, decision.id),
    )


def meeting_summary(
    session: Session, meeting_id: str, *, now: datetime | None = None
) -> MeetingSummary:
    """S15's 요약 tab, v1 (#421): what the meeting settled and left, from B's rows.

    Decisions as the review reads them (a person's wording when there is one),
    confirmed first and without the rejected; every action item; how many
    questions were asked and how many ambiguous agreements still wait for
    their speaker; and the team's memo. No model and nothing leaves, so it
    serves a real meeting whatever #392 decides.
    """
    review = review_for_meeting(session, meeting_id, now=now)
    kept = [d for d in review.decisions if d.status in ("confirmed", "pending")]
    kept.sort(key=lambda d: d.status != "confirmed")  # stable: settled order within
    open_questions = session.scalar(
        select(func.count())
        .select_from(ExtClassification)
        .where(
            ExtClassification.meeting_id == meeting_id,
            ExtClassification.kind == UtteranceKind.OPEN_QUESTION.value,
        )
    )
    note = session.get(ExtMeetingNote, meeting_id)
    return MeetingSummary(
        meeting_id=meeting_id,
        decisions=[
            SummaryDecision(id=d.id, statement=d.statement, status=d.status)  # type: ignore[arg-type]
            for d in kept
        ],
        action_items=list_action_items(session, meeting_id=meeting_id),
        open_questions=open_questions or 0,
        ambiguous_waiting=sum(
            1 for a in review.ambiguous_agreements if a.outcome in ("not_asked", "pending")
        ),
        note=note.body if note is not None else None,
        note_updated_at=note.updated_at if note is not None else None,
    )


def set_meeting_note(session: Session, meeting_id: str, body: str) -> ExtMeetingNote | None:
    """Replace the team's memo on the summary tab; a blank one removes it.

    Whole-memo writes, last one wins -- a memo is a few lines two people are
    unlikely to type at once, and a merge of two free texts has no right answer.
    """
    text = body.strip()
    note = session.get(ExtMeetingNote, meeting_id)
    if not text:
        if note is not None:
            session.delete(note)
            session.flush()
        return None
    if note is None:
        note = ExtMeetingNote(meeting_id=meeting_id, body=text)
        session.add(note)
    else:
        note.body = text
        note.updated_at = datetime.now(UTC)
    session.flush()
    return note


def review_for_meeting(
    session: Session, meeting_id: str, *, now: datetime | None = None
) -> MeetingReview:
    """What S15 puts in front of a person before they confirm and send.

    Decisions come with their verdict so far; ambiguous agreements with where the
    speaker's DM stands; action items only when they still need somebody -- status
    ``needs_confirmation``, or below the candidate line.
    """
    decisions = session.scalars(
        select(ExtDecision)
        .options(selectinload(ExtDecision.sources))
        .where(ExtDecision.meeting_id == meeting_id)
        .order_by(ExtDecision.created_at, ExtDecision.id)
    ).all()
    reviews = {
        review.decision_id: review
        for review in session.scalars(
            select(ExtDecisionReview).where(ExtDecisionReview.meeting_id == meeting_id)
        )
    }
    refs_by_decision: dict[str, list[ExtDecisionRef]] = {}
    for ref in session.scalars(
        select(ExtDecisionRef)
        .where(ExtDecisionRef.meeting_id == meeting_id)
        .order_by(ExtDecisionRef.created_at)
    ):
        refs_by_decision.setdefault(ref.decision_id, []).append(ref)
    summaries = decision_summaries(session, decisions)

    listed = [
        _review_decision_row(
            decision,
            reviews.get(decision.id),
            refs_by_decision.get(decision.id, []),
            summaries.get(decision.id),
        )
        for decision in decisions
    ]

    confirmations = session.scalars(
        select(ExtConfirmation)
        .where(ExtConfirmation.meeting_id == meeting_id)
        .order_by(ExtConfirmation.utterance_id)
    ).all()
    items = [
        item
        for item in list_action_items(session, meeting_id=meeting_id)
        if item.status == ActionStatus.NEEDS_CONFIRMATION.value
    ]
    return MeetingReview(
        meeting_id=meeting_id,
        decisions=listed,
        ambiguous_agreements=[
            ReviewAmbiguous(
                utterance_id=row.utterance_id,
                outcome=row.outcome_at(now),  # type: ignore[arg-type]
                resolved_kind=row.resolved_kind,
            )
            for row in confirmations
        ],
        action_items=items,
        pending_decisions=sum(1 for decision in listed if decision.status == "pending"),
    )


def review_decision(
    session: Session, decision: ExtDecision, payload: DecisionReviewUpdate
) -> ReviewDecision:
    """Record a verdict and/or a rewording on one decision.

    Sending the model's own wording clears the rewording instead of storing a copy
    that would stop tracking the model's text after a rerun. A body with neither
    field changes nothing.
    """
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        review = session.get(ExtDecisionReview, decision.id)
        if review is None:
            review = ExtDecisionReview(
                decision_id=decision.id, meeting_id=decision.meeting_id, status="pending"
            )
            session.add(review)
        if changes.get("status") is not None:
            review.status = changes["status"]
        if "statement" in changes:
            wording = changes["statement"]
            if decision.origin == "user":
                # A person's own decision has no model wording to keep beside
                # theirs; the rewording is the statement.
                if wording is not None:
                    decision.statement = wording
                review.statement = None
            else:
                review.statement = None if wording in (None, decision.statement) else wording
        if review.status == "rejected":
            # Rejecting drops the rewording whichever way it was asked for, as
            # ``delete_decision`` does. Left behind, it would come back with the
            # decision when the rejection is undone -- wording nobody typed this
            # time, sent to D, E and outbound as if confirmed.
            review.statement = None
        # A person has reviewed it again: a corrected source has been in front
        # of them (#586).
        decision.needs_recheck = False
        session.flush()

    return _read_decision(session, decision)


def outbound_for_meeting(session: Session, meeting_id: str) -> Outbound:
    """Exactly what may leave for Notion, Jira or Slack: nothing unconfirmed (#246).

    A decision goes only when a person confirmed it, in their wording if they gave
    one. An action item goes only once it is past ``needs_confirmation`` -- the
    status S17 moves it out of when somebody accepts it. The sync (#30) is to read
    this and nothing else, so the gate is one function rather than a rule every
    sender has to remember.

    **It screens as well as selects.** A rewording and an edited description are
    typed by a person and never went through module A's masker, so each text is
    run through ``find_unmasked`` here. One that carries personal data is held back
    in ``blocked``, by id and category, rather than failing the whole meeting: the
    other confirmed items can still go, and the screen asks for that one to be
    reworded. (Suggested in review of #247.)

    **Queries only the two lists this needs**, rather than going through
    ``review_for_meeting`` for its ``decisions`` and discarding the rest of
    what that builds -- confirmations, sources, refs, summaries for every
    decision and action item in the meeting, none of which this function
    reads (#296).
    """
    blocked: list[OutboundBlocked] = []

    decisions = []
    confirmed = session.execute(
        select(ExtDecision, ExtDecisionReview)
        .join(ExtDecisionReview, ExtDecisionReview.decision_id == ExtDecision.id)
        .where(ExtDecision.meeting_id == meeting_id, ExtDecisionReview.status == "confirmed")
        .order_by(ExtDecision.created_at, ExtDecision.id)
    )
    for decision, review in confirmed:
        statement = _confirmed_statement(decision, review)
        categories = find_unmasked(statement)
        if categories:
            blocked.append(OutboundBlocked(id=decision.id, kind="decision", categories=categories))
        else:
            decisions.append(OutboundDecision(id=decision.id, statement=statement))

    items = []
    for item in list_action_items(session, meeting_id=meeting_id):
        if item.status == ActionStatus.NEEDS_CONFIRMATION.value:
            continue
        categories = find_unmasked(item.description)
        if categories:
            blocked.append(OutboundBlocked(id=item.id, kind="action_item", categories=categories))
        else:
            items.append(item)

    return Outbound(meeting_id=meeting_id, decisions=decisions, action_items=items, blocked=blocked)


def create_decision(session: Session, payload: DecisionCreate) -> ReviewDecision:
    """Add a decision the model missed. Confirmed from the moment it exists.

    ``origin="user"`` keeps it through a rerun, and the review row says a person
    stands behind it. Every source must be an utterance of the same meeting: a
    quotation from another meeting would make this meeting claim a decision it
    never discussed. The refusal names the field, never the utterance text.
    """
    if session.get(Meeting, payload.meeting_id) is None:
        raise NotFoundError("meeting", payload.meeting_id)
    source_ids = list(dict.fromkeys(payload.source_utterance_ids))
    if source_ids:
        found = set(
            session.scalars(
                select(Utterance.id).where(
                    Utterance.id.in_(source_ids), Utterance.meeting_id == payload.meeting_id
                )
            )
        )
        if len(found) != len(source_ids):
            raise ValidationError(
                "every source utterance must belong to this meeting",
                field="source_utterance_ids",
            )

    decision = ExtDecision(
        meeting_id=payload.meeting_id,
        statement=payload.statement,
        confidence=1.0,
        origin="user",
        sources=[
            ExtDecisionSource(utterance_id=utterance_id, position=position)
            for position, utterance_id in enumerate(source_ids)
        ],
        source_digest=stored_digest(session, source_ids),
    )
    session.add(decision)
    session.flush()
    session.add(
        ExtDecisionReview(
            decision_id=decision.id, meeting_id=decision.meeting_id, status="confirmed"
        )
    )
    session.flush()
    return _read_decision(session, decision, refs=())


def delete_decision(session: Session, decision: ExtDecision) -> None:
    """Remove a decision from what the meeting will send.

    A decision a person added is really deleted, with its review -- privacy.md
    allows no soft deletes of content. A decision the model proposed cannot be:
    the next run would propose it again from the same utterances, and the person
    would be deleting it forever. It is rejected instead, which keeps it out of
    the outbound list across reruns, and its rewording, if any, is dropped.
    """
    if decision.origin == "user":
        session.execute(
            delete(ExtDecisionReview).where(ExtDecisionReview.decision_id == decision.id)
        )
        session.delete(decision)
    else:
        review = session.get(ExtDecisionReview, decision.id)
        if review is None:
            review = ExtDecisionReview(decision_id=decision.id, meeting_id=decision.meeting_id)
            session.add(review)
        review.status = "rejected"
        review.statement = None
    session.flush()


# --- step 7: sync to Notion -----------------------------------------------------

NOTION = "notion"

NOTION_PROPERTIES: Mapping[str, str] = {
    "title": "작업",
    "assignee": "담당자",
    "due": "마감일",
    "status": "상태",
    "confidence": "신뢰도",
    "meeting": "회의",
}
"""Which Notion property each field goes to, by the property's name.

These are the names in the team database the extraction owner set up. A team
whose database names them differently puts its own map under
``action_properties`` in its Notion integration config (screen S28), and **its map
replaces this one**: a map naming only ``title`` sends a title and nothing else.

Replacing rather than merging is what lets a team whose database has four columns
receive pages at all -- merged, every default name came along and Notion refused
the whole page for the properties that database does not have, so that team got
none. Raised in review of #294.
"""


NOTION_STATUS_LABELS: Mapping[str, str] = {
    ActionStatus.NEEDS_CONFIRMATION.value: "확인 필요",
    ActionStatus.TODO.value: "진행 전",
    ActionStatus.IN_PROGRESS.value: "진행 중",
    ActionStatus.DONE.value: "완료",
}
"""The 상태 option a page gets for each status: the board's column names
(``features/actions/types.ts``), so Notion and the board read the same. The
codes went out as they were until 2026-10-01 and were hard to tell apart in
Notion; a database made before then gains these options on first use, since
Notion adds a select option it has not seen."""


class NotionPages(Protocol):
    """The calls the sync makes. ``NotionClient`` and ``fakes.FakeNotion``
    both fit."""

    def create_page(self, database_id: str, properties: dict[str, Any]) -> str: ...
    def update_page(self, page_id: str, properties: dict[str, Any]) -> None: ...
    def trash_page(self, page_id: str) -> bool: ...
    def page_state(self, page_id: str) -> str: ...


PageOutcome = Literal["updated", "replaced", "archived", "retired", "gone"]
"""What became of a page a row already had. The first three are a sync of
something still confirmed (``_update_or_replace_page``). The last two, and
``"archived"`` again, are a retire (``_retire_decision_page``): taken out of
Notion by Autune, found already in a person's archive, or found already
deleted."""


def _update_or_replace_page(
    notion: NotionPages,
    ref: ExtExternalRef | ExtDecisionRef,
    *,
    database_id: str,
    update: dict[str, Any],
    create: dict[str, Any],
) -> PageOutcome:
    """Update the page ``ref`` points at, and say what became of it.

    Without this, a page removed in Notion refused every later update, the
    sync rolled back each time, and the item never reached Notion again
    (#403). When an update is refused, Notion is asked what the page is now:

    - ``"deleted"`` -- a new page in the team's database, and ``ref`` points
      at it (``"replaced"``). Notion gives the same 404 for a page that still
      exists but is no longer shared with the integration, so that case makes
      a second page while the first stays where it is. That is deliberate: a
      page we cannot write to is not one we can keep syncing into.
    - ``"archived"`` (or in the trash) -- left alone, and nothing raised
      (``"archived"``). A person put it there, often to tidy away finished
      work; making it again on the next edit, or for every archived page on a
      backfill, would undo that (PARKJAEKYUNG0525, review of #404).
    - ``"live"`` -- the refusal is raised as before: a new page for one that
      still exists would leave two.

    ``create`` is the page as a first send builds it, since a new page has no
    stale field to clear. If that create times out after Notion made the page,
    the page is orphaned and the next edit makes another -- the trade-off the
    first send already takes by not retrying (``tasks.sync_action_item``).
    The caller holds the ref row's lock throughout, so a refused update costs
    up to three Notion calls under it (PATCH, GET, POST) instead of one.
    """
    state = _update_page(notion, ref, update)
    if state != "deleted":
        return state
    page_id = notion.create_page(database_id, create)
    ref.external_id = page_id
    ref.url = notion_url(page_id)
    return "replaced"


def _update_page(
    notion: NotionPages, ref: ExtExternalRef | ExtDecisionRef, update: dict[str, Any]
) -> Literal["updated", "archived", "deleted"]:
    """Update the page ``ref`` points at, or say why Notion refused: the page
    is ``"archived"`` or ``"deleted"``. A refusal for a page that is still live
    is raised. The first half of ``_update_or_replace_page``, for a caller
    that does not make a deleted page again."""
    assert ref.external_id is not None
    try:
        notion.update_page(ref.external_id, update)
    except PermanentIntegrationError:
        state = notion.page_state(ref.external_id)
        if state == "archived":
            return "archived"
        if state != "deleted":
            raise
        return "deleted"
    return "updated"


def _follow_item_page(
    session: Session,
    notion: NotionPages,
    ref: ExtExternalRef,
    item: ExtActionItem,
    *,
    database_id: str,
    names: Mapping[str, str],
) -> PageOutcome | None:
    """Bring the page an item already has in step with the item, and say what
    became of it.

    A confirmed item whose page was deleted in Notion gets a new one (#403).
    **An item moved back to 확인 필요 does not** (#672): no page is made for a
    draft, and the team deleted that one. Its ref row goes instead, and
    ``None`` is returned -- with nothing left outside, the item is a draft
    like any other, and ``has_copy_outside`` stops counting it.
    """
    meeting = session.get(Meeting, item.meeting_id)
    title = meeting.title if meeting else None
    update = notion_properties(item, title, names, clear_missing=True)
    if item.status != ActionStatus.NEEDS_CONFIRMATION.value:
        return _update_or_replace_page(
            notion,
            ref,
            database_id=database_id,
            update=update,
            create=notion_properties(item, title, names),
        )
    state = _update_page(notion, ref, update)
    if state == "deleted":
        session.delete(ref)
        session.flush()
        return None
    return state


def has_copy_outside(session: Session, action_item_id: str) -> bool:
    """Whether the item has something outside that its confirmation made -- a
    Notion page, a Jira issue, an event on its assignee's calendar -- whatever
    its status is now.

    It counts B's own rows, not what Notion, Jira or Google hold. That stands
    for "was confirmed once" only because **a ref or an event row is never
    made for an item that has not been confirmed**: every sync returns before
    creating anything for a draft. A kind of ref made before confirmation
    would change what this function, and ``copies_follow``, mean (#672).

    A calendar event counts (#672): an item moved back to 확인 필요 with only
    an event was not one whose copies follow, so the event stayed on the
    calendar, and a deleted speech then took the draft and the event's row
    with it while the event kept the line in its title."""
    if (
        session.scalar(
            select(ExtExternalRef.action_item_id)
            .where(ExtExternalRef.action_item_id == action_item_id)
            .limit(1)
        )
        is not None
    ):
        return True
    return (
        session.scalar(
            select(ExtCalendarEvent.action_item_id).where(
                ExtCalendarEvent.action_item_id == action_item_id,
                ExtCalendarEvent.event_id.is_not(None),
            )
        )
        is not None
    )


def copies_follow(session: Session, item: ExtActionItem) -> bool:
    """Whether what this item says outside has to follow what it says here.

    A confirmed item, and one that was confirmed once: moved back to 확인 필요
    it keeps its Notion page and its Jira issue (decided with the user,
    2026-10-01), and its calendar event until the sync this queues takes it
    off, so it is a draft on the board and a record outside. Asking
    only for the status left such an item's page holding a line that had been
    deleted or corrected here (#657). One rule for the board's edits, a
    deleted speech and a corrected line."""
    return item.status != ActionStatus.NEEDS_CONFIRMATION.value or has_copy_outside(
        session, item.id
    )


def notion_url(page_id: str) -> str:
    """The page's address. Notion accepts the id without its dashes."""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def notion_properties(
    item: ExtActionItem,
    meeting_title: str | None,
    names: Mapping[str, str],
    *,
    clear_missing: bool = False,
) -> dict[str, Any]:
    """The page for one item: what an issue needs, and nothing from the transcript.

    Description, assignee, due date and status are the item itself; confidence
    tells the team which ones the model was unsure of; the meeting title says where
    it came from. Source utterances stay in Autune -- ``privacy.md`` and this
    module's CLAUDE.md both keep the transcript out of Notion, and the client's
    ``check_outbound`` refuses an unmasked value in any of these anyway.

    **``clear_missing`` is for an update.** A create leaves an empty field off
    the page (``test_a_field_the_item_does_not_have_is_left_off_the_page``). But
    Notion's PATCH overwrites only the properties it names, so an update that
    leaves the assignee or due date off keeps the *old* value on the page --
    clearing a due date is "a correction like any other" (``ActionItemUpdate``),
    and the board would say no date while Notion kept one. An update therefore
    names the emptied field with Notion's empty value (PARKJAEKYUNG0525's
    review of #342).
    """

    def text(value: str) -> dict[str, Any]:
        return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}

    fields: dict[str, Any] = {
        "title": {"title": [{"type": "text", "text": {"content": item.description[:2000]}}]},
        "status": {"select": {"name": NOTION_STATUS_LABELS.get(item.status, item.status)}},
        "confidence": {"number": round(item.confidence, 3)},
    }
    assignee = item.assignee_label
    if assignee:
        fields["assignee"] = text(assignee)
    elif clear_missing:
        fields["assignee"] = {"rich_text": []}
    if item.due_date is not None:
        fields["due"] = {"date": {"start": item.due_date.isoformat()}}
    elif clear_missing:
        fields["due"] = {"date": None}
    if meeting_title:
        fields["meeting"] = text(meeting_title)
    return {names[key]: value for key, value in fields.items() if key in names}


def sync_action_item_to_notion(
    session: Session,
    notion: NotionPages,
    *,
    action_item_id: str,
    database_id: str,
    property_names: Mapping[str, str] | None = None,
    on_page: Callable[[PageOutcome], None] | None = None,
) -> ExtExternalRef | None:
    """Create the item's Notion page the first time; update the same page every
    time after. ``None`` when there is nothing to send.

    Nothing is sent for an item that is gone, and no page is made for one
    still waiting for confirmation. An item moved back to 확인 필요 after its
    page was made updates that page, so Notion shows the status the board does
    -- the page stays, only its status changes (decided with the user,
    2026-10-01). The first send is decided by the database: the claim is an
    insert that skips an existing row, so a confirmation delivered twice, or
    two workers holding it at once, create one page -- the second blocks on
    the first's row and then finds it. Claim and create share the caller's
    transaction, so a failed call takes the claim back and a later run can
    try creating it again.

    **A later edit finds the claim already there and updates the page
    instead of creating a second one.** A PATCH is naturally idempotent for a
    *redelivery of the same edit* -- two workers racing the same update both
    converge on the same final properties -- but not for two genuinely
    different edits in flight at once: found in review of #342 (lsh2217).
    Every edit past confirmation now queues its own sync, each reading
    current state independently, so a slow network round-trip can let an
    earlier edit's page write land *after* a later edit's already has,
    leaving Notion silently stale. ``with_for_update`` on the claim row
    orders when each sync is let past it -- but ordering the sends is not
    enough on its own if each sync already fixed its properties from an
    *earlier* read (lsh2217's second-round review of #342): whichever sync
    acquires the lock last would still send whatever it read first, exactly
    backwards from the edit order the lock exists to enforce. Every read of
    the item below happens only after its ref row's lock is held, with
    ``populate_existing=True`` so a session that already looked at this item
    for an unrelated reason cannot serve a cached copy here -- the one
    sending last is always the one sending latest. The description,
    assignee, due date and status a person edited on the board are exactly
    what this sends; the source utterances never leave Autune either way.

    **A page someone removed in Notion** (#403): deleted, it is made again;
    archived, it is left alone -- ``_update_or_replace_page``. ``on_page``
    hears which, for a caller that counts (``notion_backfill``). For an item
    moved back to 확인 필요 a deleted page is not made again: its ref row
    goes and ``None`` is returned (``_follow_item_page``, #672).
    """
    names = property_names or NOTION_PROPERTIES

    existing = session.get(ExtExternalRef, (action_item_id, NOTION), with_for_update=True)
    if existing is not None:
        # Claim and create share one transaction (below), so a row that made
        # it to the database has its page id -- there is no committed row
        # from a claim whose create never ran.
        item = session.get(ExtActionItem, action_item_id, populate_existing=True)
        if item is None:
            return existing
        outcome = _follow_item_page(
            session, notion, existing, item, database_id=database_id, names=names
        )
        log.info(
            "extraction_notion_updated",
            action_item_id=item.id,
            meeting_id=item.meeting_id,
            page=outcome or "gone",
        )
        if outcome is None:
            return None
        if on_page is not None:
            on_page(outcome)
        return existing

    item = session.get(ExtActionItem, action_item_id)
    if item is None or item.status == ActionStatus.NEEDS_CONFIRMATION.value:
        return None

    claimed = session.scalars(
        _insert_if_absent_into(session, ExtExternalRef)
        .values(action_item_id=item.id, system=NOTION, meeting_id=item.meeting_id)
        .on_conflict_do_nothing(index_elements=["action_item_id", "system"])
        .returning(ExtExternalRef.action_item_id)
    ).one_or_none()
    if claimed is None:
        # Another transaction's claim landed between our lock-miss above and
        # this insert -- with_for_update only locks a row that exists, so a
        # claim still mid-flight was invisible to that first read. Re-acquire
        # the lock on its now-existing row and update instead of dropping
        # this edit (lsh2217's second-round review of #342, from
        # @mminjae97's finding).
        existing = session.get(ExtExternalRef, (item.id, NOTION), with_for_update=True)
        assert existing is not None
        item = session.get(ExtActionItem, action_item_id, populate_existing=True)
        if item is None:
            return existing
        outcome = _follow_item_page(
            session, notion, existing, item, database_id=database_id, names=names
        )
        log.info(
            "extraction_notion_updated_after_claim_race",
            action_item_id=item.id,
            page=outcome or "gone",
        )
        if outcome is None:
            return None
        if on_page is not None:
            on_page(outcome)
        return existing

    meeting = session.get(Meeting, item.meeting_id)
    properties = notion_properties(item, meeting.title if meeting else None, names)
    page_id = notion.create_page(database_id, properties)

    ref = session.get(ExtExternalRef, (item.id, NOTION))
    assert ref is not None
    ref.external_id = page_id
    ref.url = notion_url(page_id)
    log.info("extraction_notion_synced", action_item_id=item.id, meeting_id=item.meeting_id)
    return ref


DECISION_NOTION_PROPERTIES: Mapping[str, str] = {
    "title": "결정",
    "confidence": "신뢰도",
    "sources": "근거 발화 수",
    "meeting": "회의",
}
"""The decision database's property names. A team's ``decision_properties`` map
replaces this one, the rule ``NOTION_PROPERTIES`` explains for items."""


DECISION_PUT_BACK_TEXT = "확정이 취소된 결정"
"""What a decision's page is retitled to before it goes to Notion's trash
(#669). The trash keeps a page restorable for 30 days; with this title the
statement is not what it keeps."""


def decisions_with_a_page(session: Session, decision_ids: Collection[str]) -> set[str]:
    """The decisions among ``decision_ids`` whose Notion page is still there --
    made when they were confirmed, and not retired since."""
    if not decision_ids:
        return set()
    return set(
        session.scalars(
            select(ExtDecisionRef.decision_id).where(
                ExtDecisionRef.decision_id.in_(decision_ids),
                ExtDecisionRef.system == NOTION,
                ExtDecisionRef.external_id.is_not(None),
            )
        )
    )


def decision_pages_without_a_decision(session: Session, meeting_id: str) -> list[str]:
    """Ids of this meeting's decisions that are gone and still have a Notion
    page: a rerun dropped the decision (``build_decisions``), or a person
    deleted their own. For the caller to queue ``sync_decision``, which
    retires the page (#669). Ids only."""
    return list(
        session.scalars(
            select(ExtDecisionRef.decision_id).where(
                ExtDecisionRef.meeting_id == meeting_id,
                ExtDecisionRef.system == NOTION,
                ExtDecisionRef.external_id.is_not(None),
                ~select(ExtDecision.id)
                .where(ExtDecision.id == ExtDecisionRef.decision_id)
                .exists(),
            )
        )
    )


def decision_has_page(session: Session, decision_id: str) -> bool:
    """Whether the decision still has the page its confirmation made. A change
    of verdict on such a decision has to reach Notion even when the new
    verdict is not *confirmed* (#669)."""
    return bool(decisions_with_a_page(session, [decision_id]))


def _retire_decision_page(
    notion: NotionPages, ref: ExtDecisionRef, names: Mapping[str, str]
) -> Literal["retired", "archived", "gone"] | None:
    """Take a decision's page out of the team's Notion: the decision is no
    longer confirmed, or is gone (#669, decided with the user 2026-10-02).

    Nothing about a decision leaves before a person confirms it (#246), and
    the decision database has no status column to say a page was put back --
    left in place it would go on reading as a confirmed decision. So the page
    is retitled to ``DECISION_PUT_BACK_TEXT`` first, which keeps the statement
    out of the trash, and then moved there. The ref row stays and forgets its
    page: confirming again makes a new one.

    Both calls share the caller's transaction, so a failure of either leaves
    the row pointing at the page and the next sync tries again -- the
    decision's next change, or ``tasks.retire_decision_pages`` on its timer
    (#683). Retitling
    does not reach Notion's own page history, which a paid workspace keeps:
    someone who restores the page from the trash can still read the earlier
    title there (``privacy.md`` section 6). A page a
    person already archived cannot be edited -- Notion refuses -- and is
    left as they put it: it is in Notion's trash already, which is where a
    retire ends, only with its statement still in the title. The row forgets
    it, as it forgets a page already deleted (#683): kept, the id made every
    sweep ask Notion about a page nothing more can be done to.

    Returns what happened, so a caller that counts does not call a page it
    only found archived or deleted "retired": ``None`` when the row had no
    page to begin with.
    """
    page_id = ref.external_id
    if page_id is None:
        return None
    if "title" in names:
        retitled = {
            names["title"]: {
                "title": [{"type": "text", "text": {"content": DECISION_PUT_BACK_TEXT}}]
            }
        }
        try:
            notion.update_page(page_id, retitled)
        except PermanentIntegrationError:
            state = notion.page_state(page_id)
            if state == "live":
                raise
            ref.external_id = None
            ref.url = None
            log.info(
                "extraction_notion_decision_page_left_archived"
                if state == "archived"
                else "extraction_notion_decision_page_gone",
                decision_id=ref.decision_id,
            )
            return "archived" if state == "archived" else "gone"
    else:
        # A team's own property map with no title: nothing here knows which
        # property holds the statement, so the page goes to the trash as it
        # is. Better than leaving it live; said loudly (PARK, review of #679).
        log.warning(
            "extraction_notion_decision_trashed_without_retitle", decision_id=ref.decision_id
        )
    notion.trash_page(page_id)
    ref.external_id = None
    ref.url = None
    log.info("extraction_notion_decision_retired", decision_id=ref.decision_id)
    return "retired"


def _send_decision_page(
    notion: NotionPages,
    ref: ExtDecisionRef,
    *,
    database_id: str,
    properties: dict[str, Any],
) -> PageOutcome:
    """The confirmed decision's page, for a ref that already exists: updated
    in place, or made new when the ref has no page -- the decision was put
    back, its page retired, and it is confirmed again (#669). A new page
    reports ``"replaced"``, as one made for a page deleted in Notion does."""
    if ref.external_id is None:
        page_id = notion.create_page(database_id, properties)
        ref.external_id = page_id
        ref.url = notion_url(page_id)
        return "replaced"
    return _update_or_replace_page(
        notion, ref, database_id=database_id, update=properties, create=properties
    )


def decision_notion_properties(
    statement: str,
    decision: ExtDecision,
    meeting_title: str | None,
    names: Mapping[str, str],
) -> dict[str, Any]:
    """The page for one decision: the statement as confirmed, and nothing quoted.

    ``statement`` is the person's rewording when there is one -- what they
    confirmed -- and the model's sentence otherwise. The source utterances stay in
    Autune; the page carries only how many there were.
    """
    fields: dict[str, Any] = {
        "title": {"title": [{"type": "text", "text": {"content": statement[:2000]}}]},
        "confidence": {"number": round(decision.confidence, 3)},
        "sources": {"number": len(decision.sources)},
    }
    if meeting_title:
        fields["meeting"] = {
            "rich_text": [{"type": "text", "text": {"content": meeting_title[:2000]}}]
        }
    return {names[key]: value for key, value in fields.items() if key in names}


def sync_decision_to_notion(
    session: Session,
    notion: NotionPages,
    *,
    decision_id: str,
    database_id: str,
    property_names: Mapping[str, str] | None = None,
    on_page: Callable[[PageOutcome], None] | None = None,
) -> ExtDecisionRef | None:
    """Create a confirmed decision's Notion page the first time; update the
    same page every time after. ``None`` when nothing is sent.

    Nothing goes for a decision that is gone or is not confirmed (#246) -- and
    a page such a decision still has from an earlier confirmation is taken
    out of Notion (``_retire_decision_page``, #669). The
    create-then-update shape, the lock-then-reread ordering, and the
    claim-race fallback are all ``sync_action_item_to_notion``'s -- a
    reworded confirmed decision (#246 allows rewording after confirmation)
    updates the page it already has rather than being silently skipped, and
    a later rewording is never overtaken by an earlier one that acquires the
    ref lock second (lsh2217's second-round review of #342 -- this function
    was missing the lock entirely, not just the reread ordering).
    """
    names = property_names or DECISION_NOTION_PROPERTIES

    existing = session.get(ExtDecisionRef, (decision_id, NOTION), with_for_update=True)
    if existing is not None:
        decision = session.get(ExtDecision, decision_id, populate_existing=True)
        review = session.get(ExtDecisionReview, decision_id, populate_existing=True)
        if decision is None or review is None or review.status != "confirmed":
            retired = _retire_decision_page(notion, existing, names)
            if retired is not None and on_page is not None:
                on_page(retired)
            return existing
        meeting = session.get(Meeting, decision.meeting_id)
        statement = _confirmed_statement(decision, review)
        properties = decision_notion_properties(
            statement, decision, meeting.title if meeting else None, names
        )
        outcome = _send_decision_page(
            notion, existing, database_id=database_id, properties=properties
        )
        log.info(
            "extraction_notion_decision_updated",
            decision_id=decision.id,
            meeting_id=decision.meeting_id,
            page=outcome,
        )
        if on_page is not None:
            on_page(outcome)
        return existing

    decision = session.get(ExtDecision, decision_id)
    review = session.get(ExtDecisionReview, decision_id)
    if decision is None or review is None or review.status != "confirmed":
        return None

    claimed = session.scalars(
        _insert_if_absent_into(session, ExtDecisionRef)
        .values(decision_id=decision.id, system=NOTION, meeting_id=decision.meeting_id)
        .on_conflict_do_nothing(index_elements=["decision_id", "system"])
        .returning(ExtDecisionRef.decision_id)
    ).one_or_none()
    if claimed is None:
        # Same claim-race as the action-item sync: re-acquire the lock on the
        # row that beat us here and update instead of dropping this edit.
        existing = session.get(ExtDecisionRef, (decision.id, NOTION), with_for_update=True)
        assert existing is not None
        decision = session.get(ExtDecision, decision_id, populate_existing=True)
        review = session.get(ExtDecisionReview, decision_id, populate_existing=True)
        if decision is None or review is None or review.status != "confirmed":
            retired = _retire_decision_page(notion, existing, names)
            if retired is not None and on_page is not None:
                on_page(retired)
            return existing
        meeting = session.get(Meeting, decision.meeting_id)
        statement = _confirmed_statement(decision, review)
        properties = decision_notion_properties(
            statement, decision, meeting.title if meeting else None, names
        )
        outcome = _send_decision_page(
            notion, existing, database_id=database_id, properties=properties
        )
        log.info(
            "extraction_notion_decision_updated_after_claim_race",
            decision_id=decision.id,
            page=outcome,
        )
        if on_page is not None:
            on_page(outcome)
        return existing

    meeting = session.get(Meeting, decision.meeting_id)
    statement = _confirmed_statement(decision, review)
    properties = decision_notion_properties(
        statement, decision, meeting.title if meeting else None, names
    )
    page_id = notion.create_page(database_id, properties)

    ref = session.get(ExtDecisionRef, (decision.id, NOTION))
    assert ref is not None
    ref.external_id = page_id
    ref.url = notion_url(page_id)
    log.info(
        "extraction_notion_decision_synced", decision_id=decision.id, meeting_id=decision.meeting_id
    )
    return ref


def _insert_if_absent_into(session: Session, model: type[Any]) -> postgresql.Insert | sqlite.Insert:
    """``INSERT ... ON CONFLICT DO NOTHING`` in the session's own dialect.

    The same two-dialect choice ``_insert_if_absent`` makes for confirmations,
    for any table: Postgres in the app, SQLite in the unit tests.
    """
    if session.get_bind().dialect.name == "postgresql":
        return postgresql.insert(model)
    return sqlite.insert(model)


# --- the team's open Jira issues, for D's brief (#436) ---------------------------

AGENDA_LIMIT = 20
"""Issues per snapshot. D shows six; the rest is headroom, not a promise."""

_AGENDA_STATUSES = {
    ActionStatus.TODO.value: "할 일",
    ActionStatus.IN_PROGRESS.value: "진행 중",
}
"""The item's status as the brief shows it. B moves the issue to the matching
Jira status category on every edit, so this is Jira's state as far as Autune
set it; a status changed in Jira alone is not read back."""

_JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
_JIRA_URL = re.compile(JIRA_ISSUE_URL)


def _one_line(text: str, limit: int) -> str:
    """Whitespace collapsed, and cut to ``limit`` characters with an ellipsis."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _counted_for_progress(now: datetime) -> ColumnElement[bool]:
    """A meeting ``TeamActionProgress`` may count: made inside
    ``ACTION_PROGRESS_WINDOW`` of ``now`` and not past its retention window.

    The window is 91 days and a team's retention can be shorter, and a meeting
    past ``expires_at`` stays in the table until A's sweep takes it -- longer
    when one of its deletion hooks fails. Counting it would send its id to E
    and keep it in the team's completion rate after it should be gone
    (invariant 11; #649 review). The retention half is ``within_retention``,
    the one every read of B's asks.
    """
    return and_(Meeting.created_at >= now - ACTION_PROGRESS_WINDOW, within_retention(now))


def teams_with_recent_meetings(session: Session, *, now: datetime) -> list[str]:
    """Teams with a meeting ``_counted_for_progress`` -- the teams whose action
    progress is published (#605), even when nothing in the window is confirmed:
    a fresh empty snapshot says so. A team whose only meetings in the window
    are past retention is not one of them."""
    return sorted(
        session.scalars(select(Meeting.team_id).where(_counted_for_progress(now)).distinct())
    )


def team_action_progress(session: Session, team_id: str, *, now: datetime) -> TeamActionProgress:
    """The team's action items as counts per meeting, as of ``now`` (#605).

    Per meeting made inside the window and still inside its retention
    (``_counted_for_progress``): items past ``needs_confirmation``, those
    ``done``, and those confirmed, not done and due before the team's today --
    the board's overdue rule (``tools._overdue``). **Today is the date of
    ``now`` in Korea** (``slots.KST``): there is no team time zone, and a
    server's ``date.today()`` on UTC is a day behind from 00:00 to 09:00 KST,
    when the board and E's dashboard would disagree on what is late (#619
    review). A meeting with nothing confirmed is left out. Counts and meeting
    ids only: no assignee, title or item id leaves here, so no per-person
    completion record can be built from it (privacy.md section 3; the
    contract's own note).
    """
    today = now.astimezone(KST).date()
    confirmed = ExtActionItem.status != ActionStatus.NEEDS_CONFIRMATION.value
    done = ExtActionItem.status == ActionStatus.DONE.value
    overdue = and_(
        confirmed,
        ~done,
        ExtActionItem.due_date.is_not(None),
        ExtActionItem.due_date < today,
    )
    rows = session.execute(
        select(
            ExtActionItem.meeting_id,
            func.count().filter(confirmed),
            func.count().filter(done),
            func.count().filter(overdue),
        )
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(Meeting.team_id == team_id, _counted_for_progress(now))
        .group_by(ExtActionItem.meeting_id)
        .order_by(ExtActionItem.meeting_id)
    ).all()
    return TeamActionProgress(
        team_id=team_id,
        as_of=now,
        meetings=[
            MeetingActionProgress(
                meeting_id=meeting_id, confirmed=n_confirmed, done=n_done, overdue=n_overdue
            )
            for meeting_id, n_confirmed, n_done, n_overdue in rows
            if n_confirmed > 0
        ],
    )


def teams_with_jira_issues(session: Session) -> list[str]:
    """Teams with an item that became a Jira issue and still exists -- the teams
    whose agenda can be non-empty, or whose issues all closed and must now be
    published empty. A team whose last such item was *deleted* drops out (its
    Jira link goes with it); its last snapshot then goes stale for D under
    ``AGENDA_STALE_AFTER`` (#491 review)."""
    return sorted(
        session.scalars(
            select(Meeting.team_id)
            .join(ExtExternalRef, ExtExternalRef.meeting_id == Meeting.id)
            .where(ExtExternalRef.system == "jira", ExtExternalRef.external_id.is_not(None))
            .distinct()
        )
    )


def team_agenda(session: Session, team_id: str, *, now: datetime) -> TeamAgenda:
    """The team's open issues made from its action items, most pressing first:
    the soonest due date, then undated ones, oldest first (#436).

    Only what Autune made -- an item confirmed and sent to Jira -- so there is
    no call to Jira here and nothing a person wrote in Jira alone leaves it. The
    title is the item's description, which is stored masked. A key or a link
    that does not have the shape the contract requires is left out rather than
    failing the whole snapshot; the title still goes.
    """
    rows = session.execute(
        select(
            ExtActionItem.description,
            ExtActionItem.status,
            ExtExternalRef.external_id,
            ExtExternalRef.url,
        )
        .join(
            ExtExternalRef,
            (ExtExternalRef.action_item_id == ExtActionItem.id) & (ExtExternalRef.system == "jira"),
        )
        .join(Meeting, Meeting.id == ExtActionItem.meeting_id)
        .where(
            Meeting.team_id == team_id,
            within_retention(now),
            ExtExternalRef.external_id.is_not(None),
            ExtActionItem.status.in_(_AGENDA_STATUSES),
        )
        .order_by(
            ExtActionItem.due_date.is_(None),
            ExtActionItem.due_date,
            ExtActionItem.created_at,
            ExtActionItem.id,
        )
        .limit(AGENDA_LIMIT)
    )
    issues = []
    for description, status, key, url in rows:
        title = _one_line(description, AGENDA_TITLE_MAX)
        if not title:
            continue
        issues.append(
            AgendaIssue(
                title=title,
                key=key if key and _JIRA_KEY.match(key) else None,
                status=_AGENDA_STATUSES[status],
                url=url if url and _JIRA_URL.match(url) else None,
            )
        )
    return TeamAgenda(team_id=team_id, as_of=now, issues=issues)
