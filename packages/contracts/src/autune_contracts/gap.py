"""C -> E. What the meeting should have covered and did not."""

from __future__ import annotations

from pydantic import Field

from ._base import ContractModel, Payload
from .enums import GapSeverity


class Topic(ContractModel):
    id: str = Field(pattern=r"^topic_")
    label: str
    centrality: float = Field(ge=0, le=1)
    utterance_ids: list[str] = Field(default_factory=list)


class Participation(ContractModel):
    """Whether a participant spoke on a topic — coverage, not speech volume.

    This must never become a per-person talk-time metric. See
    docs/architecture/privacy.md section 3.
    """

    topic_id: str
    spoke: list[str] = Field(default_factory=list)
    silent: list[str] = Field(default_factory=list)


class Gap(ContractModel):
    id: str = Field(pattern=r"^gap_")
    category: str
    title: str
    severity: GapSeverity
    risk_score: float = Field(ge=0, le=1)
    template_item: str | None = None
    related_topic_ids: list[str] = Field(default_factory=list)
    suggested_question: str | None = None


class GapReport(Payload):
    gaps: list[Gap] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    participation: list[Participation] = Field(default_factory=list)
