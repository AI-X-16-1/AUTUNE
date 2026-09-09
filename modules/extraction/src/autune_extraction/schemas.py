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
    """One item as this module's own screens read it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_id: str
    description: str
    assignee_id: str | None
    assignee_label: str | None
    due_date: date | None
    status: str
    confidence: float
    origin: str
