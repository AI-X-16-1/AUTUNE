"""Module E gap-pattern classification — pure functions, no database, no ML."""

from __future__ import annotations

import pytest

from autune_intelligence.config import get_settings
from autune_intelligence.pipeline import get_gap_classifier, reset_cache
from autune_intelligence.pipeline.base import PATTERN_TYPES, Classification
from autune_intelligence.pipeline.classifier import FakeGapClassifier


@pytest.fixture(autouse=True)
def _reset():
    yield
    get_settings.cache_clear()
    reset_cache()


def test_classification_rejects_an_unknown_pattern_type() -> None:
    with pytest.raises(ValueError, match="unknown pattern_type"):
        Classification(pattern_type="not_a_real_type", confidence=0.9)


def test_classification_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValueError, match="confidence out of range"):
        Classification(pattern_type="schedule", confidence=1.5)


@pytest.mark.parametrize("label", [t for t in PATTERN_TYPES if t != "other"])
def test_fake_classifier_matches_a_canonical_label_verbatim(label: str) -> None:
    classifier = FakeGapClassifier()
    result = classifier.classify([f"{label} something"])
    assert result == [Classification(pattern_type=label, confidence=1.0)]


def test_fake_classifier_matches_korean_keywords_without_the_label_itself() -> None:
    classifier = FakeGapClassifier()
    result = classifier.classify(["담당자가 아직 지정되지 않았습니다"])
    assert result[0].pattern_type == "ownership"


def test_fake_classifier_falls_back_to_other() -> None:
    classifier = FakeGapClassifier()
    result = classifier.classify(["점심 메뉴를 못 정했습니다"])
    assert result == [Classification(pattern_type="other", confidence=0.0)]


def test_fake_classifier_preserves_order_and_handles_empty_input() -> None:
    classifier = FakeGapClassifier()
    assert classifier.classify([]) == []
    result = classifier.classify(["risk here", "budget there"])
    assert [c.pattern_type for c in result] == ["risk", "budget"]


def test_fake_classifier_model_version_is_stable() -> None:
    assert FakeGapClassifier().model_version == "fake"


def test_registry_returns_fake_for_fake_impl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    assert isinstance(get_gap_classifier(), FakeGapClassifier)


def test_registry_caches_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL", "fake")
    get_settings.cache_clear()
    reset_cache()
    assert get_gap_classifier() is get_gap_classifier()


def test_registry_rejects_an_unknown_impl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_INTELLIGENCE_GAP_CLASSIFIER_IMPL", "does_not_exist")
    get_settings.cache_clear()
    reset_cache()
    with pytest.raises(ValueError, match="GAP_CLASSIFIER_IMPL"):
        get_gap_classifier()
