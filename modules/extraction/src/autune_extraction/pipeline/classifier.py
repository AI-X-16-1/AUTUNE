"""The five-way classifier: in-process weights, our own inference server, a fake.

Three implementations and deliberately no fourth. There is no external-API option
because sending a meeting's utterances to somebody else's classifier is a decision
about where personal data goes, and `privacy.md` section 6 makes that a design
conversation rather than a value of ``AUTUNE_EXTRACTION_CLASSIFIER_IMPL``.

``torch`` and ``transformers`` are imported inside the class that needs them.
Importing at module scope would make ``apps/api`` load a deep-learning stack to
serve a health check, and would make this module's unit tests need one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from autune_contracts.enums import UtteranceKind
from autune_core import get_logger
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

from .base import Prediction

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

LABELS: tuple[UtteranceKind, ...] = (
    UtteranceKind.COMMITMENT,
    UtteranceKind.DECISION,
    UtteranceKind.OPEN_QUESTION,
    UtteranceKind.CONCERN,
    UtteranceKind.AMBIGUOUS,
)
"""Label order. The fine-tuned head's output columns are in this order and the
order is part of the checkpoint — reordering ``UtteranceKind`` without retraining
would silently relabel every prediction.

Written out rather than ``tuple(UtteranceKind)``. Deriving it made the test that
was supposed to catch a divergence read ``x == x``: ``LABELS`` followed any
reordering of the enum, ``_to_prediction`` mapped the checkpoint's fixed columns
onto the new order, every prediction came back mislabelled, and the suite stayed
green. A literal is the only version of this that can disagree with the contract.
"""


def _to_prediction(scores: list[float]) -> Prediction:
    """One row of probabilities into a Prediction.

    Raises rather than normalising a row that does not sum to one: a distribution
    that is off means the head or the label order is wrong, and quietly rescaling
    it would hide that behind plausible-looking confidences.
    """
    if len(scores) != len(LABELS):
        raise ValueError(f"expected {len(LABELS)} scores, got {len(scores)}")
    total = sum(scores)
    if not 0.99 <= total <= 1.01:
        raise ValueError(f"scores do not sum to 1 (got {total:.4f})")

    distribution = dict(zip(LABELS, scores, strict=True))
    kind = max(distribution, key=lambda k: distribution[k])
    return Prediction(kind=kind, confidence=distribution[kind], scores=distribution)


def _classifier_client(endpoint: str) -> Any:
    """Our inference server, as a client the way every other one is written.

    A subclass rather than an instance whose ``service`` is reassigned after
    construction: ``JiraClient``, ``NotionClient``, ``SlackClient`` and
    ``CalendarClient`` are all ``class X(HttpClient): service = "x"``, and
    ``addressing`` can only be declared the standard way on a class.

    ``addressing`` stays empty. Every string in this body is an utterance, so
    there is nothing here that addresses the request rather than carrying
    meeting content — and declaring nothing is what fails closed.
    """
    from autune_integrations.base import HttpClient  # noqa: PLC0415

    class ClassifierClient(HttpClient):
        service = "extraction-classifier"

    return ClassifierClient(endpoint)


class LocalDeberta:
    """Weights in this process. What a worker uses.

    Loaded once per process by the registry, not per task: the checkpoint is
    hundreds of megabytes and a per-call load would dominate the pipeline.

    ``device`` is taken rather than detected. A worker that picks whatever
    hardware it lands on has a throughput that changes when it is rescheduled,
    and a latency measured on one scheduling is not a measurement of the other.
    """

    def __init__(self, checkpoint: str, *, device: str = "cpu", batch_size: int = 32) -> None:
        self._checkpoint = checkpoint
        self._device = device
        self._batch_size = batch_size
        self._model: Any = None
        self._tokenizer: Any = None

    @property
    def model_version(self) -> str:
        return self._checkpoint

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: PLC0415
            from transformers import (  # noqa: PLC0415
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            # Named rather than left as a bare ImportError because this is the
            # default implementation: the first person to run a worker hits it,
            # and "No module named 'transformers'" does not say that an extra
            # exists or what it is called.
            raise RuntimeError(
                "the local classifier needs the 'local-models' extra: "
                "uv sync --package autune-extraction --extra local-models"
            ) from exc

        self._torch = torch
        if self._device == "cuda" and not torch.cuda.is_available():
            # Said here rather than left to a CUDA error inside the first forward
            # pass, which arrives mid-meeting and names a tensor. A build ending
            # in "+cpu" has no CUDA support whatever the machine has, which is
            # not obvious from a version number.
            raise RuntimeError(
                "AUTUNE_EXTRACTION_CLASSIFIER_DEVICE=cuda but torch reports no CUDA "
                f"device (torch {torch.__version__}). See "
                "docs/engineering/environments.md."
            )

        self._tokenizer = AutoTokenizer.from_pretrained(self._checkpoint)
        self._model = AutoModelForSequenceClassification.from_pretrained(self._checkpoint)
        self._model.to(self._device)
        self._model.eval()
        log.info("extraction_classifier_loaded", checkpoint=self._checkpoint, device=self._device)

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        self._load()
        torch = self._torch

        predictions: list[Prediction] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            encoded = self._tokenizer(
                batch, padding=True, truncation=True, max_length=256, return_tensors="pt"
            ).to(self._device)
            with torch.no_grad():
                logits = self._model(**encoded).logits
            for row in torch.softmax(logits, dim=-1).tolist():
                predictions.append(_to_prediction(row))
        return predictions


def _batches_within_budget(texts: list[str], budget: int) -> Iterator[list[str]]:
    """Split so no one request carries more than the outbound guard allows.

    ``check_outbound`` sums every string in the body, so the budget is exactly
    the total characters of a batch — ``strings_in`` collects values and not
    keys, which is why this can count rather than guess at a margin.

    A single utterance over the whole budget is refused instead of truncated.
    Truncating would classify something the meeting did not say, and the label
    would then look like a model error rather than a transport one.
    """
    batch: list[str] = []
    size = 0
    for text in texts:
        if len(text) > budget:
            raise ValueError(
                f"one utterance is {len(text)} characters, over the {budget}-character "
                "outbound limit; it cannot be sent to the inference server"
            )
        if batch and size + len(text) > budget:
            yield batch
            batch, size = [], 0
        batch.append(text)
        size += len(text)
    if batch:
        yield batch


class HostedDeberta:
    """The same model on our own inference server.

    Goes through ``autune_integrations.HttpClient`` rather than ``httpx``
    directly, so the outbound guard runs on the request body. The endpoint is
    ours and utterances are masked before they are ever stored, but "it is our
    server" is the reasoning that leaves a guard unrun, and the guard costs one
    call.

    **Requests are split to fit that guard.** ``MAX_OUTBOUND_CHARS`` caps an
    outbound body at 4,000 characters and its error says "send what the feature
    needs, not the whole meeting" — but the whole meeting is exactly what this
    feature needs, since it classifies every utterance. Sending one request per
    meeting raised ``PrivacyViolationError`` at 109 utterances of ordinary
    length, which is a couple of minutes of talk. The cap is right and the batch
    was wrong: it is aimed at the integrations that carry meeting content
    outward, and the fix is to fit it rather than to widen it for our own host.
    """

    def __init__(self, endpoint: str, model_version: str) -> None:
        self._client = _classifier_client(endpoint)
        self._model_version = model_version

    @property
    def model_version(self) -> str:
        return self._model_version

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []

        predictions: list[Prediction] = []
        for batch in _batches_within_budget(texts, MAX_OUTBOUND_CHARS):
            body = self._client.request("POST", "/classify", json={"texts": batch})
            # A server answering with a bare array is wrong but comprehensible;
            # letting it surface as AttributeError on a dict method is not.
            rows = body.get("scores", []) if isinstance(body, dict) else body
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ValueError(
                    f"asked for {len(batch)} predictions, got {type(rows).__name__} "
                    f"of length {len(rows) if isinstance(rows, list) else 'n/a'}"
                )
            predictions.extend(_to_prediction(row) for row in rows)
        return predictions


class FakeClassifier:
    """Deterministic, no weights, no network. What the tests run.

    Keys on Korean sentence endings because that is where the class is marked —
    the same observation the pipeline order rests on. It is not an approximation
    of the model's accuracy and must not be used to estimate it; it exists so
    everything downstream of classification can be built and tested before the
    model is trained.

    **The endings carry no verb stem.** An earlier version matched "하겠습니다"
    and "하기로 했", which only fire when the verb happens to be 하다:
    "공유드리겠습니다" and "가기로 했습니다" both fell through to ``ambiguous``.
    The marker is "-겠-" and "-기로 하-"; the stem in front of it is the verb,
    not the class.

    Deliberately conservative otherwise. A miss lands on ``ambiguous``, which
    downstream means *ask the speaker* — so under-matching costs a question and
    over-matching invents a commitment nobody made.
    """

    model_version = "fake"

    _ENDINGS: tuple[tuple[str, UtteranceKind], ...] = (
        ("기로 했", UtteranceKind.DECISION),
        ("겠습니다", UtteranceKind.COMMITMENT),
        ("할게요", UtteranceKind.COMMITMENT),
        ("갑니다", UtteranceKind.DECISION),
        ("까요", UtteranceKind.OPEN_QUESTION),
        ("나요", UtteranceKind.OPEN_QUESTION),
        ("어렵", UtteranceKind.CONCERN),
        ("걱정", UtteranceKind.CONCERN),
    )
    """Checked in order, first match wins. ``기로 했`` comes before ``겠습니다``
    because "하기로 했겠습니다" is a decision being reported, not a new promise."""

    def classify(self, texts: list[str]) -> list[Prediction]:
        predictions = []
        for text in texts:
            kind = UtteranceKind.AMBIGUOUS
            for ending, candidate in self._ENDINGS:
                if ending in text:
                    kind = candidate
                    break
            scores = {k: 0.05 for k in LABELS}
            scores[kind] = 1.0 - 0.05 * (len(LABELS) - 1)
            predictions.append(Prediction(kind=kind, confidence=scores[kind], scores=scores))
        return predictions
