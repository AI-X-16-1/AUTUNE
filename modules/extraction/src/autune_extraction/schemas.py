"""Internal schemas for module B.

Anything another module needs belongs in ``packages/contracts``, not here.
These are request and response bodies for this module's own HTTP surface, which
nobody else parses.
"""

from __future__ import annotations

from datetime import date

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
