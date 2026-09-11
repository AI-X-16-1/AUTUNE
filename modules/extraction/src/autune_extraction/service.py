"""Business logic for module B: Structured Extraction.

Owner: 강민구. See docs/modules/extraction.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ext_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from autune_contracts.enums import ActionStatus, UtteranceKind
from autune_contracts.extraction import (
    ActionItem,
    AmbiguousAgreement,
    Classification,
    Decision,
    ExtractionResult,
)
from autune_contracts.transcript import Utterance as TranscriptUtterance
from autune_core import Meeting, Participant, Utterance, get_logger, session_scope
from autune_integrations import SlackApi, assert_personal_delivery

from .config import get_settings
from .confirmations import WEAK_ASSENT, ConfirmationResponse, build_confirmation_dm
from .decisions import DEFAULT_MAX_GAP, ClassifiedUtterance, group_decisions
from .edit_cost import EditCost
from .models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionSource,
    ExtEditEvent,
)
from .pipeline.base import Classifier
from .schemas import (
    ActionItemCreate,
    ActionItemDetail,
    ActionItemRead,
    ActionItemUpdate,
    SourceUtterance,
)
from .slots import assignee_of, meeting_day, parse_due

log = get_logger(__name__)


def send_confirmation_dm(
    slack: SlackApi,
    *,
    speaker_id: str,
    recipient_id: str,
    utterance_id: str,
    quoted_text: str,
) -> str:
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

    text, blocks = build_confirmation_dm(utterance_id=utterance_id, quoted_text=quoted_text)
    timestamp = slack.send_dm(recipient_id, text, blocks)

    # Ids only. The utterance is meeting content and a log line is a store.
    log.info("extraction_confirmation_sent", utterance_id=utterance_id)
    return timestamp


def ask_for_confirmation(
    session: Session,
    slack: SlackApi,
    *,
    meeting_id: str,
    speaker_id: str,
    recipient_id: str,
    utterance_id: str,
    quoted_text: str,
    reason: str = WEAK_ASSENT,
) -> ExtConfirmation:
    """Open a confirmation and send its DM, in that order.

    The row goes in first. Delivery is at-least-once and Slack can fail after
    accepting the call, so a send that is not preceded by a row can leave a
    question asked with no deadline running — the state where nothing ever
    resolves it. The reverse, a row whose DM failed, is visible and retryable.

    ``send_confirmation_dm`` stays callable on its own for the privacy guards it
    carries; this is the entry point that also starts the clock.
    """
    row = open_confirmation(
        session, meeting_id=meeting_id, utterance_id=utterance_id, reason=reason
    )
    send_confirmation_dm(
        slack,
        speaker_id=speaker_id,
        recipient_id=recipient_id,
        utterance_id=utterance_id,
        quoted_text=quoted_text,
    )
    return row


def open_confirmation(
    session: Session, *, meeting_id: str, utterance_id: str, reason: str = WEAK_ASSENT
) -> ExtConfirmation:
    """Start the clock, once.

    A re-send keeps the original ``sent_at``. The deadline measures how long the
    speaker has had the question in front of them, and a Celery retry means they
    had it the whole time — restarting it would give the model another day to
    look undecided for free.

    Deliberately not a place to reset an answer: someone who has already replied
    keeps their reply if the DM is sent again.
    """
    row = session.get(ExtConfirmation, utterance_id)
    if row is not None:
        return row

    row = ExtConfirmation(
        utterance_id=utterance_id,
        meeting_id=meeting_id,
        reason=reason,
        sent_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def apply_confirmation_response(response: ConfirmationResponse) -> None:
    """Record what the speaker answered. The seam the Slack handler delegates to.

    Opens its own session because the handler has none — a Slack action arrives
    outside any request or task that owns one.
    """
    with session_scope() as session:
        resolve_confirmation(session, response)


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
    """
    row = session.get(ExtConfirmation, response.utterance_id)
    if row is None:
        log.info("extraction_confirmation_orphaned", utterance_id=response.utterance_id)
        return None

    row.resolved_kind = response.resolved_kind.value
    row.responded_at = datetime.now(UTC)

    # Ids and a label. The utterance itself is meeting content.
    log.info(
        "extraction_confirmation_received",
        utterance_id=response.utterance_id,
        resolved_kind=response.resolved_kind.value,
    )
    # TODO(강민구): #10 — reclassify the utterance in ext_classifications, which
    # does not exist until the classifier lands. #11 — build an action item from
    # a confirmed commitment; the description and due date come from slot
    # filling, and inventing them here would put a guess where a parse belongs.
    return row


