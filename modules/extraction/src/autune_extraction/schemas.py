"""Internal schemas for module B.

Anything another module needs belongs in ``packages/contracts``, not here.
These are request and response bodies for this module's own HTTP surface, which
nobody else parses.
"""

from __future__ import annotations

from datetime import date
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


class ActionItemRead(BaseModel):
    """One item as this module's own screens read it.

    Not ``from_attributes``: two of these fields are not columns on the row.
    ``source_utterance_ids`` lives in the link table and ``is_candidate`` is
    derived from a setting, so an ``ExtActionItem`` alone cannot answer either.
    Built by ``service.read_model``.
    """

    id: str
    meeting_id: str
    description: str
    assignee_id: str | None
    assignee_label: str | None
    due_date: date | None
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
    """

    is_candidate: bool
    """Whether the model was unsure enough that this is shown apart from the
    board rather than asserted on it.

    Decided here rather than in the browser. The threshold is a property of the
    classifier, and a client comparing against a number it happens to hold is one
    deploy away from disagreeing with the server about which items the meeting
    produced.

    **False for everything while ``candidate_confidence`` is unset**, which is
    its default until #10 measures one.
    """

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
    status: Literal["pending", "confirmed", "rejected"]
    suggested: bool | None
    """Whether the screen should pre-check it: the confidence clears
    ``candidate_confidence``. ``None`` while that setting is unset -- there is no
    measured line yet, and pre-checking everything or nothing would both be a
    claim the numbers do not support."""

    source_utterance_ids: list[str]

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

    The Notion, Slack and Jira sync (#30) is to read this and only this. A decision
    nobody confirmed is not in it, and neither is an item still waiting for
    confirmation.
    """

    meeting_id: str
    decisions: list[OutboundDecision]
    action_items: list[ActionItemRead]
    blocked: list[OutboundBlocked]
    """Confirmed, but held back by the personal-data screen. Not in the lists above."""
