"""Business logic for module B: Structured Extraction.

Owner: 강민구. See docs/modules/extraction.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ext_*`` tables.
Never imports another module.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, selectinload

from autune_contracts.enums import ActionStatus, UtteranceKind
from autune_contracts.extraction import (
    AGENDA_TITLE_MAX,
    JIRA_ISSUE_URL,
    ActionItem,
    AgendaIssue,
    AmbiguousAgreement,
    Classification,
    Decision,
    ExtractionResult,
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
    session_scope,
)
from autune_core.errors import NotFoundError, ValidationError
from autune_integrations import PermanentIntegrationError, SlackApi, assert_personal_delivery
from autune_integrations.privacy import find_unmasked

from .config import get_settings
from .confirmations import WEAK_ASSENT, ConfirmationResponse, build_confirmation_dm
from .decisions import DEFAULT_MAX_GAP, ClassifiedUtterance, decision_id, group_decisions
from .edit_cost import EditCost
from .models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
)
from .pipeline.base import Classifier, NliModel, ReferenceResolver, ResolutionRequest
from .pipeline.resolver import MAX_CONTEXT_AFTER, MAX_CONTEXT_UTTERANCES
from .schemas import (
    ActionItemCreate,
    ActionItemDetail,
    ActionItemRead,
    ActionItemUpdate,
    DecisionCreate,
    DecisionReviewUpdate,
    EditHistoryEntry,
    ExternalRefRead,
    MeetingReview,
    Outbound,
    OutboundBlocked,
    OutboundDecision,
    ReviewAmbiguous,
    ReviewDecision,
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
    # TODO(강민구): #10 — reclassify the utterance in ext_classifications, which
    # does not exist until the classifier lands. #11 — build an action item from
    # a confirmed commitment; the description and due date come from slot
    # filling, and inventing them here would put a guess where a parse belongs.
    return row


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


def create_action_item(session: Session, payload: ActionItemCreate) -> ExtActionItem:
    """Add an item the model missed.

    ``confidence`` is 1.0 and ``origin`` is ``user``: a person typing an item is
    the certainty, and the origin is what edit cost is measured against.

    Counted as an edit. An item the model missed costs the user more than one it
    got wrong -- they have to notice the absence, which is the failure recall
    makes likely and the one editing cannot fix by itself.

    **Every foreign key on this row is checked before anything is written.** An
    unknown meeting is a 404 and a source that is not one of *this* meeting's
    utterances is a 422 naming the field. Without the check the first reached
    the client as a 500 from the foreign key, found by a local end-to-end run on
    2026-09-19, and the second was worse when the utterance did exist: an item
    on one meeting citing another meeting's utterance, whose words
    ``GET /action-items/{id}`` then quotes on this meeting's board.

    ``assignee_id`` gets the same treatment as the other two, for the reason
    ``slots.assignee_of`` already gives for the model's own path: the
    ``user_`` prefix a well-formed id carries does not promise the row is still
    there, and the field is a foreign key, so a deleted account or a typo would
    otherwise reach ``session.flush()`` as a 500 rather than a 422 naming the
    field.
    """
    if session.get(Meeting, payload.meeting_id) is None:
        raise NotFoundError("meeting", payload.meeting_id)
    if payload.assignee_id is not None and session.get(User, payload.assignee_id) is None:
        raise ValidationError("assignee_id does not name an existing user", field="assignee_id")
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


def read_model(
    item: ExtActionItem,
    *,
    assignee_name: str | None = None,
    summary: str | None = None,
    sync_refs: list[ExternalRefRead] | None = None,
    assignee_departed: bool = False,
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
    source_ids = live_source_ids(item)
    return ActionItemRead(
        id=item.id,
        meeting_id=item.meeting_id,
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
        deleted_source_count=len(item.sources) - len(source_ids),
        needs_reassignment=assignee_departed and item.status in _OPEN_STATUSES,
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
    )


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


def _is_team_member(session: Session, *, user_id: str, team_id: str) -> bool:
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.user_id == user_id, TeamMember.team_id == team_id
            )
        )
        is not None
    )


def _refuse(kind: str, ident: str, reader: User, reason: str) -> NotFoundError:
    # Ids only: a description or a decision statement is meeting content.
    log.info("extraction_read_refused", kind=kind, ident=ident, reader_id=reader.id, reason=reason)
    return NotFoundError(kind, ident)


def _require_member_of_meeting(
    session: Session, meeting_id: str, reader: User, *, kind: str, ident: str
) -> None:
    team_id = session.scalar(select(Meeting.team_id).where(Meeting.id == meeting_id))
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
    if visible_to is not None:
        query = query.join(Meeting, Meeting.id == ExtActionItem.meeting_id).where(
            Meeting.team_id.in_(select(TeamMember.team_id).where(TeamMember.user_id == visible_to))
        )

    items = list(session.scalars(query))
    names = assignee_names(session, items)
    departed = departed_assignees(session, items)
    summaries = action_item_summaries(session, items)
    refs = action_item_external_refs(session, [item.id for item in items])
    return [
        read_model(
            item,
            assignee_name=names.get(item.assignee_id) if item.assignee_id else None,
            summary=summaries.get(item.id),
            sync_refs=refs.get(item.id, []),
            assignee_departed=item.id in departed,
        )
        for item in items
    ]


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
    return ActionItemDetail(
        **read_model(
            item, assignee_name=name, summary=summary, sync_refs=refs, assignee_departed=departed
        ).model_dump(),
        sources=source_utterances(session, item.id),
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

    A new ``assignee_id`` gets the same existence check ``create_action_item``
    gives it -- the field is the same foreign key either way, and clearing it
    (``None``) needs no check at all.
    """
    changes = payload.changes()
    if not changes:
        return item

    new_assignee = changes.get("assignee_id")
    if new_assignee is not None and session.get(User, new_assignee) is None:
        raise ValidationError("assignee_id does not name an existing user", field="assignee_id")

    for field, value in changes.items():
        setattr(item, field, value.value if isinstance(value, ActionStatus) else value)
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
) -> list[ExtDecision]:
    """Rebuild this meeting's decisions from its classified utterances.

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
    (its own docstring), so it needs the same explicit delete or a decision
    whose id comes back after a gap would inherit a stale "already sent to
    Notion" claim and the resync it needs would silently never happen.

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

    # Only the model's decisions are rebuilt. One a person added is not derived
    # from labels, so no rerun can recompute it (#246).
    model_made = (ExtDecision.meeting_id == meeting_id, ExtDecision.origin == "model")
    existing_ids = set(session.scalars(select(ExtDecision.id).where(*model_made)))

    gone = existing_ids - fresh.keys()
    if gone:
        session.execute(delete(ExtDecisionReview).where(ExtDecisionReview.decision_id.in_(gone)))
        session.execute(delete(ExtDecisionRef).where(ExtDecisionRef.decision_id.in_(gone)))
        session.execute(delete(ExtDecisionSource).where(ExtDecisionSource.decision_id.in_(gone)))
        session.execute(delete(ExtDecision).where(ExtDecision.id.in_(gone)))

    if fresh:
        upsert = _insert_if_absent_into(session, ExtDecision).values(
            [
                {
                    "id": id_,
                    "meeting_id": meeting_id,
                    "statement": group.statement,
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
            statement=_confirmed_statement(row, reviews.get(row.id)),
            source_utterance_ids=[
                source.utterance_id for source in sorted(row.sources, key=lambda s: s.position)
            ],
            confidence=row.confidence,
        )
        for row in rows
        if (review := reviews.get(row.id)) is None or review.status != "rejected"
    ]


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


# --- a speaker identified after extraction (#360) ------------------------------


def fill_identified_assignees(session: Session) -> list[ExtActionItem]:
    """Give an item its speaker's account once A has identified the speaker.

    A commitment by an unidentified speaker is drafted with only the label
    ("Speaker 2", ``slots.assignee_of``). When somebody later confirms who that
    was, A fills ``participants.user_id``, and nothing announces it -- #360
    settled on consumers reading it back rather than on a new event. This
    finds the model's items still holding only a label whose source utterances
    all belong to one identified, consenting participant, and sets that
    account as the assignee, clearing the label, as a fresh extraction would.

    **A person's choice is never overwritten.** An item whose assignee a person
    has edited -- set, cleared or relabelled -- is left alone, and the update
    is conditional on ``assignee_id`` still being empty, so an edit committed
    while this ran wins. No ``ext_edit_events`` row: that table counts a
    person's corrections (ADR 0006), and this is neither.

    Returns the items it changed.
    """
    assignee_edited = (
        select(ExtEditEvent.id)
        .where(
            ExtEditEvent.action_item_id == ExtActionItem.id,
            ExtEditEvent.fields.like("%assignee%"),
        )
        .exists()
    )
    rows = session.execute(
        select(ExtActionItem.id, Participant.user_id)
        .join(ExtActionItemSource, ExtActionItemSource.action_item_id == ExtActionItem.id)
        .join(Utterance, Utterance.id == ExtActionItemSource.utterance_id)
        .join(Participant, Participant.id == Utterance.participant_id)
        .join(User, User.id == Participant.user_id)
        .where(
            ExtActionItem.assignee_id.is_(None),
            ExtActionItem.assignee_label.is_not(None),
            ExtActionItem.origin == "model",
            Participant.consented.is_(True),
            ~assignee_edited,
        )
    ).all()
    speakers: dict[str, set[str]] = {}
    for item_id, user_id in rows:
        speakers.setdefault(item_id, set()).add(user_id)

    filled: list[ExtActionItem] = []
    for item_id, users in sorted(speakers.items()):
        if len(users) != 1:
            # Sources spoken by two people: whose promise it is was never the
            # speaker's alone, and a person decides.
            continue
        (user_id,) = users
        changed = session.scalar(
            update(ExtActionItem)
            .where(ExtActionItem.id == item_id, ExtActionItem.assignee_id.is_(None))
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
    commitments = [u for u in classified if u.kind is UtteranceKind.COMMITMENT]
    if not commitments:
        return {}

    position = {utterance.id: index for index, utterance in enumerate(classified)}
    requests = []
    for utterance in commitments:
        index = position[utterance.id]
        start = max(0, index - MAX_CONTEXT_UTTERANCES)
        context = tuple(u.text for u in classified[start:index] if u.text)
        after_end = index + 1 + MAX_CONTEXT_AFTER
        context_after = tuple(u.text for u in classified[index + 1 : after_end] if u.text)
        requests.append(
            ResolutionRequest(target=utterance.text, context=context, context_after=context_after)
        )

    resolved = resolver.resolve(requests)
    return dict(zip((u.id for u in commitments), resolved, strict=True))


def build_action_items(
    session: Session,
    *,
    meeting_id: str,
    utterances: Sequence[TranscriptUtterance],
    classified: Sequence[ClassifiedUtterance],
    resolved: Mapping[str, str] | None = None,
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
    """
    resolved = resolved or {}
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
        assignee = assignee_of(said.speaker_id, said.speaker, known=known)
        due = parse_due(said.text, day)
        description = resolved.get(utterance.id, said.text)
        items.append(
            ExtActionItem(
                meeting_id=meeting_id,
                description=description,
                description_resolved=description != said.text,
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
        status=review.status if review else "pending",  # type: ignore[arg-type]
        suggested=_suggested(decision.confidence),
        source_utterance_ids=[
            source.utterance_id for source in sorted(decision.sources, key=lambda s: s.position)
        ],
        sync_refs=[
            ExternalRefRead(system=ref.system, url=ref.url, external_id=ref.external_id)  # type: ignore[arg-type]
            for ref in refs
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
        session.flush()

    return _read_decision(session, decision)


def outbound_for_meeting(session: Session, meeting_id: str) -> Outbound:
    """Exactly what may leave for Notion or Slack: nothing unconfirmed (#246).
    Jira was dropped from the product (#82).

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


class NotionPages(Protocol):
    """The calls the sync makes. ``NotionClient`` and ``fakes.FakeNotion``
    both fit."""

    def create_page(self, database_id: str, properties: dict[str, Any]) -> str: ...
    def update_page(self, page_id: str, properties: dict[str, Any]) -> None: ...
    def page_state(self, page_id: str) -> str: ...


PageOutcome = Literal["updated", "replaced", "archived"]


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
    assert ref.external_id is not None
    try:
        notion.update_page(ref.external_id, update)
    except PermanentIntegrationError:
        state = notion.page_state(ref.external_id)
        if state == "archived":
            return "archived"
        if state != "deleted":
            raise
        page_id = notion.create_page(database_id, create)
        ref.external_id = page_id
        ref.url = notion_url(page_id)
        return "replaced"
    return "updated"


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
        "status": {"select": {"name": item.status}},
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

    Nothing is sent for an item that is gone or one still waiting for
    confirmation. The first send is decided by the database: the claim is an
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
    hears which, for a caller that counts (``notion_backfill``).
    """
    names = property_names or NOTION_PROPERTIES

    existing = session.get(ExtExternalRef, (action_item_id, NOTION), with_for_update=True)
    if existing is not None:
        # Claim and create share one transaction (below), so a row that made
        # it to the database has its page id -- there is no committed row
        # from a claim whose create never ran.
        item = session.get(ExtActionItem, action_item_id, populate_existing=True)
        if item is None or item.status == ActionStatus.NEEDS_CONFIRMATION.value:
            return existing
        meeting = session.get(Meeting, item.meeting_id)
        title = meeting.title if meeting else None
        outcome = _update_or_replace_page(
            notion,
            existing,
            database_id=database_id,
            update=notion_properties(item, title, names, clear_missing=True),
            create=notion_properties(item, title, names),
        )
        log.info(
            "extraction_notion_updated",
            action_item_id=item.id,
            meeting_id=item.meeting_id,
            page=outcome,
        )
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
        if item is None or item.status == ActionStatus.NEEDS_CONFIRMATION.value:
            return existing
        meeting = session.get(Meeting, item.meeting_id)
        title = meeting.title if meeting else None
        outcome = _update_or_replace_page(
            notion,
            existing,
            database_id=database_id,
            update=notion_properties(item, title, names, clear_missing=True),
            create=notion_properties(item, title, names),
        )
        log.info(
            "extraction_notion_updated_after_claim_race",
            action_item_id=item.id,
            page=outcome,
        )
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

    Nothing goes for a decision that is gone or is not confirmed (#246). The
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
            return existing
        meeting = session.get(Meeting, decision.meeting_id)
        statement = _confirmed_statement(decision, review)
        properties = decision_notion_properties(
            statement, decision, meeting.title if meeting else None, names
        )
        outcome = _update_or_replace_page(
            notion, existing, database_id=database_id, update=properties, create=properties
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
            return existing
        meeting = session.get(Meeting, decision.meeting_id)
        statement = _confirmed_statement(decision, review)
        properties = decision_notion_properties(
            statement, decision, meeting.title if meeting else None, names
        )
        outcome = _update_or_replace_page(
            notion, existing, database_id=database_id, update=properties, create=properties
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
