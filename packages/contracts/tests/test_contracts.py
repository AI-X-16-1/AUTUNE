"""Contract conformance. Every fixture must validate, and round-trip."""

from __future__ import annotations

import pytest

from autune_contracts import (
    CONTRACT_VERSION,
    ContextLinks,
    ExtractionResult,
    GapReport,
    IntelligenceSnapshot,
    Payload,
    TranscriptReady,
    UtteranceKind,
    fixtures,
    validate_major_version,
)

FIXTURE_MODELS: dict[str, type[Payload]] = {
    "transcript_ready.short": TranscriptReady,
    "transcript_ready.typical": TranscriptReady,
    "transcript_ready.unidentified": TranscriptReady,
    "extraction_result": ExtractionResult,
    "gap_report": GapReport,
    "context_links": ContextLinks,
    "intelligence_snapshot": IntelligenceSnapshot,
}


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_fixture_validates(name: str) -> None:
    FIXTURE_MODELS[name].model_validate(fixtures.load(name))


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_fixture_round_trips(name: str) -> None:
    model = FIXTURE_MODELS[name]
    once = model.model_validate(fixtures.load(name))
    twice = model.model_validate(once.model_dump(mode="json"))
    assert once == twice


def test_every_fixture_is_covered() -> None:
    assert set(fixtures.NAMES) == set(FIXTURE_MODELS)


def test_unidentified_speaker_is_representable() -> None:
    """The case consumers forget: diarized but not identified."""
    t = TranscriptReady.model_validate(fixtures.load("transcript_ready.unidentified"))
    assert t.utterances
    for u in t.utterances:
        assert u.speaker_id is None
        assert u.role is None
        assert u.speaker.startswith("Speaker ")


def test_additive_field_does_not_break_a_consumer() -> None:
    """An older consumer must ignore a field a newer producer added."""
    payload = fixtures.load("gap_report")
    payload["gaps"][0]["some_future_field"] = "added in 1.1"
    report = GapReport.model_validate(payload)
    assert report.gaps[0].id == "gap_001"


def test_major_version_mismatch_is_rejected() -> None:
    payload = fixtures.load("gap_report") | {"contract_version": "2.0"}
    with pytest.raises(ValueError, match="major version mismatch"):
        validate_major_version(GapReport.model_validate(payload))


def test_matching_major_version_passes() -> None:
    validate_major_version(GapReport.model_validate(fixtures.load("gap_report")))
    assert CONTRACT_VERSION.startswith("1.")


def test_utterance_kinds_are_exactly_five() -> None:
    assert {k.value for k in UtteranceKind} == {
        "commitment",
        "decision",
        "open_question",
        "concern",
        "ambiguous",
    }
