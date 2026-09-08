"""Privacy obligations that the contract itself enforces.

See docs/architecture/privacy.md.
"""

from __future__ import annotations

import pytest

from autune_contracts import IntelligenceSnapshot, TranscriptReady, fixtures


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
