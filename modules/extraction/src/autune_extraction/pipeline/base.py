"""Model-facing interfaces for module B.

Every model this module runs sits behind one of these Protocols. The concrete
implementation is chosen by a config string (``AUTUNE_EXTRACTION_*_IMPL``) and is
never referenced directly outside this package — the same shape module D settled
on, so the two modules can be read the same way.

**No implementation here sends an utterance to a third party.** The utterance
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
    """One utterance's kind, or none, and how sure the model is of it.

    ``kind`` is ``None`` when the model's answer is "none of these" -- which is
    most of a meeting. See ``autune_extraction.labels`` for why that is a label
    the model can give and not one the contract has. A caller building
    ``Classification`` rows has to handle it, and the type says so.

    ``confidence`` is the probability of the answer, whichever it was -- the
    softmax maximum, not the margin over the runner-up. ADR 0006 compares it
    against a threshold to decide whether an item is asserted or shown as a
    candidate, so the number has to mean "how likely is this label" rather than
    "how much better than the next one".

    ``scores`` carries the five kinds' probabilities and ``none_score`` the
    sixth; together they sum to one. The threshold work in #64 needs to look at
    what the model nearly said, and a caller that only has the maximum cannot
    recover it.
    """

    kind: UtteranceKind | None
    confidence: float
    scores: dict[UtteranceKind, float]
    none_score: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def runner_up(self) -> tuple[UtteranceKind | None, float]:
        """The second-most-likely answer and its score, ``None`` meaning none.

        A commitment at 0.51 with a decision at 0.49 is a different situation
        from one at 0.51 with everything else near zero, and only the first is
        worth a person's attention. A kind that narrowly lost to none is the
        same situation from the other side.
        """
        answers: list[tuple[UtteranceKind | None, float]] = [
            *self.scores.items(),
            (None, self.none_score),
        ]
        ranked = sorted(answers, key=lambda kv: kv[1], reverse=True)
        return ranked[1] if len(ranked) > 1 else (self.kind, 0.0)


@dataclass(frozen=True)
class NliScores:
    """One premise/hypothesis result. ``label`` is the argmax of the three --
    the model's own answer, not a threshold a caller picks (#12: entailment
    means "the speaker actually promised this", neutral or contradiction means
    the weak assent stays weak)."""

    label: str  # "entailment" | "contradiction" | "neutral"
    entailment: float
    contradiction: float
    neutral: float


@dataclass(frozen=True)
class ResolutionRequest:
    """One utterance to resolve, and the context it may draw a referent from.

    ``context`` is masked text only, oldest first -- privacy.md section 6, the
    same rule ``target`` is already under. Nothing outside this window is
    available to resolve against, by construction: a resolver cannot look up
    the rest of the meeting, only what it was handed.
    """

    target: str
    context: tuple[str, ...] = ()


@runtime_checkable
class ReferenceResolver(Protocol):
    """A commitment or decision's closing utterance, with its pronouns and
    bare references filled in from what came before it (#175).

    "그거 제가 할게요" becomes "회의실 예약 제가 할게요" when the context named
    what "그거" was -- ``ext_action_items.description`` and
    ``ext_decisions.statement`` read the resolved form; the quote itself is
    still reachable through ``source_utterance_ids``, so nothing is lost by
    rewriting it.

    **Never raises for one bad request.** A resolver that cannot resolve a
    reference, generates something not grounded in its own context, or fails
    to answer at all returns that request's own ``target`` unchanged rather
    than raising -- the pipeline does not stop for one commitment. This is a
    property implementations must uphold, not something ``resolve`` can be
    asked to skip: a caller passing bad input still gets a same-length,
    same-order answer back.
    """

    @property
    def model_version(self) -> str:
        """Pinned, and recorded the same way ``Classifier.model_version`` is."""
        ...

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        """One resolved sentence per request, in order, never fewer.

        Order is the contract, the same reason ``Classifier.classify`` promises
        it: callers zip this against their own utterance ids.
        """
        ...


@runtime_checkable
class Classifier(Protocol):
    """Five kinds or none, per utterance. Fine-tuned DeBERTa by default.

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


@runtime_checkable
class NliModel(Protocol):
    """Premise/hypothesis entailment -- step 4 (#12): does an utterance the
    5-way classifier called ``ambiguous`` actually entail a real promise.
    klue/roberta fine-tuned on KorNLI by default (#172).

    A second, independent copy of module D's own ``NliModel`` seam
    (``autune_context.pipeline.base``) rather than a shared one: modules never
    import each other (invariant 2), and the two modules use NLI for different
    questions (this one for weak-assent verification, D's for decision-change
    detection) that happen to be the same kind of model call.
    """

    @property
    def model_version(self) -> str: ...

    def classify(self, pairs: list[tuple[str, str]]) -> list[NliScores]:
        """One result per ``(premise, hypothesis)`` pair, aligned to ``pairs``."""
        ...
