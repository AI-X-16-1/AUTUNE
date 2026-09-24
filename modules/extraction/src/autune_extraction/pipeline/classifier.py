"""The utterance classifier: in-process weights, our own inference server, a fake.

Three implementations and deliberately no fourth. There is no external-API option
because sending a meeting's utterances to somebody else's classifier is a decision
about where personal data goes, and `privacy.md` section 6 makes that a design
conversation rather than a value of ``AUTUNE_EXTRACTION_CLASSIFIER_IMPL``.

``torch`` and ``transformers`` are imported inside the class that needs them.
Importing at module scope would make ``apps/api`` load a deep-learning stack to
serve a health check, and would make this module's unit tests need one.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from autune_contracts.enums import UtteranceKind
from autune_core import get_logger
from autune_extraction.labels import NONE
from autune_integrations.errors import TransientIntegrationError
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
"""The five kinds, in the order of the head's first five columns. The order is
part of the checkpoint — reordering ``UtteranceKind`` without retraining would
silently relabel every prediction.

Written out rather than ``tuple(UtteranceKind)``. Deriving it made the test that
was supposed to catch a divergence read ``x == x``: ``LABELS`` followed any
reordering of the enum, ``_to_prediction`` mapped the checkpoint's fixed columns
onto the new order, every prediction came back mislabelled, and the suite stayed
green. A literal is the only version of this that can disagree with the contract.
"""

HEAD: tuple[str, ...] = (
    "commitment",
    "decision",
    "open_question",
    "concern",
    "ambiguous",
    "none",
)
"""Every output column of the head, as ``id2label`` names them: the five kinds,
then ``none`` last (#149).

Last, so the five kinds keep the columns they always had. A sixth label is still
a new model -- a five-column checkpoint is refused by ``_check_label_order`` --
but nothing already written about the first five moves. Written out for the same
reason as ``LABELS``, and ``training.dataset.LABELS`` must equal it, which a test
checks.
"""


def _to_prediction(scores: list[float]) -> Prediction:
    """One row of probabilities, in ``HEAD`` order, into a Prediction.

    Raises rather than normalising a row that does not sum to one: a distribution
    that is off means the head or the label order is wrong, and quietly rescaling
    it would hide that behind plausible-looking confidences.
    """
    if len(scores) != len(HEAD):
        raise ValueError(f"expected {len(HEAD)} scores, got {len(scores)}")
    total = sum(scores)
    if not 0.99 <= total <= 1.01:
        raise ValueError(f"scores do not sum to 1 (got {total:.4f})")

    *kind_scores, none_score = scores
    distribution = dict(zip(LABELS, kind_scores, strict=True))
    best = max(distribution, key=lambda k: distribution[k])
    if none_score > distribution[best]:
        return Prediction(
            kind=None, confidence=none_score, scores=distribution, none_score=none_score
        )
    return Prediction(
        kind=best, confidence=distribution[best], scores=distribution, none_score=none_score
    )


def _check_label_order(id2label: Mapping[int | str, str]) -> None:
    """Refuse a checkpoint whose head is not in ``HEAD`` order.

    ``LABELS`` says the order is part of the checkpoint, and the checkpoint says
    what it is: the training loop writes ``id2label`` into ``config.json``. This
    reads it back. Without it, a checkpoint trained in another order -- or a bare
    encoder, whose untrained head is labelled ``LABEL_0`` to ``LABEL_5`` -- loads
    cleanly and relabels every prediction behind confidences that look fine.

    A five-column checkpoint from before ``none`` existed is refused too. Its
    softmax has nowhere to put "none of these", so every utterance of a meeting
    would come back as one of the kinds.

    Keys are compared as integers because ``config.json`` stores them as strings
    and transformers converts them on load; either should be accepted here.
    """
    by_index = {int(index): label for index, label in id2label.items()}
    found = [by_index.get(index) for index in range(max(len(by_index), len(HEAD)))]
    expected = list(HEAD)
    if found != expected:
        raise RuntimeError(
            f"the checkpoint's head is labelled {found}, but this classifier reads its "
            f"columns as {expected}. Retrain, or load a checkpoint the training loop wrote."
        )


def _truncation_length(model_max_length: int, max_position_embeddings: int) -> int:
    """How many tokens of an utterance the classifier reads.

    The training loop saves its ``MAX_LENGTH`` as the tokenizer's
    ``model_max_length``, so a checkpoint it wrote is cut where it was trained.
    This used to be a hardcoded 256 against a training length of 96 -- two
    numbers in two files that nothing connected.

    A tokenizer that never had a length set reports a sentinel around ``1e30``;
    the position embeddings are the real limit then.
    """
    return min(model_max_length, max_position_embeddings)


ENSEMBLE_SEPARATOR = ","
"""Separates checkpoints in ``AUTUNE_EXTRACTION_CLASSIFIER_CHECKPOINT``.

Several checkpoints are an ensemble. Measured on 2026-09-17 with three seeds of
the same training run (v2), averaging all three beat the mean of its members on
every evaluation set -- probe macro F1 0.396 -> 0.412, blind kappa 0.269 -> 0.302,
dummy team meetings macro 0.509 -> 0.522 -- while one seed alone found anywhere
from 15 to 35 of the same 61 decisions, so which seed a deployment got was luck.
Averaging the three models' *weights* into one checkpoint did not hold up on the
team meetings (macro 0.498).

The first checkpoint is the primary and scores everything; the rest are asked only
about utterances the primary is unsure of -- see ``ESCALATE_BELOW``.
"""

ESCALATE_BELOW = 0.8
"""Below this top probability from the primary, the other checkpoints are asked too
and the answer is the mean of all of them.

Asking every model every time costs one forward pass per checkpoint -- 0.7 -> 2.3
CPU minutes for a 45-minute meeting on 6 cores. Simulated from saved per-seed
probabilities with seed 20260910 as primary, the cost and what it kept:

    top p below    cost        probe macro/kappa   blind macro/kappa   dummy macro/kappa
    (single)       x1.00       0.369 / 0.212       0.302 / 0.270       0.492 / 0.502
    0.7            x1.47-1.66  0.391 / 0.246       0.323 / 0.302       0.513 / 0.530
    0.8            x1.68-1.91  0.401 / 0.260       0.328 / 0.302       0.516 / 0.533
    (all, x3)      x3.00       0.412 / 0.274       0.328 / 0.302       0.522 / 0.536

0.8 keeps the whole blind-set gain and most of the rest for about 60% of the full
cost. A constant rather than a setting: the table was measured offline with one
primary, and a knob invites values nobody has measured with the deployed
checkpoints.
"""

MODEL_VERSION_MAX = 200
"""``ext_classifications.model_version`` is ``String(200)``. Three checkpoint
directories joined overrun it, and a version that does not fit fails the write of
a meeting's whole classification."""


def _checkpoints(value: str) -> list[str]:
    """The checkpoints named by the setting, in order, blanks dropped."""
    return [part.strip() for part in value.split(ENSEMBLE_SEPARATOR) if part.strip()]


def _model_version(checkpoints: list[str]) -> str:
    """What is recorded with every classification.

    The checkpoint -- or, for an ensemble, the comma-joined list -- when it fits
    the column, as a single checkpoint always has been. Otherwise a digest of it:
    the same list gives the same version, a different list or order a different
    one, and the full list is logged when the models load.
    """
    joined = ENSEMBLE_SEPARATOR.join(checkpoints)
    if len(joined) <= MODEL_VERSION_MAX:
        return joined
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    return f"ensemble-{len(checkpoints)}:{digest}"


def _mean_distribution(per_model: list[list[list[float]]]) -> list[list[float]]:
    """Average each utterance's probabilities over the models, column by column.

    ``per_model[m][u]`` is model ``m``'s distribution for utterance ``u``. Every
    model scored the same utterances, so the lengths must agree; a mismatch is a
    bug in the caller, not something to average around.
    """
    if not per_model:
        raise ValueError("no model distributions to average")
    count = len(per_model[0])
    if any(len(rows) != count for rows in per_model):
        raise ValueError("models scored different numbers of utterances")
    n = len(per_model)
    return [
        [sum(column) / n for column in zip(*rows, strict=True)]
        for rows in zip(*per_model, strict=True)
    ]


def _unsure(primary: list[list[float]], below: float) -> list[int]:
    """Positions of the utterances whose primary top probability is below ``below``."""
    return [i for i, row in enumerate(primary) if max(row) < below]


def _cascade(
    primary: list[list[float]],
    ask_the_rest: Callable[[list[int]], list[list[list[float]]]],
    *,
    below: float = ESCALATE_BELOW,
) -> list[list[float]]:
    """The primary's distributions, with each unsure one replaced by the mean of all
    models' distributions for that utterance.

    ``ask_the_rest(positions)`` returns, per other model, its distributions for
    exactly those positions in that order. It is not called when nothing is unsure,
    which is the point: the other models cost nothing on a confident utterance.
    """
    positions = _unsure(primary, below)
    if not positions:
        return primary
    others = ask_the_rest(positions)
    if not others:
        return primary
    subset = [primary[i] for i in positions]
    merged = dict(zip(positions, _mean_distribution([subset, *others]), strict=True))
    return [merged.get(i, row) for i, row in enumerate(primary)]


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
        self._checkpoints = _checkpoints(checkpoint)
        if not self._checkpoints:
            raise ValueError("LocalDeberta needs at least one checkpoint")
        self._device = device
        self._batch_size = batch_size
        self._models: list[Any] = []
        self._tokenizer: Any = None
        self._max_length = 0

    @property
    def model_version(self) -> str:
        return _model_version(self._checkpoints)

    def _load(self) -> None:
        if self._models:
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

        # One tokenizer feeds every model, so every checkpoint must have been
        # trained with the same vocabulary. Checked rather than assumed: an
        # ensemble member fine-tuned from another encoder would receive token ids
        # that mean something else to it, and still return a distribution.
        self._tokenizer = AutoTokenizer.from_pretrained(self._checkpoints[0])
        vocab = self._tokenizer.get_vocab()
        lengths = []
        models = []
        for checkpoint in self._checkpoints:
            if (
                checkpoint != self._checkpoints[0]
                and AutoTokenizer.from_pretrained(checkpoint).get_vocab() != vocab
            ):
                raise ValueError(
                    f"{checkpoint} does not share {self._checkpoints[0]}'s vocabulary; "
                    "an ensemble must be seeds or runs of the same encoder"
                )
            model = AutoModelForSequenceClassification.from_pretrained(checkpoint)
            _check_label_order(model.config.id2label)
            lengths.append(
                _truncation_length(
                    self._tokenizer.model_max_length,
                    getattr(model.config, "max_position_embeddings", 512),
                )
            )
            model.to(self._device)
            model.eval()
            models.append(model)
        self._max_length = min(lengths)
        self._models = models
        log.info(
            "extraction_classifier_loaded",
            checkpoints=self._checkpoints,
            model_version=self.model_version,
            device=self._device,
        )

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        self._load()
        torch = self._torch

        def distributions(model: Any, batch: list[str]) -> list[list[float]]:
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self._max_length,
                return_tensors="pt",
            ).to(self._device)
            with torch.no_grad():
                rows: list[list[float]] = torch.softmax(model(**encoded).logits, dim=-1).tolist()
            return rows

        primary, *rest = self._models
        predictions: list[Prediction] = []
        escalated = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]

            def ask_the_rest(
                positions: list[int], batch: list[str] = batch
            ) -> list[list[list[float]]]:
                nonlocal escalated
                escalated += len(positions)
                subset = [batch[i] for i in positions]
                return [distributions(model, subset) for model in rest]

            rows = distributions(primary, batch)
            if rest:
                rows = _cascade(rows, ask_the_rest)
            predictions.extend(_to_prediction(row) for row in rows)
        if rest:
            log.info("extraction_classifier_escalated", utterances=len(texts), escalated=escalated)
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


