"""The classifier seam: what a prediction promises, and what has no implementation.

No weights, no network, no corpus. The model is #10's other half; this is the
shape everything downstream is built against, and it is testable now.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator

import pytest
from pydantic import ValidationError

from autune_contracts.enums import UtteranceKind
from autune_extraction.config import ExtractionSettings
from autune_extraction.labels import NONE
from autune_extraction.pipeline import FakeClassifier, Prediction, registry
from autune_extraction.pipeline import classifier as classifier_module
from autune_extraction.pipeline.classifier import (
    HEAD,
    LABELS,
    RETRY_BACKOFF_SEC,
    HostedDeberta,
    LocalDeberta,
    _batches_within_budget,
    _check_label_order,
    _to_prediction,
    _truncation_length,
)
from autune_extraction.pipeline.registry import _CLASSIFIERS
from autune_extraction.training.dataset import LABELS as TRAINING_LABELS
from autune_integrations.errors import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, check_outbound

K = UtteranceKind


# --- what a prediction means ------------------------------------------------


def test_confidence_is_the_probability_of_the_chosen_kind() -> None:
    """ADR 0006 compares this against a threshold, so it has to be the label's
    own probability rather than its margin over the next one."""
    scores = [0.7, 0.1, 0.1, 0.05, 0.05, 0.0]
    prediction = _to_prediction(scores)

    assert prediction.kind is LABELS[0]
    assert prediction.confidence == pytest.approx(0.7)
    assert prediction.scores[LABELS[0]] == pytest.approx(0.7)


def test_the_full_distribution_survives() -> None:
    """#64's threshold work needs what the model nearly said.

    A caller holding only the maximum cannot recover it, and 0.51 against 0.49 is
    a different situation from 0.51 against nothing.
    """
    prediction = _to_prediction([0.51, 0.49, 0.0, 0.0, 0.0, 0.0])

    assert prediction.runner_up == (LABELS[1], pytest.approx(0.49))


def test_a_confident_prediction_has_a_distant_runner_up() -> None:
    prediction = _to_prediction([0.96, 0.01, 0.01, 0.01, 0.01, 0.0])
    _, second = prediction.runner_up

    assert prediction.confidence - second > 0.9


def test_a_distribution_that_does_not_sum_to_one_raises() -> None:
    """A head or a label order that is wrong shows up here.

    Rescaling it quietly would hide the defect behind confidences that look
    reasonable, and every downstream threshold would read them as real.
    """
    with pytest.raises(ValueError, match="do not sum to 1"):
        _to_prediction([0.5, 0.2, 0.1, 0.05, 0.05, 0.0])


def test_the_wrong_number_of_scores_raises() -> None:
    with pytest.raises(ValueError, match="expected 6 scores"):
        _to_prediction([0.5, 0.5])


def test_none_is_an_answer_and_not_a_kind() -> None:
    """Most of a meeting is none of the kinds (#149).

    ``kind is None`` rather than a sixth enum member: the contract has five
    kinds, and a ``Classification`` built from this has to fail to type-check,
    not quietly carry a label no other module has heard of.
    """
    prediction = _to_prediction([0.1, 0.05, 0.05, 0.05, 0.05, 0.7])

    assert prediction.kind is None
    assert prediction.confidence == pytest.approx(0.7)
    assert prediction.none_score == pytest.approx(0.7)
    assert NONE not in {kind.value for kind in prediction.scores}


def test_a_kind_that_narrowly_lost_to_none_is_the_runner_up() -> None:
    """The same "what it nearly said" as between two kinds, from the other side."""
    prediction = _to_prediction([0.0, 0.45, 0.0, 0.0, 0.0, 0.55])

    assert prediction.kind is None
    assert prediction.runner_up == (K.DECISION, pytest.approx(0.45))


def test_none_can_be_the_runner_up() -> None:
    prediction = _to_prediction([0.6, 0.0, 0.0, 0.0, 0.0, 0.4])

    assert prediction.kind is K.COMMITMENT
    assert prediction.runner_up == (None, pytest.approx(0.4))


def test_confidence_outside_zero_to_one_raises() -> None:
    with pytest.raises(ValueError, match="confidence out of range"):
        Prediction(kind=K.COMMITMENT, confidence=1.4, scores=dict.fromkeys(LABELS, 0.2))


# --- the label order is part of the checkpoint ------------------------------


def test_label_order_matches_the_contract_enum() -> None:
    """Two independent statements of the order, so they can disagree.

    ``LABELS`` used to be ``tuple(UtteranceKind)``, which made this ``x == x``:
    reordering the enum moved both sides together, the checkpoint's fixed head
    columns were mapped onto the new order, and every prediction came back
    mislabelled with the suite still green.
    """
    """The fine-tuned head's columns are in this order, and the order ships with
    the weights. Reordering UtteranceKind without retraining would relabel every
    prediction silently, so the coupling is asserted rather than assumed."""
    assert tuple(UtteranceKind) == LABELS
    assert len(LABELS) == 5


def test_the_head_is_the_five_kinds_then_none() -> None:
    """``none`` is appended, so the five kinds keep the columns they had (#149)."""
    assert HEAD[:5] == tuple(kind.value for kind in LABELS)
    assert HEAD[5:] == (NONE,)


def test_training_writes_the_head_inference_reads() -> None:
    """Two literals in two packages, and a checkpoint is only readable when they
    agree: the training loop writes its order into ``id2label`` and
    ``_check_label_order`` compares it with ``HEAD``."""
    assert TRAINING_LABELS == HEAD


def test_a_checkpoint_in_label_order_is_accepted() -> None:
    """Integer keys as transformers holds them, string keys as ``config.json``
    stores them. The training loop writes this exact mapping."""
    _check_label_order(dict(enumerate(HEAD)))
    _check_label_order({str(i): label for i, label in enumerate(HEAD)})


def test_a_checkpoint_in_another_order_is_refused() -> None:
    """Loading it would relabel every prediction behind plausible confidences."""
    swapped = list(HEAD)
    swapped[0], swapped[1] = swapped[1], swapped[0]

    with pytest.raises(RuntimeError, match="labelled"):
        _check_label_order(dict(enumerate(swapped)))


def test_a_bare_encoder_is_refused() -> None:
    """An untrained head is labelled ``LABEL_0``... and would load without
    complaint, classifying at random."""
    with pytest.raises(RuntimeError, match="labelled"):
        _check_label_order({i: f"LABEL_{i}" for i in range(len(HEAD))})


def test_a_head_with_an_extra_column_is_refused() -> None:
    """Six matching names are not enough if a seventh column exists: the softmax
    would run over seven and ``_to_prediction`` would refuse every row mid-meeting."""
    extra = dict(enumerate(HEAD)) | {len(HEAD): "other"}

    with pytest.raises(RuntimeError, match="labelled"):
        _check_label_order(extra)


def test_a_five_column_checkpoint_from_before_none_is_refused() -> None:
    """Its softmax has nowhere to put "none of these", so every utterance of a
    meeting would come back as a kind -- the defect #149 measured."""
    with pytest.raises(RuntimeError, match="labelled"):
        _check_label_order({i: kind.value for i, kind in enumerate(LABELS)})


def test_inference_truncates_where_the_checkpoint_was_trained() -> None:
    """The training loop saves its length as ``model_max_length``."""
    assert _truncation_length(96, 512) == 96


def test_a_tokenizer_without_a_length_falls_back_to_the_positions() -> None:
    """transformers reports ``int(1e30)`` for a length nobody set."""
    assert _truncation_length(int(1e30), 512) == 512


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


def test_the_fake_marks_weak_assent_as_ambiguous() -> None:
    """Weak assent is the case module B exists to be careful about."""
    assert FakeClassifier().classify(["한번 볼게요"])[0].kind is K.AMBIGUOUS


def test_the_fake_says_none_when_nothing_matches() -> None:
    """Most of a meeting is none of the kinds.

    It used to fall back to ``ambiguous``, and ``ambiguous`` is what sends the
    speaker a DM -- a fake that asked about every unmatched utterance made every
    downstream fixture look like a meeting full of questions.
    """
    assert FakeClassifier().classify(["네 알겠어요 다음 안건으로"])[0].kind is None


def test_the_fake_produces_a_real_distribution() -> None:
    """It stands in for the model everywhere downstream, so its output has to
    satisfy the same invariants — otherwise tests pass on values the real model
    could never produce."""
    for prediction in FakeClassifier().classify(["제가 하겠습니다", "무슨 말인지"]):
        assert sum(prediction.scores.values()) + prediction.none_score == pytest.approx(1.0)
        assert set(prediction.scores) == set(LABELS)
        assert 0.0 <= prediction.confidence <= 1.0


def test_an_empty_batch_is_not_an_error() -> None:
    """A meeting can have nothing left after filtering, and that is not a failure."""
    assert FakeClassifier().classify([]) == []


def test_the_fake_names_itself() -> None:
    """model_version is recorded with every row; a score that cannot be
    attributed to a version cannot be compared against the next one."""
    assert FakeClassifier().model_version == "fake"


# --- the hosted classifier fits the outbound guard ---------------------------


def utterances(count: int, length: int = 37) -> list[str]:
    """A meeting's worth of ordinary masked utterances."""
    return ["a" * length] * count


def test_a_meetings_worth_of_utterances_passes_the_outbound_guard() -> None:
    """The guard caps a request at 4,000 characters and a meeting is far over it.

    ``check_outbound`` sums every string in the body, and its message says "send
    what the feature needs, not the whole meeting". But the whole meeting is what
    this feature needs — it classifies every utterance — so the request has to be
    split rather than the cap widened.

    Sent as one request this raised ``PrivacyViolationError`` at 109 utterances,
    which is a couple of minutes of talk.
    """
    for batch in _batches_within_budget(utterances(3000), MAX_OUTBOUND_CHARS):
        check_outbound({"texts": batch}, destination="extraction-classifier")


def test_no_batch_exceeds_the_cap() -> None:
    batches = list(_batches_within_budget(utterances(3000), MAX_OUTBOUND_CHARS))

    assert len(batches) > 1, "a meeting has to be split"
    assert all(sum(len(t) for t in b) <= MAX_OUTBOUND_CHARS for b in batches)


def test_every_utterance_is_sent_exactly_once_and_in_order() -> None:
    """Order is the contract: callers zip predictions against their own ids."""
    texts = [f"{i:04d}" + "a" * 33 for i in range(500)]

    rejoined = [t for batch in _batches_within_budget(texts, MAX_OUTBOUND_CHARS) for t in batch]

    assert rejoined == texts


def test_the_budget_is_read_from_the_guard_not_copied() -> None:
    """A local 4000 would drift the day the shared cap changes."""
    small = list(_batches_within_budget(utterances(10), budget=100))

    assert all(sum(len(t) for t in b) <= 100 for b in small)
    assert len(small) == 5  # 37 + 37 = 74 fits, 111 does not, so two per request


def test_an_utterance_larger_than_the_whole_budget_is_refused() -> None:
    """Not truncated. A shortened utterance would be classified as something the
    meeting did not say, and the label would read as a model error."""
    with pytest.raises(ValueError, match="over the .* outbound limit"):
        list(_batches_within_budget(["x" * (MAX_OUTBOUND_CHARS + 1)], MAX_OUTBOUND_CHARS))


def test_an_empty_meeting_produces_no_requests() -> None:
    assert list(_batches_within_budget([], MAX_OUTBOUND_CHARS)) == []


# --- the fake keys on endings, not on one verb -------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("제가 정리해서 공유드리겠습니다", K.COMMITMENT),
        ("내일까지 보내겠습니다", K.COMMITMENT),
        ("제가 확인하겠습니다", K.COMMITMENT),
        ("그럼 인기순으로 가기로 했습니다", K.DECISION),
        ("이걸로 하기로 했습니다", K.DECISION),
        ("이거 지금 되나요", K.OPEN_QUESTION),
        ("일정이 좀 어렵습니다", K.CONCERN),
    ],
)
def test_the_fake_reads_the_ending_and_not_the_verb(text: str, expected: K) -> None:
    """An earlier version matched ``하겠습니다`` and ``하기로 했``.

    Those carry the verb stem 하-, so they only fire when the verb happens to be
    하다: three of these five landed on ``ambiguous``. The marker is ``-겠-`` and
    ``-기로 하-``; what precedes it is the verb, not the class.

    It matters because ``ambiguous`` is what triggers a confirmation DM. A fake
    that calls every ordinary commitment ambiguous makes every downstream fixture
    look like it needs to ask the speaker something.
    """
    assert FakeClassifier().classify([text])[0].kind is expected


