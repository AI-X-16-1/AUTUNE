"""D -> E. Links to past meetings and how decisions moved."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from ._base import ContractModel, Payload
from .enums import ChangeType, NliLabel


class TopicLink(ContractModel):
    topic_label: str
    linked_meeting_id: str = Field(pattern=r"^mtg_")
    linked_meeting_date: date
    similarity: float = Field(ge=0, le=1, description="Hybrid retrieval score.")
    rerank_score: float = Field(ge=0, le=1, description="Cross-encoder score; the one to trust.")


class DecisionChange(ContractModel):
    """One version of a decision, as tracked across meetings.

    Two identities meet here and they are not the same thing:

    ``thread_id`` is the lineage — the identity that persists across meetings,
    owned by D. ``source_decision_id`` is the decision B extracted from *this*
    meeting, which D matched into that thread.
    """

    thread_id: str = Field(
        pattern=r"^thr_", description="D's lineage identity, stable across meetings."
    )
    source_decision_id: str = Field(
        pattern=r"^dec_", description="The decision B extracted from this meeting."
    )
    current_statement: str
    previous_statement: str | None = None
    previous_meeting_id: str | None = None
    change_type: ChangeType
    nli_label: NliLabel | None = None
    confidence: float = Field(ge=0, le=1)
    key_stakeholders_absent: list[str] = Field(
        default_factory=list,
        description="User ids absent when the decision changed. Drives the drift warning.",
    )


class ContextLinks(Payload):
    """D's output. Topic linking and decision lineage have different inputs.

    Topic linking needs only the transcript, so it runs in parallel with B and C.
    Decision lineage needs B's decisions, so it runs after B. D publishes once
    both are in — or, if B never reports, with an empty ``decision_lineage`` and
    ``"extraction"`` in ``missing_sources``. A failure in B must not cost the
    user their topic links.
    """

    topic_links: list[TopicLink] = Field(default_factory=list)
    decision_lineage: list[DecisionChange] = Field(default_factory=list)
    missing_sources: list[str] = Field(
        default_factory=list,
        description="Modules that had not reported when this payload was built.",
    )
