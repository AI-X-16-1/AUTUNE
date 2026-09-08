"""B -> E. Classifications, action items, and unresolved agreement."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from ._base import ContractModel, Payload
from .enums import ActionStatus, ExternalSystem, UtteranceKind


class ExternalRef(ContractModel):
    system: ExternalSystem
    url: str
    external_id: str | None = None


class ActionItem(ContractModel):
    id: str = Field(pattern=r"^act_")
    description: str
    assignee_id: str | None = Field(default=None, description="None until someone is assigned.")
    assignee_label: str | None = None
    due_date: date | None = None
    source_utterance_ids: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.NEEDS_CONFIRMATION
    confidence: float = Field(ge=0, le=1)
    external_refs: list[ExternalRef] = Field(default_factory=list)


class Classification(ContractModel):
    utterance_id: str = Field(pattern=r"^utt_")
    kind: UtteranceKind
    confidence: float = Field(ge=0, le=1)
    nli_verified: bool = False


class AmbiguousAgreement(ContractModel):
    """Assent too weak to treat as a commitment; the speaker was asked to confirm."""

    utterance_id: str = Field(pattern=r"^utt_")
    reason: str
    confirmation_sent: bool = False


class ExtractionResult(Payload):
    action_items: list[ActionItem] = Field(default_factory=list)
    classifications: list[Classification] = Field(default_factory=list)
    ambiguous_agreements: list[AmbiguousAgreement] = Field(default_factory=list)
