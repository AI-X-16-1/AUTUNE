"""The classifier seam: what a prediction promises, and what has no implementation.

No weights, no network, no corpus. The model is #10's other half; this is the
shape everything downstream is built against, and it is testable now.
"""

from __future__ import annotations

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.pipeline import FakeClassifier, Prediction
from autune_extraction.pipeline.classifier import LABELS, _to_prediction
from autune_extraction.pipeline.registry import _CLASSIFIERS

K = UtteranceKind


# --- what a prediction means ------------------------------------------------


def test_confidence_is_the_probability_of_the_chosen_kind() -> None:
    """ADR 0006 compares this against a threshold, so it has to be the label's
    own probability rather than its margin over the next one."""
    scores = [0.7, 0.1, 0.1, 0.05, 0.05]
    prediction = _to_prediction(scores)

    assert prediction.kind is LABELS[0]
    assert prediction.confidence == pytest.approx(0.7)
    assert prediction.scores[LABELS[0]] == pytest.approx(0.7)


def test_the_full_distribution_survives() -> None:
    """#64's threshold work needs what the model nearly said.

    A caller holding only the maximum cannot recover it, and 0.51 against 0.49 is
    a different situation from 0.51 against nothing.
    """
    prediction = _to_prediction([0.51, 0.49, 0.0, 0.0, 0.0])

    assert prediction.runner_up == (LABELS[1], pytest.approx(0.49))


def test_a_confident_prediction_has_a_distant_runner_up() -> None:
    prediction = _to_prediction([0.96, 0.01, 0.01, 0.01, 0.01])
    _, second = prediction.runner_up

    assert prediction.confidence - second > 0.9


def test_a_distribution_that_does_not_sum_to_one_raises() -> None:
    """A head or a label order that is wrong shows up here.

    Rescaling it quietly would hide the defect behind confidences that look
    reasonable, and every downstream threshold would read them as real.
    """
    with pytest.raises(ValueError, match="do not sum to 1"):
        _to_prediction([0.5, 0.2, 0.1, 0.05, 0.05])


def test_the_wrong_number_of_scores_raises() -> None:
    with pytest.raises(ValueError, match="expected 5 scores"):
        _to_prediction([0.5, 0.5])


def test_confidence_outside_zero_to_one_raises() -> None:
    with pytest.raises(ValueError, match="confidence out of range"):
        Prediction(kind=K.COMMITMENT, confidence=1.4, scores=dict.fromkeys(LABELS, 0.2))


# --- the label order is part of the checkpoint ------------------------------


def test_label_order_matches_the_contract_enum() -> None:
    """The fine-tuned head's columns are in this order, and the order ships with
    the weights. Reordering UtteranceKind without retraining would relabel every
    prediction silently, so the coupling is asserted rather than assumed."""
    assert tuple(UtteranceKind) == LABELS
    assert len(LABELS) == 5


# --- what has deliberately no implementation --------------------------------


def test_there_is_no_external_classifier() -> None:
    """Sending a meeting's utterances to somebody else's model is a privacy
    decision, not a config string. privacy.md section 6 bounds what may leave.

    Asserted as the whole key set so adding one fails here, where the reason is
    written down, rather than passing as an ordinary feature.
    """
    assert set(_CLASSIFIERS) == {"local", "hosted", "fake"}


# --- the fake, which everything downstream is built on ----------------------


def test_the_fake_returns_one_prediction_per_input_in_order() -> None:
    """Order is the contract: callers zip against their own utterance ids."""
    texts = ["제가 하겠습니다", "그건 좀 어렵습니다", "언제까지 해야 하나요"]
    predictions = FakeClassifier().classify(texts)

    assert [p.kind for p in predictions] == [K.COMMITMENT, K.CONCERN, K.OPEN_QUESTION]


def test_the_fake_falls_back_to_ambiguous() -> None:
    """Weak assent is the case module B exists to be careful about."""
    assert FakeClassifier().classify(["한번 볼게요"])[0].kind is K.AMBIGUOUS


def test_the_fake_produces_a_real_distribution() -> None:
    """It stands in for the model everywhere downstream, so its output has to
    satisfy the same invariants — otherwise tests pass on values the real model
    could never produce."""
    for prediction in FakeClassifier().classify(["제가 하겠습니다", "무슨 말인지"]):
        assert sum(prediction.scores.values()) == pytest.approx(1.0)
        assert set(prediction.scores) == set(LABELS)
        assert 0.0 <= prediction.confidence <= 1.0


def test_an_empty_batch_is_not_an_error() -> None:
    """A meeting can have nothing left after filtering, and that is not a failure."""
    assert FakeClassifier().classify([]) == []


def test_the_fake_names_itself() -> None:
    """model_version is recorded with every row; a score that cannot be
    attributed to a version cannot be compared against the next one."""
    assert FakeClassifier().model_version == "fake"