def test_a_reported_decision_is_not_a_fresh_promise() -> None:
    """``기로 했`` is checked before ``겠습니다``; both endings can co-occur."""
    assert FakeClassifier().classify(["그렇게 하기로 했겠습니다"])[0].kind is K.DECISION


# --- the device is chosen, not discovered -----------------------------------


def settings(**overrides: str) -> ExtractionSettings:
    """Settings built from the declared defaults and nothing else.

    ``model_config`` carries ``env_file=".env"``, so a plain ``ExtractionSettings()``
    reads whatever the developer running the suite happens to have exported. A
    test that asserts a *default* has to be told not to — otherwise it fails for
    somebody with ``AUTUNE_EXTRACTION_CLASSIFIER_DEVICE=cuda`` in their shell,
    and the parametrised ``cuda`` case passes for them without testing anything.
    """
    return ExtractionSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_the_default_device_is_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not "whatever this machine has".

    A worker that picks up a GPU because it happened to land on one has a
    throughput that changes when it is rescheduled, and a latency measured on
    one scheduling says nothing about the other.
    """
    monkeypatch.delenv("AUTUNE_EXTRACTION_CLASSIFIER_DEVICE", raising=False)

    assert settings().classifier_device == "cpu"


@pytest.mark.parametrize("value", ["gpu", "CUDA", "cuda:0", "mps"])
def test_a_device_name_we_do_not_handle_is_refused_at_startup(value: str) -> None:
    """A typo should surface when the worker boots, not mid-meeting as a CUDA
    error naming a tensor."""
    with pytest.raises(ValidationError):
        settings(classifier_device=value)


@pytest.mark.parametrize("value", ["cpu", "cuda"])
def test_both_devices_we_handle_are_accepted(value: str) -> None:
    assert settings(classifier_device=value).classifier_device == value


# --- there is no checkpoint to default to ------------------------------------


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Point the registry at chosen settings, and forget the cached classifier
    on both sides so no test sees another's."""

    def configure(**overrides: str) -> None:
        monkeypatch.setattr(registry, "get_settings", lambda: settings(**overrides))

    registry.get_classifier.cache_clear()
    yield configure
    registry.get_classifier.cache_clear()


