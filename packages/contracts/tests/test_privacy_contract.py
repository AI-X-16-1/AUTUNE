"""Privacy obligations that the contract itself enforces.

See docs/architecture/privacy.md.
"""

from __future__ import annotations

import pytest

from autune_contracts import (
    STANCE_MIN_IDENTIFIED_PER_ROLE,
    ExtractionResult,
    IntelligenceSnapshot,
    RoleStance,
    TranscriptReady,
    fixtures,
)


def _transcript(**privacy: bool) -> dict:
    payload = fixtures.load("transcript_ready.short")
    payload["metadata"]["privacy"].update(privacy)
    return payload


def test_consumer_refuses_when_audio_was_not_deleted() -> None:
    t = TranscriptReady.model_validate(_transcript(original_audio_deleted=False))
    with pytest.raises(ValueError, match="raw audio was not deleted"):
        t.require_privacy_guarantees()


def test_consumer_refuses_unmasked_transcript() -> None:
    t = TranscriptReady.model_validate(_transcript(pii_masked=False))
    with pytest.raises(ValueError, match="not PII-masked"):
        t.require_privacy_guarantees()


def test_clean_transcript_passes() -> None:
    TranscriptReady.model_validate(_transcript()).require_privacy_guarantees()


def test_snapshot_carries_no_speaking_ratio() -> None:
    """Speaking ratios are delivered by DM and never stored or shared.

    If this test fails, someone added a per-person speech-volume field to a
    shared payload. That is a privacy violation, not a feature.
    """
    banned = {"speaking_ratio", "speaking_ratios", "talk_time", "speech_volume", "speaker_share"}
    assert banned.isdisjoint(IntelligenceSnapshot.model_fields)


def _stance(**overrides: object) -> dict:
    return {"role": "PM", "identified": 3, "supporting": 2, "concerns": 1, **overrides}


def test_role_stance_carries_no_identity() -> None:
    """Stance is counted per role and never tied to a person.

    If this test fails, someone gave `RoleStance` a way to say who backed or
    opposed a decision. That is a per-person behaviour record, not a feature.
    """
    assert set(RoleStance.model_fields) == {"role", "identified", "supporting", "concerns"}


def test_a_role_below_the_gate_is_not_representable() -> None:
    """In a small team a role is a person, so a count over it is that person's stance."""
    below = STANCE_MIN_IDENTIFIED_PER_ROLE - 1
    with pytest.raises(ValueError, match="greater than or equal"):
        RoleStance.model_validate(_stance(identified=below, supporting=0, concerns=0))


@pytest.mark.parametrize("field", ["supporting", "concerns"])
def test_a_stance_count_cannot_exceed_the_role(field: str) -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        RoleStance.model_validate(_stance(**{field: 4}))


def test_a_decision_without_stance_is_still_valid() -> None:
    decision = ExtractionResult.model_validate(fixtures.load("extraction_result")).decisions[0]
    assert decision.stance_by_role == []