def ambiguous_agreements_for_meeting(
    session: Session, meeting_id: str, *, now: datetime | None = None
) -> list[AmbiguousAgreement]:
    """This meeting's ambiguous agreements as the contract E reads.

    Every stored row is one a DM went out for, so ``confirmation_sent`` is true
    throughout. The field stays in the contract because an ambiguity found with
    no DM sent — the workspace app missing, the speaker unmapped — is a state
    that has to be expressible even though this query cannot produce it.

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


def create_action_item(session: Session, payload: ActionItemCreate) -> ExtActionItem:
    """Add an item the model missed.

    ``confidence`` is 1.0 and ``origin`` is ``user``: a person typing an item is
    the certainty, and the origin is what edit cost is measured against.

    Counted as an edit. An item the model missed costs the user more than one it
    got wrong -- they have to notice the absence, which is the failure recall
    makes likely and the one editing cannot fix by itself.
    """
    item = ExtActionItem(
        meeting_id=payload.meeting_id,
        description=payload.description,
        assignee_id=payload.assignee_id,
        assignee_label=payload.assignee_label,
        due_date=payload.due_date,
        status=ActionStatus.NEEDS_CONFIRMATION.value,
        confidence=1.0,
        origin="user",
    )
    item.sources = [
        ExtActionItemSource(utterance_id=utterance_id)
        for utterance_id in dict.fromkeys(payload.source_utterance_ids)
    ]
    session.add(item)
    session.flush()

    _record_edit(session, meeting_id=item.meeting_id, action_item_id=item.id, kind="created")
    return item


def read_model(item: ExtActionItem) -> ActionItemRead:
    """One item as this module's own screens read it.

    Built here rather than by ``from_attributes`` on the schema because two of
    its fields are not columns: the source ids live in the link table, and
    whether the item is a candidate depends on a setting the row knows nothing
    about.

    Deciding *candidate* on the server is the point of this function. The
    threshold belongs to the classifier that produced the confidence, and a
    browser comparing against a number it happens to hold is one deploy away from
    disagreeing with the server about what the meeting produced. While
    ``candidate_confidence`` is unset -- its default until #10 measures one --
    nothing is a candidate, because there is no honest line to draw yet.
    """
    threshold = get_settings().candidate_confidence
    return ActionItemRead(
        id=item.id,
        meeting_id=item.meeting_id,
        description=item.description,
        assignee_id=item.assignee_id,
        assignee_label=item.assignee_label,
        due_date=item.due_date,
        status=item.status,
        confidence=item.confidence,
        origin=item.origin,
        source_utterance_ids=[source.utterance_id for source in item.sources],
        is_candidate=threshold is not None and item.confidence < threshold,
    )


def list_action_items(
    session: Session,
    *,
    meeting_id: str | None = None,
    assignee_id: str | None = None,
    status: ActionStatus | None = None,
    due_before: date | None = None,
) -> list[ActionItemRead]:
    """The items S17 and S05 put on screen. Every filter is optional and they AND.

    ``due_before`` is strict: an item due on that day is not before it. That
    makes "overdue" one argument -- today's date -- instead of yesterday's, and
    an item with no due date is never before anything, so it drops out of any
    date filter rather than reading as overdue.

    Sources are loaded in the same round trip. The card counts them, so a lazy
    load would be one more query per card.
    """
    query = (
        select(ExtActionItem)
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

    return [read_model(item) for item in session.scalars(query)]


def read_detail(session: Session, item: ExtActionItem) -> ActionItemDetail:
    """One item with the text of the utterances it was drawn from.

    The only route in this module that returns transcript text. It is here and
    not on the list because the drawer is the one screen that shows a quotation,
    and it shows one item's at a time.
    """
    return ActionItemDetail(
        **read_model(item).model_dump(), sources=source_utterances(session, item.id)
    )


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
    """
    changes = payload.changes()
    if not changes:
        return item

    for field, value in changes.items():
        setattr(item, field, value.value if isinstance(value, ActionStatus) else value)
    if "due_date" in changes:
        # The phrase explained the date the model read. A date a person set is
        # not explained by it, and keeping it would hold on to what they
        # corrected (#109).
        item.due_text = None

    _record_edit(session, meeting_id=item.meeting_id, action_item_id=item.id, kind="edited")
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
    session: Session, *, meeting_id: str, action_item_id: str | None, kind: str
) -> None:
    """One correction, counted and not attributed.

    No user id is passed in because none is stored. ADR 0003 forbids per-person
    metrics, and "who corrected the model most" is the same shape of data as a
    speaking ratio.
    """
    session.add(ExtEditEvent(meeting_id=meeting_id, action_item_id=action_item_id, kind=kind))


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
) -> list[ExtDecision]:
    """Rebuild this meeting's decisions from its classified utterances.

    ``utterances`` is every utterance of the meeting in ``start_sec`` order; see
    ``group_decisions`` for why the non-decision ones have to be there.

    **Rebuilding replaces.** The meeting's existing decisions are deleted and the
    new ones get fresh ``dec_`` ids, so a caller that rebuilds must republish
    ``ExtractionResult`` — D's lineage points at ids that no longer exist
    otherwise. That is why this is a rebuild rather than a merge: matching an old
    decision to a new one is the same-decision question, and #25 gave that to D.

    The delete is a real delete. These rows are derived from utterances that are
    still there, so nothing is lost that cannot be recomputed, and privacy.md
    leaves no room for a soft one.
    """
    session.execute(delete(ExtDecision).where(ExtDecision.meeting_id == meeting_id))

    decisions = [
        ExtDecision(
            meeting_id=meeting_id,
            statement=group.statement,
            confidence=group.confidence,
            sources=[
                ExtDecisionSource(utterance_id=utterance_id, position=position)
                for position, utterance_id in enumerate(group.source_utterance_ids)
            ],
        )
        for group in group_decisions(utterances, max_gap=max_gap)
    ]
    session.add_all(decisions)
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
    """
    rows = session.scalars(
        select(ExtDecision)
        .where(ExtDecision.meeting_id == meeting_id)
        .order_by(ExtDecision.created_at, ExtDecision.id)
    ).all()

    return [
        Decision(
            id=row.id,
            statement=row.statement,
            source_utterance_ids=[
                source.utterance_id for source in sorted(row.sources, key=lambda s: s.position)
            ],
            confidence=row.confidence,
        )
        for row in rows
    ]


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
    the Notion and Jira sync (#30), and nothing has been synced before it.
    """
    return ActionItem(
        id=item.id,
        description=item.description,
        assignee_id=item.assignee_id,
        assignee_label=item.assignee_label,
        due_date=item.due_date,
        source_utterance_ids=[source.utterance_id for source in item.sources],
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
        )
        if (prediction := answer.get(utterance.id)) is not None
        else ClassifiedUtterance(id=utterance.id, kind=None, confidence=0.0, text="")
        for utterance in ordered
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
            nli_verified=False,
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


