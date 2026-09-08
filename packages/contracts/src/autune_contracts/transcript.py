"""A -> B, C, D. The canonical meeting record."""

from __future__ import annotations

from pydantic import Field, field_validator

from ._base import ContractModel, Payload
from .enums import TranscriptSource


class Utterance(ContractModel):
    """One continuous stretch of speech by one speaker.

    ``text`` is already PII-masked. There is no unmasked form to recover.
    """

    id: str = Field(pattern=r"^utt_")
    speaker: str = Field(description="Display label. 'Speaker 2' when unidentified.")
    speaker_id: str | None = Field(
        default=None,
        description="None when diarized but not identified. Consumers must handle this.",
    )
    role: str | None = Field(default=None, description="None when unidentified or unset.")
    start: float = Field(ge=0, description="Seconds from the start of the recording.")
    end: float = Field(ge=0)
    text: str = Field(description="PII-masked.")
    confidence: float = Field(ge=0, le=1)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError("end must not precede start")
        return v


class PrivacyFlags(ContractModel):
    """Proof that module A honored its obligations before publishing.

    ``original_audio_deleted`` being False means the pipeline is broken.
    Consumers fail loudly rather than proceeding.
    """

    original_audio_deleted: bool
    pii_masked: bool


class TranscriptMetadata(ContractModel):
    duration: float = Field(ge=0, description="Seconds.")
    participants: list[str] = Field(default_factory=list)
    source: TranscriptSource
    language: str = "ko"
    privacy: PrivacyFlags


class TranscriptReady(Payload):
    utterances: list[Utterance]
    metadata: TranscriptMetadata

    def require_privacy_guarantees(self) -> None:
        """Raise unless module A finished its privacy obligations.

        Every consumer calls this before touching ``utterances``.
        """
        p = self.metadata.privacy
        if not p.original_audio_deleted:
            raise ValueError(f"{self.meeting_id}: raw audio was not deleted; refusing to process")
        if not p.pii_masked:
            raise ValueError(
                f"{self.meeting_id}: transcript is not PII-masked; refusing to process"
            )
