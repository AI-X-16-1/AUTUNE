"""Business logic for module B: Structured Extraction.

Owner: 강민구. See docs/modules/extraction.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ext_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from autune_contracts.enums import ActionStatus
from autune_contracts.extraction import AmbiguousAgreement, Decision
from autune_core import get_logger, session_scope
from autune_integrations import SlackApi, assert_personal_delivery

from .confirmations import WEAK_ASSENT, ConfirmationResponse, build_confirmation_dm
from .decisions import DEFAULT_MAX_GAP, ClassifiedUtterance, group_decisions
from .edit_cost import EditCost
from .models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionSource,
    ExtEditEvent,
)
from .schemas import ActionItemCreate, ActionItemUpdate

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