def build_action_items(
    session: Session,
    *,
    meeting_id: str,
    utterances: Sequence[TranscriptUtterance],
    classified: Sequence[ClassifiedUtterance],
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

    Each item is filled by ``slots``: the utterance as its description, its
    speaker as the assignee, the first date phrase as the due date. Every model
    item starts in *needs confirmation*.
    """
    edited = session.scalar(
        select(func.count()).select_from(ExtEditEvent).where(ExtEditEvent.meeting_id == meeting_id)
    )
    if edited:
        log.info("extraction_action_items_kept", meeting_id=meeting_id, edits=edited)
        return None

    meeting = session.get(Meeting, meeting_id)
    day = meeting_day(meeting.started_at if meeting is not None else None)
    spoken = {utterance.id: utterance for utterance in utterances}

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
        said = spoken[utterance.id]
        assignee = assignee_of(said.speaker_id, said.speaker)
        due = parse_due(said.text, day)
        items.append(
            ExtActionItem(
                meeting_id=meeting_id,
                description=said.text,
                assignee_id=assignee.user_id,
                assignee_label=assignee.label,
                due_date=due.date if due is not None else None,
                due_text=due.text if due is not None else None,
                status=ActionStatus.NEEDS_CONFIRMATION.value,
                confidence=utterance.confidence,
                origin="model",
                sources=[ExtActionItemSource(utterance_id=utterance.id)],
            )
        )
    session.add_all(items)
    session.flush()
    return items
