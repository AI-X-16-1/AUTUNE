"""Internal schemas for module B.

Anything another module needs belongs in ``packages/contracts``, not here.
These are request and response bodies for this module's own HTTP surface, which
nobody else parses.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autune_contracts.enums import ActionStatus


class ActionItemCreate(BaseModel):
    """An item the model missed, typed by a person.

    No ``confidence``: a person typing an item is the certainty, and the service
    stores 1.0. Letting a caller set it would put a model score on a human
    judgement and quietly corrupt the metric that compares the two.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: str = Field(pattern=r"^mtg_")
    description: str = Field(min_length=1, max_length=2000)
    assignee_id: str | None = Field(default=None, pattern=r"^user_")
    assignee_label: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    source_utterance_ids: list[str] = Field(default_factory=list)
    """Optional. A hand-added item often has no utterance behind it — that is
    what "the model missed it" means — and the drawer renders that state."""


class ActionItemUpdate(BaseModel):
    """A correction to one item. Every field optional; absent means unchanged.

    ``meeting_id`` and ``origin`` are not here. An item does not move between
    meetings, and rewriting where it came from would erase the distinction edit
    cost is measured on.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, min_length=1, max_length=2000)
    assignee_id: str | None = Field(default=None, pattern=r"^user_")
    assignee_label: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    status: ActionStatus | None = None

    def changes(self) -> dict[str, object]:
        """Only the fields the caller actually sent.

        ``exclude_unset`` rather than ``exclude_none``: clearing a due date is a
        correction like any other, and the two are indistinguishable without it.
        """
        return self.model_dump(exclude_unset=True)


class ExternalRefRead(BaseModel):
    """Where one confirmed item or decision stands with one outside system.

    Not ``autune_contracts.extraction.ExternalRef``: that type is the outbound
    event to D and E, and it requires ``url`` because it is only ever built for
    a ref that finished. This is this module's own read, so it has to say the
    other two states a sync can be in -- ``url`` is ``None`` while the row is
    claimed but the call has not returned (in flight) or did not survive it
    (failed); no row at all means nothing has tried yet, and neither list nor
    drawer constructs one for that case.

    On the list, not gated behind the drawer the way ``sources`` is: a system
    name, a url and an id are not meeting content, so ``description`` and
    ``assignee_label``'s reasoning for being on the list already covers this.
    """

    system: Literal["notion"]
    """Jira was dropped from the product (#82): both its credential paths tie
    a workspace to whoever set it up. The DB's own check constraint still
    allows ``'jira'`` (unused, kept rather than a migration for a value that
    only removes a possibility) -- this type is the narrower, honest answer
    for what the API actually returns."""
    url: str | None
    external_id: str | None


class ActionItemRead(BaseModel):
    """One item as this module's own screens read it.

    Not ``from_attributes``: two of these fields are not columns on the row.
    ``source_utterance_ids`` lives in the link table and ``is_candidate`` is
    derived from a setting, so an ``ExtActionItem`` alone cannot answer either.
    Built by ``service.read_model``.
    """

    id: str
    meeting_id: str
    meeting_title: str | None = None
    """The title of the meeting the item came from. The board across every
    meeting shows it on each card, so a person can tell which meeting an item
    belongs to without opening it (mentoring, 2026-10-01). Read with the item,
    never stored on it."""
    description: str
    description_resolved: bool = False
    """Whether ``description`` is ``ReferenceResolver``'s rewrite rather than
    the source utterance verbatim (#175, #366). S18 shows this so a reviewer
    knows which descriptions are the speaker's own words and which are a
    model's paraphrase of them -- worth a closer look, given #366's own review
    found the paraphrase wrong often enough to matter. Always ``False`` for a
    hand-added item, a raw quote (no resolver configured), or a resolution
    that failed every check and fell back to the quote."""
    assignee_id: str | None
    assignee_label: str | None
    assignee_name: str | None = None
    """The assignee's current display name, read fresh from ``users`` -- never
    stored. ``assignee_label`` is "the name as spoken, kept when it does not
    resolve to an account" (``ExtActionItem.assignee_label``'s own docstring);
    an identified assignee has no label at all, so a card showing only
    ``assignee_label`` reads an assigned item as unassigned. This is the other
    half: set only when ``assignee_id`` resolves to an account that still
    exists, so a screen can show *somebody's name* without caring which half
    filled it in."""
    due_date: date | None
    due_text: str | None = None
    """The phrase the date was parsed from ("다음 주 화요일", "9/20"), kept
    beside the resolved ``due_date`` rather than replacing it on this response
    -- S18 shows both (ui-spec): the resolved date to act on, and the speaker's
    own words so a person can judge the parse rather than take it on faith.
    ``None`` for a hand-added item, or a model item where no date phrase was
    said at all; ``slots.parse_due`` leaves both fields empty rather than
    guessing one from the other."""
    status: str
    confidence: float
    origin: str

    source_utterance_ids: list[str]
    """The utterances this item was drawn from. Empty for a hand-added item.

    This response did not carry them until now, and the gap was not cosmetic:
    S17's card reads the list to decide between "근거 발화 N건" and "직접 추가",
    and an absent list is an empty one. Every model-extracted item was therefore
    labelled as one somebody typed -- the distinction ADR 0006 and the edit-cost
    metric are built on, printed inverted, with nothing failing.

    Ordered by row id, which is insertion order. ``ext_action_item_sources`` has
    no position column; when the drawer needs them in spoken order that is the
    change to make, not a sort here over a field that does not exist.

    Only utterances that still exist. One that was deleted is counted in
    ``deleted_source_count`` instead.
    """

    deleted_source_count: int = 0
    """How many of this item's sources were deleted after it was made (ADR 0007,
    "Missing attribution is shown, not hidden").

    Without it, a model item whose transcript went has an empty
    ``source_utterance_ids`` -- the same shape as a hand-added item -- and the
    drawer printed "직접 추가한 항목" over it. ``origin`` says who made the
    item; this says its evidence is gone, and the screen needs both."""

    needs_recheck: bool = False
    """A line this was drawn from was corrected after it was made (a PII report,
    #586), and the text shown may still need a person's eye: a summary rewritten
    from the corrected line, or their own wording. Cleared by their next edit."""

    needs_reassignment: bool = False
    """An open item (``todo`` or ``in_progress``) whose assignee is no longer a
    member of the meeting's team (ADR 0007, "An open commitment is reassigned,
    never orphaned"). S17 puts it at the top of its column.

    Derived on every read from ``team_members``, the way module D filters
    ``key_stakeholders_absent``: nothing in the product removes a member yet,
    so there is no departure event to store it from. Not a member covers a
    guest who never was one as well as someone who left -- either way nobody
    on the team holds the item, which is what the flag is for.

    When the assignee is not a member, ``assignee_id`` and ``assignee_name`` on
    this response are ``None`` whatever the status: ADR 0007's "its assignee
    clears", applied at read time. The stored column keeps the id, so a person
    who rejoins gets their items back. A ``done`` item does not need
    reassigning and stays ``False``."""

    is_candidate: bool
    """Whether the model was unsure enough that this is shown apart from the
    board rather than asserted on it.

    Decided here rather than in the browser. The threshold is a property of the
    classifier, and a client comparing against a number it happens to hold is one
    deploy away from disagreeing with the server about which items the meeting
    produced.

    **False for everything while ``candidate_confidence`` is unset**, which is
    its default until #10 measures one. **False once a person has confirmed
    the item**, whatever its confidence -- confirming moves ``status``, not
    the model's score, so scoring only on confidence would keep a low-
    confidence item candidate forever, back on the review screen every visit
    after the one where it was already confirmed (#295).
    """

    sync_refs: list[ExternalRefRead]
    """One entry per system this item has been claimed for -- today, at most
    ``notion`` (#30). ``jira`` was designed (ui-spec S18, S28) but dropped
    before being built (#82), so it never appears rather than being shown
    always-empty. Ordered by ``created_at``, which for one system is also
    insertion order.

    Not ``external_refs``: ``ActionItem`` (the contract this extends) already
    has a field by that name -- the outbound one, ``list[ExternalRef]``, which
    requires ``url`` -- and TypeScript's `extends` cannot narrow an optional,
    stricter-typed inherited field to this one, which also reports the
    in-flight and failed states. Same name collision, same fix, as
    ``ReviewDecision.sync_refs`` below would have hit if ``Decision`` carried
    the field too."""

    summary: str | None = None
    """A one-line preview of the item's sources beyond ``description`` itself.
    Rule-based (the longest of them, truncated), and only when there is more
    than one -- with a single source ``description`` already is that sentence,
    and a second copy of it would say nothing ``description`` does not. See
    ``ReviewDecision.summary`` for why a chosen line belongs on the list."""


class SourceUtterance(BaseModel):
    """One utterance an item was drawn from, as the drawer quotes it.

    The text is what module A stored, which is after masking. There is no
    unmasked string anywhere this could have been read from -- privacy.md
    section 2 puts the masker before the first write.
    """

    id: str
    text: str


class EditHistoryEntry(BaseModel):
    """One thing a person did to an item: which fields, when -- never the value
    before or after, and never who (#109, ADR 0003)."""

    kind: Literal["created", "edited"]
    fields: list[str]
    """For ``edited``: the fields changed, e.g. ``["due_date"]``. Empty for
    ``created`` and for edits recorded before fields were kept."""
    at: datetime


class SummaryDecision(BaseModel):
    """A decision as the summary tab lists it: the wording a person confirmed,
    or the model's while it is still pending."""

    id: str
    statement: str
    status: Literal["pending", "confirmed"]


MAX_NOTE_CHARS = 2000


ConfirmationAnswer = Literal["commitment", "decision", "not_commitment"]


class MyConfirmation(BaseModel):
    """One ambiguous agreement the reader said, as the web asks them about it.

    ``text`` is their own line, masked as stored. ``answer`` is what they said,
    or ``None`` while they have not; a later answer replaces an earlier one.
    """

    utterance_id: str
    text: str
    answer: ConfirmationAnswer | None = None


class ConfirmationAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: ConfirmationAnswer


class MeetingNoteUpdate(BaseModel):
    """The memo, whole. Blank removes it."""

    body: str = Field(max_length=MAX_NOTE_CHARS)


class MeetingSummary(BaseModel):
    """S15's 요약 tab, v1 (#421, WBS 4.9): B's own rows in three levels, no model.

    The tab reads these top down -- counts, then the decisions and items
    themselves, then (through the 액션 tab's drawer) the lines they came from.
    Nothing here is a verbatim quotation: descriptions and statements are the
    same fields the board already lists. A summary written by an LLM over the
    whole meeting is v2, and waits on #392.
    """

    meeting_id: str
    decisions: list[SummaryDecision]
    """Confirmed first, then pending, each in the order they were settled.
    A rejected one is not a decision of the meeting and is left out."""
    action_items: list[ActionItemRead]
    """Every item of the meeting, whatever its status."""
    open_questions: int
    """Utterances classified as open questions -- asked, not settled."""
    ambiguous_waiting: int
    """Ambiguous agreements not yet answered: never asked, or asked and within
    the confirmation window."""
    note: str | None = None
    note_updated_at: datetime | None = None


class ActionItemDetail(ActionItemRead):
    """One item and its evidence, for S18.

    The list carries the source utterances' ids, never their words: a verbatim
    quotation leaves the server only when the drawer asks for one item's.

    That does not make the list free of meeting content. ``description`` is
    drawn from what was said and ``assignee_label`` is a person's name, so
    nothing may forward a list response outside our infrastructure on the
    grounds that it quotes nobody -- ``check_outbound`` catches the shapes of
    personal data, not a Korean name or the sentence that settled a decision.
    """

    sources: list[SourceUtterance]
    """In the order they were spoken, which is the order the argument was made.

    ``source_utterance_ids`` above stays in insertion order: it is built from
    the row alone. This list is read from ``utterances`` anyway, so the spoken
    order comes with it at no extra cost.
    """

    context: list[SourceUtterance] = Field(default_factory=list)
    """What was said just before the first source, in spoken order, so a sentence
    with nothing to point at ("다음 주까지 볼게요") can be read with the thing it
    is about. Not what this was drawn from, only the lines around it, which is
    why it is apart from ``sources``. A speaker who did not consent is never here
    -- the line every read of the transcript draws (privacy.md section 5)."""

    related: list[SourceUtterance] = Field(default_factory=list)
    """The lines the summary says it was written from, beyond the commitment itself,
    in spoken order -- a turn that names the thing, a line elsewhere about the
    same subject (``ext_action_item_related``). Empty when the description is the
    quote, a person's own words, or the resolver was not asked to cite. The
    person checking the summary reads these beneath it and corrects the sentence
    if it says more than they show. Same consent filter as ``context``."""

    history: list[EditHistoryEntry] = Field(default_factory=list)
    """What people did to the item, oldest first (S18, #109). Empty for an item
    the model extracted and nobody has touched since."""


class CarriedOverItem(ActionItemRead):
    """An open item from an earlier meeting of the same team, with the meeting
    it was made in -- the popup says where each one came from."""

    meeting_title: str
    meeting_started_at: datetime | None


class CarriedOver(BaseModel):
    """What earlier meetings left open, for the popup a meeting's review opens
    with (PRD 5.2, "incomplete items from previous meetings resurface in the
    next one"; WBS 4.8).

    ``open`` and ``overdue`` count everything; ``items`` is the most urgent
    part of it -- overdue first, then the nearest due date, undated last -- so
    a team with a long tail still sees what matters without a list to scroll.
    """

    open: int
    overdue: int
    items: list[CarriedOverItem]


# --- review before anything leaves (#246) ------------------------------------


class DecisionReviewUpdate(BaseModel):
    """A person's verdict on one proposed decision. Both fields optional.

    ``pending`` is allowed so a mis-click can be undone. ``statement`` rewords the
    decision; sending the model's own wording back clears the rewording rather than
    storing a copy of it.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "confirmed", "rejected"] | None = None
    statement: str | None = Field(default=None, min_length=1, max_length=2000)


class DecisionCreate(BaseModel):
    """A decision the model missed, typed by a person.

    No ``confidence``: a person typing it is the certainty, as with
    ``ActionItemCreate``. It is confirmed from the moment it exists.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: str = Field(pattern=r"^mtg_")
    statement: str = Field(min_length=1, max_length=2000)
    source_utterance_ids: list[str] = Field(default_factory=list)
    """Optional, in spoken order. Each must be an utterance of this meeting."""


class ReviewDecision(BaseModel):
    """One decision as S15 lists it -- proposed by the model or added by a person."""

    id: str
    statement: str
    """What will be sent: the person's rewording when there is one, else the model's."""

    model_statement: str
    """What the model proposed, kept beside the rewording so the screen can show both."""

    confidence: float
    origin: Literal["model", "user"]
    needs_recheck: bool = False
    """A source line was corrected since a person typed or reworded this (#586):
    B cannot correct their wording, so it asks them to look. Cleared by their
    next review."""
    status: Literal["pending", "confirmed", "rejected"]
    suggested: bool | None
    """Whether the screen should pre-check it: the confidence clears
    ``candidate_confidence``. ``None`` while that setting is unset -- there is no
    measured line yet, and pre-checking everything or nothing would both be a
    claim the numbers do not support."""

    source_utterance_ids: list[str]

    sync_refs: list[ExternalRefRead]
    """One entry per system this decision has been claimed for -- today, at
    most ``notion`` (#30). No drawer exists for a decision (S15 is the whole
    screen), so this rides on the list the way ``ActionItemRead.sync_refs``
    does; a URL is not meeting content. Named ``sync_refs`` rather than
    ``external_refs`` for the same reason as that one -- consistency, though
    ``Decision`` (the contract) carries no field of that name to collide with."""

    summary: str | None = None
    """A one-line preview of what the source utterances said, so the list says
    more than a count. Rule-based, not a model: the longest of them, truncated
    -- see ``service.decision_summaries``. ``None`` when there is nothing to
    summarise (a decision with no sources -- a data problem, not a normal
    state).

    **Still a quotation, on the list, on purpose.** ``ActionItemDetail`` draws
    the line at the *set* of sources -- the drawer's whole evidence, never
    forwarded whole -- not at any single derived line; ``description`` and
    ``assignee_label`` already put content on this same list. One chosen
    sentence is that kind of line, not the other."""


class ReviewAmbiguous(BaseModel):
    """One weak assent and where its question to the speaker stands.

    Read-only here. The speaker answers by DM (``ext_confirmations``); who else may
    answer on their behalf is #246 point 1.
    """

    utterance_id: str
    outcome: Literal["not_asked", "pending", "undecided", "resolved"]
    resolved_kind: str | None


class DecisionDetail(ReviewDecision):
    """One decision and the words it was settled in, for the row S15 expands.

    The list carries the source utterances' ids and one preview line, never the
    whole set of quotations -- the same line ``ActionItemDetail`` draws, and for
    the same reason: a verbatim quotation leaves the server only when one row's
    is asked for.
    """

    sources: list[SourceUtterance]
    """In the order they were spoken -- the proposal first, the sentence that
    settled it last. Empty for a decision a person added, which has none."""

    context: list[SourceUtterance] = Field(default_factory=list)
    """What was said just before the first source, in spoken order, so a sentence
    with nothing to point at ("다음 주까지 볼게요") can be read with the thing it
    is about. Not what this was drawn from, only the lines around it, which is
    why it is apart from ``sources``. A speaker who did not consent is never here
    -- the line every read of the transcript draws (privacy.md section 5)."""

    related: list[SourceUtterance] = Field(default_factory=list)
    """The lines the model's write-up of this decision says it used, beyond the turns
    it was settled in (``ext_decision_related``), in spoken order. Empty when the
    statement is the assembled line or a person's own wording. Same consent filter
    as ``context``."""


class MeetingReview(BaseModel):
    """Everything in one meeting that needs a person before it goes anywhere."""

    meeting_id: str
    decisions: list[ReviewDecision]
    ambiguous_agreements: list[ReviewAmbiguous]
    action_items: list[ActionItemRead]
    """Items still ``needs_confirmation``, or below the candidate line."""

    pending_decisions: int


class OutboundDecision(BaseModel):
    id: str
    statement: str


class OutboundBlocked(BaseModel):
    """Something confirmed that still may not leave: its text carries personal data.

    The categories, never the values -- the same rule ``assert_masked`` follows
    for an exception message. The screen asks the person to reword it.
    """

    id: str
    kind: Literal["decision", "action_item"]
    categories: list[str]


class Outbound(BaseModel):
    """What confirm-and-send would send, and nothing else.

    The Notion and Slack sync (#30; Jira dropped, #82) is to read this and only
    this. A decision nobody confirmed is not in it, and neither is an item
    still waiting for confirmation.
    """

    meeting_id: str
    decisions: list[OutboundDecision]
    action_items: list[ActionItemRead]
    blocked: list[OutboundBlocked]
    """Confirmed, but held back by the personal-data screen. Not in the lists above."""
