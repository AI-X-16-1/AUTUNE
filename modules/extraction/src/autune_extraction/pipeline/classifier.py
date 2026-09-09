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
    from autune_integrations.base import HttpClient

log = get_logger(__name__)

LABELS: tuple[UtteranceKind, ...] = tuple(UtteranceKind)
"""Label order. The fine-tuned head's output columns are in this order and the
order is part of the checkpoint — reordering ``UtteranceKind`` without retraining
would silently relabel every prediction."""


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


class LocalDeberta:
    """Weights in this process. What a worker uses.

    Loaded once per process by the registry, not per task: the checkpoint is
    hundreds of megabytes and a per-call load would dominate the pipeline.
    """

    def __init__(self, checkpoint: str, *, batch_size: int = 32) -> None:
        self._checkpoint = checkpoint
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
        self._tokenizer = AutoTokenizer.from_pretrained(self._checkpoint)
        self._model = AutoModelForSequenceClassification.from_pretrained(self._checkpoint)
        self._model.eval()
        log.info("extraction_classifier_loaded", checkpoint=self._checkpoint)

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
            )
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
        from autune_integrations.base import HttpClient  # noqa: PLC0415

        self._client: HttpClient = HttpClient(endpoint)
        self._client.service = "extraction-classifier"
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
            rows = body.get("scores", [])
            if len(rows) != len(batch):
                raise ValueError(f"asked for {len(batch)} predictions, got {len(rows)}")
            predictions.extend(_to_prediction(row) for row in rows)
        return predictions


class FakeClassifier:
    """Deterministic, no weights, no network. What the tests run.

    Keys on Korean sentence endings because that is where the class is marked —
    the same observation the pipeline order rests on. It is not an approximation
    of the model's accuracy and must not be used to estimate it; it exists so
    everything downstream of classification can be built and tested before the
    model is trained.
    """

    model_version = "fake"

    _ENDINGS: tuple[tuple[str, UtteranceKind], ...] = (
        ("하겠습니다", UtteranceKind.COMMITMENT),
        ("할게요", UtteranceKind.COMMITMENT),
        ("하기로 했", UtteranceKind.DECISION),
        ("갑니다", UtteranceKind.DECISION),
        ("까요", UtteranceKind.OPEN_QUESTION),
        ("나요", UtteranceKind.OPEN_QUESTION),
        ("어렵", UtteranceKind.CONCERN),
        ("걱정", UtteranceKind.CONCERN),
    )

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
