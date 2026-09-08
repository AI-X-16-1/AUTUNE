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
    decision_id: str = Field(pattern=r"^dec_")
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
    topic_links: list[TopicLink] = Field(default_factory=list)
    decision_lineage: list[DecisionChange] = Field(default_factory=list)
