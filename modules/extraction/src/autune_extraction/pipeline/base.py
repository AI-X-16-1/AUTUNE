"""Model-facing interfaces for module B.

Every model this module runs sits behind one of these Protocols. The concrete
implementation is chosen by a config string (``AUTUNE_EXTRACTION_*_IMPL``) and is
never referenced directly outside this package — the same shape module D settled
on, so the two modules can be read the same way.

**No implementation here sends an utterance to a third party.** The five-way
classifier is a model we fine-tune, run in process or on our own inference
server; there is no ``external`` option and adding one would be a privacy
decision rather than a config string. `privacy.md` section 6 bounds what may
leave our infrastructure, and a whole meeting's utterances is not it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from autune_contracts.enums import UtteranceKind


@dataclass(frozen=True)
class Prediction:
    """One utterance's kind and how sure the model is of it.

    ``confidence`` is the probability of ``kind`` — the softmax maximum, not the
    margin over the runner-up. ADR 0006 compares it against a threshold to decide
    whether an item is asserted or shown as a candidate, so the number has to mean
    "how likely is this label" rather than "how much better than the next one".

    ``scores`` carries the full distribution. The threshold work in #64 needs to
    look at what the model nearly said, and a caller that only has the maximum
    cannot recover it.
    """

    kind: UtteranceKind
    confidence: float
    scores: dict[UtteranceKind, float]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def runner_up(self) -> tuple[UtteranceKind, float]:
        """The second-most-likely kind and its score.

        A commitment at 0.51 with a decision at 0.49 is a different situation
        from one at 0.51 with everything else near zero, and only the first is
        worth a person's attention.
        """
        ranked = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[1] if len(ranked) > 1 else (self.kind, 0.0)


@runtime_checkable
class Classifier(Protocol):
    """Five-way utterance classification. Fine-tuned DeBERTa by default.

    Takes a batch rather than one utterance: a 45-minute meeting is thousands of
    utterances, and a per-utterance call turns one forward pass into thousands.
    The corpus this is trained on has 398,748.
    """

    @property
    def model_version(self) -> str:
        """Pinned, and recorded with every row this produces.

        A score that cannot be attributed to a model version cannot be compared
        against the next one, and the evaluation harness reports across versions.
        """
        ...

    def classify(self, texts: list[str]) -> list[Prediction]:
        """One prediction per input, in the same order.

        Order is the contract: callers zip the result against their own utterance
        ids rather than the model returning them, so the model never needs to know
        what an utterance id is.
        """
        ...