def test_the_default_checkpoint_is_blank() -> None:
    """The name it used to default to was never published. A default that cannot
    load is found by the first meeting a worker picks up."""
    assert settings().classifier_checkpoint == ""


@pytest.mark.parametrize("impl", ["local", "hosted"])
def test_a_real_classifier_without_a_checkpoint_is_refused(
    configured: Callable[..., None], impl: str
) -> None:
    """Named in the registry, rather than a hub error from the first forward pass
    -- or, for ``hosted``, classifications stored with no model version."""
    configured(classifier_impl=impl, classifier_endpoint="https://classifier.internal")

    with pytest.raises(ValueError, match="AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT"):
        registry.get_classifier()


def test_the_fake_needs_no_checkpoint(configured: Callable[..., None]) -> None:
    configured(classifier_impl="fake")

    assert isinstance(registry.get_classifier(), FakeClassifier)


def test_a_checkpoint_is_enough_to_build_the_local_classifier(
    configured: Callable[..., None],
) -> None:
    """Construction loads nothing -- the weights come on the first
    classification -- so this runs without the extra."""
    configured(classifier_impl="local", classifier_checkpoint="runs/kf-deberta-v1")

    classifier = registry.get_classifier()

    assert isinstance(classifier, LocalDeberta)
    assert classifier.model_version == "runs/kf-deberta-v1"


