"""``classifier_impl=llm_checked``: how the LLM's and DeBERTa's answers combine,
and when the registry refuses to build it. No network, no weights."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from autune_contracts.enums import UtteranceKind as K
from autune_extraction.config import ExtractionSettings
from autune_extraction.pipeline import registry
from autune_extraction.pipeline.base import Prediction
from autune_extraction.pipeline.checked import (
    AGREED_CONFIDENCE,
    UNCHECKED_CONFIDENCE,
    CheckedClassifier,
    combine,
)
from autune_extraction.pipeline.classifier import MODEL_VERSION_MAX, LocalDeberta
from autune_extraction.pipeline.llm import LlmClassifier


def said(kind: K | None, confidence: float = 0.9) -> Prediction:
    scores = dict.fromkeys(K, 0.0)
    if kind is not None:
        scores[kind] = confidence
    return Prediction(kind=kind, confidence=confidence, scores=scores, none_score=1.0 - confidence)


class Scripted:
    """A classifier that answers from a list, and records what it was asked."""

    def __init__(self, answers: list[K | None], version: str = "scripted") -> None:
        self.answers = answers
        self.version = version
        self.asked: list[list[str]] = []

    @property
    def model_version(self) -> str:
        return self.version

    def classify(self, texts: list[str]) -> list[Prediction]:
        self.asked.append(texts)
        return [said(k) for k in self.answers[: len(texts)]]


# --- routing ------------------------------------------------------------------


def test_a_commitment_both_models_found_is_asserted() -> None:
    got = combine(said(K.COMMITMENT), said(K.COMMITMENT, 0.97))
    assert got.kind is K.COMMITMENT
    assert got.confidence == AGREED_CONFIDENCE


def test_a_commitment_only_the_llm_found_is_kept_as_a_candidate() -> None:
    """Kept, not dropped: DeBERTa's recall on real speech is too low to veto."""
    got = combine(said(K.COMMITMENT), said(None, 0.99))
    assert got.kind is K.COMMITMENT
    assert got.confidence == UNCHECKED_CONFIDENCE
    assert got.runner_up == (None, 1.0 - UNCHECKED_CONFIDENCE)


@pytest.mark.parametrize("threshold", [0.51, 0.7, 0.9])
def test_any_threshold_in_the_band_separates_the_two(threshold: float) -> None:
    """``service.read_model`` marks ``confidence < candidate_confidence``."""

    def is_candidate(confidence: float) -> bool:
        return confidence < threshold

    assert is_candidate(UNCHECKED_CONFIDENCE)
    assert not is_candidate(AGREED_CONFIDENCE)


def test_a_commitment_only_deberta_found_is_the_llms_answer() -> None:
    llm_says = said(K.DECISION)
    assert combine(llm_says, said(K.COMMITMENT)) is llm_says
    llm_says_none = said(None)
    assert combine(llm_says_none, said(K.COMMITMENT)) is llm_says_none


@pytest.mark.parametrize("kind", [K.DECISION, K.OPEN_QUESTION, K.CONCERN, K.AMBIGUOUS, None])
def test_every_other_answer_is_the_llms_unchanged(kind: K | None) -> None:
    llm_says = said(kind)
    assert combine(llm_says, said(K.CONCERN)) is llm_says


def test_the_whole_meeting_is_combined_in_order() -> None:
    proposer = Scripted([K.COMMITMENT, K.COMMITMENT, None, K.DECISION])
    checker = Scripted([K.COMMITMENT, None, K.COMMITMENT, None])
    texts = ["제가 할게요", "그건 제가 볼게요", "네 알겠습니다", "B안으로 가죠"]

    got = CheckedClassifier(proposer, checker).classify(texts)

    assert [(p.kind, p.confidence) for p in got] == [
        (K.COMMITMENT, AGREED_CONFIDENCE),
        (K.COMMITMENT, UNCHECKED_CONFIDENCE),
        (None, 0.9),
        (K.DECISION, 0.9),
    ]
    assert proposer.asked == [texts]
    assert checker.asked == [texts]


def test_nothing_to_classify_asks_neither_model() -> None:
    proposer, checker = Scripted([]), Scripted([])
    assert CheckedClassifier(proposer, checker).classify([]) == []
    assert proposer.asked == checker.asked == []


def test_a_short_answer_from_either_model_is_refused() -> None:
    """Zipping a short list would pair an utterance with another one's check."""
    texts = ["하나", "둘"]
    with pytest.raises(ValueError, match="asked for 2"):
        CheckedClassifier(Scripted([K.COMMITMENT]), Scripted([None, None])).classify(texts)
    with pytest.raises(ValueError, match="asked for 2"):
        CheckedClassifier(Scripted([None, None]), Scripted([None])).classify(texts)


# --- model_version ------------------------------------------------------------


def test_the_version_names_both_models() -> None:
    c = CheckedClassifier(Scripted([], "llm:gemini-3.5-flash-lite"), Scripted([], "runs/ckpt"))
    assert c.model_version == "llm:gemini-3.5-flash-lite|checked:runs/ckpt"


def test_a_version_that_overruns_the_column_is_a_stable_digest() -> None:
    long = "runs/" + "x" * MODEL_VERSION_MAX

    def version(checker: str) -> str:
        return CheckedClassifier(Scripted([], "llm:m"), Scripted([], checker)).model_version

    assert len(version(long)) <= MODEL_VERSION_MAX
    assert version(long) == version(long)
    assert version(long) != version(long + "y")


# --- how it is chosen ---------------------------------------------------------


def settings(**overrides: str) -> ExtractionSettings:
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    def configure(**overrides: str) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: settings(**overrides))

    registry.get_classifier.cache_clear()
    yield configure
    registry.get_classifier.cache_clear()


def test_llm_checked_without_a_checkpoint_is_refused_by_name(configured) -> None:
    configured(classifier_impl="llm_checked", llm_api_key="k")
    with pytest.raises(ValueError, match="CLASSIFIER_CHECKPOINT"):
        registry.get_classifier()


def test_llm_checked_without_a_key_is_refused_by_name(configured) -> None:
    configured(
        classifier_impl="llm_checked",
        classifier_checkpoint="runs/ckpt",
        llm_api_key="",
        AUTUNE_LLM_API_KEY="",
    )
    with pytest.raises(ValueError, match="CLASSIFIER_IMPL=llm_checked needs .*LLM_API_KEY"):
        registry.get_classifier()


def test_llm_checked_is_the_llm_checked_by_local_deberta(configured) -> None:
    """Built without loading weights -- ``LocalDeberta`` loads on first use."""
    configured(
        classifier_impl="llm_checked",
        llm_api_key="k",
        llm_model="gemini-3.5-flash-lite",
        llm_fallback_model="",
        classifier_checkpoint="runs/ckpt",
    )
    chosen = registry.get_classifier()

    assert isinstance(chosen, CheckedClassifier)
    assert isinstance(chosen._proposer, LlmClassifier)
    assert isinstance(chosen._checker, LocalDeberta)
    assert chosen.model_version == "llm:gemini-3.5-flash-lite|checked:runs/ckpt"


def test_llm_checked_is_not_a_resolver() -> None:
    assert "llm_checked" not in registry._RESOLVERS
