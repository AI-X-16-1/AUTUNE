"""B -> E. Classifications, action items, and unresolved agreement."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

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


STANCE_MIN_IDENTIFIED_PER_ROLE = 3
"""A role is reported only when at least this many identified people held it."""


class RoleStance(ContractModel):
    """How many people in one role backed a decision or raised a concern on it.

    Counts, never identities. There is no participant id, user id or speaker
    label here, and adding one is a privacy violation rather than an additive
    change: who opposed a decision is a per-person record of behaviour in a
    meeting, the same shape docs/architecture/privacy.md section 3 forbids for
    speaking ratios.

    A role appears only when the meeting had at least
    `STANCE_MIN_IDENTIFIED_PER_ROLE` identified people in it. In a small team a
    role is a person, and a count over one person is that person's stance.

    People are counted by distinct ``user_id``, each in at most one of the two
    counts, so ``supporting + concerns <= identified``. A person who spoke on the
    decision without doing either is in neither count, so this is not coverage.

    **A unanimous role is not representable.** ``supporting == identified`` or
    ``concerns == identified`` says what every person in the role did, which is
    each person's stance however many of them there are -- the gap k-anonymity
    leaves (review on #232). A producer leaves such a role out. A count of zero is
    still allowed: whether "nobody in the role raised a concern" identifies anyone
    is open on #232.
    """

    role: str
    identified: int = Field(
        ge=STANCE_MIN_IDENTIFIED_PER_ROLE,
        description="Identified people in the role at the meeting, carried so the gate is checked.",
    )
    supporting: int = Field(ge=0, description="People in this role who voiced support.")
    concerns: int = Field(ge=0, description="People in this role who raised a concern.")

    @model_validator(mode="after")
    def _counts_fit_the_role(self) -> RoleStance:
        if self.supporting + self.concerns > self.identified:
            raise ValueError(
                "supporting and concerns together cannot exceed the people identified in the role"
            )
        if self.identified in (self.supporting, self.concerns):
            raise ValueError("a unanimous role reveals every person's stance and is left out")
        return self


class Decision(ContractModel):
    """A decision the meeting settled.

    Distinct from a `Classification` with ``kind="decision"``: that marks one
    utterance, while a decision is often spread over several. B owns deciding
    *what counts as a decision in this meeting*; D owns deciding *whether it is
    the same decision as one from a past meeting*.
    """

    id: str = Field(pattern=r"^dec_")
    statement: str = Field(description="The decision as settled, in one sentence.")
    source_utterance_ids: list[str] = Field(
        default_factory=list, description="One decision may span several utterances."
    )
    confidence: float = Field(ge=0, le=1)
    stance_by_role: list[RoleStance] = Field(
        default_factory=list,
        description=(
            "Per role, never per person. Empty when no role cleared the gate or "
            "stance was not computed; the two are not distinguished."
        ),
    )


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
    decisions: list[Decision] = Field(
        default_factory=list,
        description="Consumed by D to build decision lineage across meetings.",
    )
    classifications: list[Classification] = Field(default_factory=list)
    ambiguous_agreements: list[AmbiguousAgreement] = Field(default_factory=list)