RETRY_BACKOFF_SEC: tuple[float, ...] = (0.5, 2.0)
"""How long to wait before each re-attempt of one batch, and how many there are.

Two re-attempts, 2.5 seconds of waiting at worst, per batch. The numbers are
written here rather than made a setting because nobody has run this against a
real inference server yet, and a knob whose default nobody measured reads as a
tuned value. When there is a server and a latency distribution, this becomes
``AUTUNE_EXTRACTION_CLASSIFIER_RETRIES`` and gets a number somebody took.

Bounded on purpose. A meeting is tens of requests (#113) and the worker thread
sleeps through every wait, so an unbounded or long backoff turns one sick server
into a worker that is not processing anything else either. Two short attempts
cover what they are for -- a restart, a rolling deploy, one 502 -- and a server
that is down for longer should fail the task and let Celery's own retry, which
does not hold a worker, decide when to come back.
"""


MAX_CONCURRENT_BATCHES = 4
"""How many of a meeting's batches may be in flight to the inference server at
once.

Sequential was safe but wasteful: 28 requests at roughly a hundred
milliseconds of network time each is close to three seconds spent waiting, one
batch at a time, on a server that could have been answering four of them
together. ``httpx.Client`` is documented thread-safe for exactly this --
concurrent requests share one connection pool -- so a bounded thread pool
around ``_post`` is enough; nothing here is CPU-bound, so the GIL costs
nothing while a thread waits on the network.

Written here rather than made a setting, for the same reason as
``RETRY_BACKOFF_SEC``: there is no inference server yet to measure against, and
a knob nobody has tuned reads as a tuned value. Bounded on purpose too -- an
unbounded pool turns "send everything at once" into a self-inflicted burst
against a server that is supposed to also be answering everyone else, and one
meeting is not supposed to be able to do that. Four is a guess conservative
enough not to matter until there is a latency distribution to pick a real
number from.

This does not change what one bad batch does to the meeting -- a batch that
exhausts its retries still fails the whole ``classify`` call, same as before.
It only stops the *other* batches from waiting behind each other for no
reason.
"""


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

    **Each of those requests is re-attempted on a transient failure** -- see
    ``_post``. Splitting a meeting into tens of requests multiplies its exposure
    to one bad second, and the split is not optional.

    **Up to ``MAX_CONCURRENT_BATCHES`` of them are in flight together** (#113).
    Sequential sending was never necessary -- the batches do not depend on each
    other -- and a meeting of any length used to pay for that anyway. Results
    are matched back to their batch by position, not by arrival order, so a
    later batch answering first does not reshuffle the meeting's predictions.
    """

    def __init__(self, endpoint: str, model_version: str) -> None:
        self._client = _classifier_client(endpoint)
        self._model_version = model_version

    @property
    def model_version(self) -> str:
        return self._model_version

    def _post(self, batch: list[str], *, index: int) -> Any:
        """One batch, re-attempted while the failure is a transient one.

        A meeting is tens of requests, so without this a single 502 or timeout on
        the fifteenth of twenty-eight fails the whole meeting, and Celery's retry
        starts again from the first (#113). The odds compound with the length of
        the meeting, which is backwards: the longer the recording, the more
        likely it never gets classified at all.

        **Only ``TransientIntegrationError``.** That is the shared client's word
        for a timeout, an unreachable host, a 429 or a 5xx -- the failures where
        the same request later is a different answer. A 4xx arrives as
        ``PermanentIntegrationError`` and is not retried: the request is wrong,
        and sending it twice more only says so twice more.

        **Safe to repeat.** ``/classify`` stores nothing and mints nothing; the
        same texts give the same scores. It is a POST because the body is too
        large for a URL, not because it changes anything on the server.

        This does not make a long meeting survive a server that is really down --
        that is what partial progress is for, and it is still open on #113. What
        it removes is the failure that has nothing to do with the meeting.
        """
        for attempt, wait in enumerate(RETRY_BACKOFF_SEC, start=1):
            try:
                return self._client.request("POST", "/classify", json={"texts": batch})
            except TransientIntegrationError as exc:
                # Counts and a reason only. Every string in the batch is an
                # utterance, so none of it is loggable.
                log.info(
                    "extraction_classifier_retry",
                    batch=index,
                    utterances=len(batch),
                    attempt=attempt,
                    reason=str(exc),
                )
                time.sleep(wait)
        return self._client.request("POST", "/classify", json={"texts": batch})

    def classify(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []

        batches = list(_batches_within_budget(texts, MAX_OUTBOUND_CHARS))
        bodies: list[Any] = [None] * len(batches)
        # `min` so a short meeting (one or two batches) does not spin up idle
        # worker threads it will never use.
        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_BATCHES, len(batches))) as pool:
            futures = {
                pool.submit(self._post, batch, index=index): index
                for index, batch in enumerate(batches)
            }
            # Not `as_completed`: every future is waited on regardless of which
            # one raises first, so one batch's exhausted retries do not cut off
            # the others while they are still in flight against a shared
            # connection pool.
            first_error: BaseException | None = None
            for future, index in futures.items():
                try:
                    bodies[index] = future.result()
                except BaseException as exc:  # noqa: BLE001 - re-raised below, not swallowed
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error

        predictions: list[Prediction] = []
        for index, batch in enumerate(batches):
            body = bodies[index]
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

    **A miss is none**, because most of a meeting is none of the kinds. It used
    to land on ``ambiguous``, the cautious choice while the model had no way to
    say none -- but ``ambiguous`` means *ask the speaker*, and a fake that sends
    every unmatched utterance there asks about most of the meeting. Weak assent
    has its own ending instead.
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
        ("볼게요", UtteranceKind.AMBIGUOUS),
    )
    """Checked in order, first match wins. ``기로 했`` comes before ``겠습니다``
    because "하기로 했겠습니다" is a decision being reported, not a new promise."""

    def classify(self, texts: list[str]) -> list[Prediction]:
        predictions = []
        for text in texts:
            kind: UtteranceKind | None = None
            for ending, candidate in self._ENDINGS:
                if ending in text:
                    kind = candidate
                    break
            row = [0.05] * len(HEAD)
            row[HEAD.index(kind.value if kind is not None else NONE)] = 1.0 - 0.05 * (len(HEAD) - 1)
            predictions.append(_to_prediction(row))
        return predictions
