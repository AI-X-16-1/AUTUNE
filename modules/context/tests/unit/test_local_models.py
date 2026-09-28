"""The in-process model wrappers, against fakes shaped like today's libraries.

Both bugs these pin went unnoticed because the output still looked right: a
score in ``[0, 1]`` either way, three probabilities either way. The fakes
mimic the library behaviour the wrappers depend on, so no weights -- and no
``local-models`` extra -- are needed to catch either one coming back.
"""

from __future__ import annotations

import math
import sys
import types
from typing import Any

import pytest

from autune_context.pipeline.nli import KlueKorNliLocal
from autune_context.pipeline.reranking import BgeRerankerKoLocal


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# --------------------------------------------------------------------------- #
# Re-ranker: one sigmoid, not two
# --------------------------------------------------------------------------- #


class _Identity:
    def __call__(self, x: float) -> float:
        return x


class _FakeCrossEncoder:
    """``sentence_transformers.CrossEncoder.predict`` as of 6.x: a one-label
    model applies its default sigmoid unless ``activation_fn`` replaces it."""

    def __init__(self, logits: list[float]) -> None:
        self._logits = logits
        self.activation_fns: list[Any] = []

    def predict(self, pairs: list[tuple[str, str]], activation_fn: Any = None) -> list[float]:
        assert len(pairs) == len(self._logits)
        self.activation_fns.append(activation_fn)
        apply = activation_fn if activation_fn is not None else _sigmoid
        return [apply(logit) for logit in self._logits]


@pytest.fixture
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``torch`` is in the optional ``local-models`` extra, which CI does not
    install; the wrapper only needs ``nn.Identity`` from it."""
    torch = types.ModuleType("torch")
    torch.nn = types.SimpleNamespace(Identity=_Identity)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)


def _reranker(model: _FakeCrossEncoder) -> BgeRerankerKoLocal:
    reranker = object.__new__(BgeRerankerKoLocal)  # skip loading weights
    reranker._model = model
    reranker._model_version = "fake"
    return reranker


@pytest.mark.usefixtures("fake_torch")
def test_reranker_applies_the_sigmoid_exactly_once() -> None:
    """A strongly negative logit: once-squashed it is near 0; squashed twice it
    would be ~0.5, which read as "somewhat relevant" to every threshold."""
    model = _FakeCrossEncoder([-13.9, 0.0, 4.0])

    scores = _reranker(model).score("질의", ["a", "b", "c"])

    assert scores == pytest.approx([_sigmoid(-13.9), 0.5, _sigmoid(4.0)])
    assert scores[0] < 1e-5  # a double sigmoid gives 0.5000008 here
    assert isinstance(model.activation_fns[0], _Identity)


@pytest.mark.usefixtures("fake_torch")
def test_reranker_with_no_passages_does_not_call_the_model() -> None:
    model = _FakeCrossEncoder([])

    assert _reranker(model).score("질의", []) == []
    assert model.activation_fns == []


# --------------------------------------------------------------------------- #
# NLI: a batch in, one record list per pair out
# --------------------------------------------------------------------------- #


class _FakeTextClassificationPipeline:
    """``transformers.pipeline("text-classification", top_k=None)`` as of 5.x:
    a list input returns one list of label records per item; a single dict
    input returns that one item's records unwrapped."""

    def __init__(self, scores: list[dict[str, float]]) -> None:
        self._scores = scores
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, inputs: Any, **kwargs: Any) -> Any:
        self.calls.append((inputs, kwargs))
        items = inputs if isinstance(inputs, list) else [inputs]
        out = [
            [{"label": label.upper(), "score": score} for label, score in scored.items()]
            for scored, _ in zip(self._scores, items, strict=True)
        ]
        return out if isinstance(inputs, list) else out[0]


def _nli(pipe: _FakeTextClassificationPipeline) -> KlueKorNliLocal:
    nli = object.__new__(KlueKorNliLocal)  # skip loading weights
    nli._pipe = pipe
    nli._model_version = "fake"
    return nli


def test_nli_reads_one_record_list_per_pair() -> None:
    pipe = _FakeTextClassificationPipeline(
        [
            {"entailment": 0.8, "contradiction": 0.1, "neutral": 0.1},
            {"entailment": 0.05, "contradiction": 0.9, "neutral": 0.05},
        ]
    )

    results = _nli(pipe).classify([("가", "가"), ("가", "나")])

    assert [r.label for r in results] == ["entailment", "contradiction"]
    assert results[0].entailment == pytest.approx(0.8)
    assert results[1].contradiction == pytest.approx(0.9)


def test_nli_sends_one_batch_without_token_type_ids() -> None:
    """``klue/roberta-base``'s BertTokenizer emits segment id 1, out of range
    for the RoBERTa encoder under it (see ``KlueKorNliLocal.classify``)."""
    pipe = _FakeTextClassificationPipeline(
        [{"entailment": 1.0, "contradiction": 0.0, "neutral": 0.0}] * 2
    )

    _nli(pipe).classify([("가", "나"), ("다", "라")])

    assert len(pipe.calls) == 1
    inputs, kwargs = pipe.calls[0]
    assert inputs == [{"text": "가", "text_pair": "나"}, {"text": "다", "text_pair": "라"}]
    assert kwargs["return_token_type_ids"] is False


def test_nli_with_a_single_pair_still_reads_its_records() -> None:
    """The case transformers 5 broke: one pair. A dict input came back
    unwrapped, and ``[0]`` then picked one record instead of the pair's list."""
    pipe = _FakeTextClassificationPipeline(
        [{"entailment": 0.1, "contradiction": 0.2, "neutral": 0.7}]
    )

    [result] = _nli(pipe).classify([("가", "나")])

    assert result.label == "neutral"
    assert result.neutral == pytest.approx(0.7)


def test_nli_with_no_pairs_does_not_call_the_pipeline() -> None:
    pipe = _FakeTextClassificationPipeline([])

    assert _nli(pipe).classify([]) == []
    assert pipe.calls == []