def test_asking_for_cuda_without_it_fails_before_the_model_loads() -> None:
    """A build ending in ``+cpu`` has no CUDA support whatever the machine has,
    which a version number does not say.

    Raised from ``_load`` rather than left to the first forward pass: by then a
    meeting is already being processed, and the error names a tensor.

    Needs the whole ``local-models`` stack, not just torch. CI installs neither,
    and ``_load`` reports the missing extra before it can look at a device — so a
    version of this that only skipped on torch passed here and failed there.
    """
    pytest.importorskip("transformers")
    torch = pytest.importorskip("torch")
    if torch.cuda.is_available():
        pytest.skip("this box has CUDA; the guard cannot fire")

    with pytest.raises(RuntimeError, match="no CUDA device"):
        LocalDeberta("kakaobank/kf-deberta-base", device="cuda")._load()


def test_a_missing_extra_is_reported_even_when_a_gpu_was_asked_for() -> None:
    """Without ``transformers`` there is no torch to ask about a device, so the
    extra has to be named first — otherwise the reader is told to check a GPU
    they were never going to reach.

    This is the case CI runs: it installs the workspace but not the extra.
    """
    if importlib.util.find_spec("transformers") is not None:
        pytest.skip("the extra is installed here; the missing-extra path cannot fire")

    with pytest.raises(RuntimeError, match="local-models"):
        LocalDeberta("kakaobank/kf-deberta-base", device="cuda")._load()


# --- one bad second does not cost the meeting (#113) --------------------------


class Server:
    """The inference server, as a client that can be told to fail first.

    ``failures`` are raised in order, one per call, before any answer is given.
    """

    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.calls: list[int] = []

    def request(self, method: str, path: str, *, json: dict) -> dict:
        self.calls.append(len(json["texts"]))
        if self.failures:
            raise self.failures.pop(0)
        return {"scores": [[0.0, 0.0, 0.0, 0.0, 0.0, 1.0]] * len(json["texts"])}


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def hosted(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> Callable[[Server], HostedDeberta]:
    """A hosted classifier whose waits are recorded instead of slept."""

    def build(server: Server) -> HostedDeberta:
        classifier = HostedDeberta("http://inference.invalid", "ckpt")
        classifier._client = server  # type: ignore[assignment]
        return classifier

    monkeypatch.setattr(classifier_module.time, "sleep", slept.append)
    return build


def test_a_transient_failure_is_re_attempted_rather_than_failing_the_meeting(
    hosted: Callable[[Server], HostedDeberta],
) -> None:
    """The fifteenth request of twenty-eight gets a 502 and the meeting survives.

    Without this the whole meeting fails and Celery's retry starts again at the
    first request -- so the longer the recording, the likelier it is never
    classified at all.
    """
    server = Server(TransientIntegrationError("extraction-classifier returned 502"))

    predictions = hosted(server).classify(["네"])

    assert len(predictions) == 1
    assert server.calls == [1, 1], "the same batch, sent again"


def test_a_re_attempt_waits_and_gives_up_after_a_bounded_number(
    hosted: Callable[[Server], HostedDeberta], slept: list[float]
) -> None:
    """A server that is really down fails the task rather than holding a worker.

    The thread sleeps through every wait, so the bound is what keeps one sick
    server from stopping everything else that worker would have run.
    """
    server = Server(*[TransientIntegrationError("timed out") for _ in range(5)])

    with pytest.raises(TransientIntegrationError):
        hosted(server).classify(["네"])

    assert len(server.calls) == len(RETRY_BACKOFF_SEC) + 1
    assert slept == list(RETRY_BACKOFF_SEC)


def test_a_rejected_request_is_not_sent_again(
    hosted: Callable[[Server], HostedDeberta], slept: list[float]
) -> None:
    """A 4xx is the request being wrong. Sending it twice more says so twice more."""
    server = Server(PermanentIntegrationError("extraction-classifier rejected the request"))

    with pytest.raises(PermanentIntegrationError):
        hosted(server).classify(["네"])

    assert len(server.calls) == 1
    assert slept == []


def test_a_meeting_is_still_one_request_per_batch_when_nothing_fails(
    hosted: Callable[[Server], HostedDeberta], slept: list[float]
) -> None:
    """The retry must not add a request to the path where nothing went wrong."""
    texts = utterances(300)
    server = Server()

    predictions = hosted(server).classify(texts)

    assert len(predictions) == len(texts)
    assert len(server.calls) == len(list(_batches_within_budget(texts, MAX_OUTBOUND_CHARS)))
    assert slept == []
