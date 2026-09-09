"""Scoring for module B's evaluation harness.

Pure functions over label sequences: no model loading, no database, no corpus.
The harness composes these and the unit tests exercise them directly, which is
the only reason any of this can be tested before the evaluation set exists.

ADR 0006 makes the five-way macro F1 defined here the metric module B trains
against, because it is what a training run can actually move. Action item F1 is
derived from it rather than measured on its own — see ``action_item_f1`` for
what that derivation assumes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from autune_contracts.enums import UtteranceKind

KINDS: tuple[UtteranceKind, ...] = tuple(UtteranceKind)

# Liu et al., Meeting Action Item Detection with Regularized Context Modeling,
# ICASSP 2023 (arxiv.org/abs/2303.16763): best positive-class F1 on AMI, against
# a 38.67 sentence-level baseline in the same table. ADR 0006 reports our number
# beside this one so a reader who does not know the task can read ours.
AMI_BEST_PUBLISHED_ACTION_ITEM_F1 = 0.4312


@dataclass(frozen=True)
class ClassScore:
    """One class's scores. ``support`` is the gold count, ``predicted`` ours."""

    kind: UtteranceKind
    precision: float
    recall: float
    f1: float
    support: int
    predicted: int

    @property
    def is_absent(self) -> bool:
        """True when the class appears in neither the gold set nor the output.

        Its F1 is 0.0 by convention and that zero drags the macro average down
        for a reason that has nothing to do with the model. Worth seeing rather
        than silently averaging over.
        """
        return self.support == 0 and self.predicted == 0


@dataclass(frozen=True)
class Report:
    per_class: tuple[ClassScore, ...]
    macro_f1: float
    accuracy: float
    n: int
    confusion: dict[tuple[UtteranceKind, UtteranceKind], int]

    def by_kind(self, kind: UtteranceKind) -> ClassScore:
        for score in self.per_class:
            if score.kind is kind:
                return score
        raise KeyError(kind)  # unreachable: per_class covers every kind

    @property
    def absent_kinds(self) -> tuple[UtteranceKind, ...]:
        return tuple(s.kind for s in self.per_class if s.is_absent)


def _divide(numerator: int, denominator: int) -> float:
    """Zero when undefined.

    Standard for precision and recall, and the reason ``absent_kinds`` exists:
    a class scoring 0.0 because it never occurred looks identical to one the
    model got wrong every time, so the harness reports which it was.
    """
    return numerator / denominator if denominator else 0.0


def score(gold: list[UtteranceKind], predicted: list[UtteranceKind]) -> Report:
    """Score a five-way run. Macro F1 averages over all five classes.

    Macro rather than micro because the class distribution is skewed and the
    rare classes are the product. ``concern`` and ``ambiguous`` feed the NLI
    confirmation step, so a model that scores well by getting the common classes
    right is not the model this module needs.
    """
    if len(gold) != len(predicted):
        raise ValueError(f"gold has {len(gold)} labels, predictions have {len(predicted)}")
    if not gold:
        raise ValueError("nothing to score: the evaluation set is empty")

    confusion: Counter[tuple[UtteranceKind, UtteranceKind]] = Counter(
        zip(gold, predicted, strict=True)
    )

    scores = []
    for kind in KINDS:
        true_positive = confusion[(kind, kind)]
        support = sum(confusion[(kind, p)] for p in KINDS)
        predicted_count = sum(confusion[(g, kind)] for g in KINDS)
        precision = _divide(true_positive, predicted_count)
        recall = _divide(true_positive, support)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores.append(
            ClassScore(
                kind=kind,
                precision=precision,
                recall=recall,
                f1=f1,
                support=support,
                predicted=predicted_count,
            )
        )

    correct = sum(confusion[(k, k)] for k in KINDS)
    return Report(
        per_class=tuple(scores),
        macro_f1=sum(s.f1 for s in scores) / len(KINDS),
        accuracy=correct / len(gold),
        n=len(gold),
        confusion=dict(confusion),
    )


def action_item_f1(report: Report) -> float:
    """Action item F1, derived from the five-way report per ADR 0006.

    The derivation is the ``commitment`` class F1. The pipeline builds one action
    item card per commitment, so an utterance the classifier misses is a card
    that never exists, and one it invents is a card the user has to delete.

    This is an upper bound on the number a user would recognise. Slot filling can
    still fail on a correctly classified commitment — a missing assignee, an
    unparseable due date — and this figure cannot see that. It exists to compare
    against the literature, not to tell us the product works.
    """
    return report.by_kind(UtteranceKind.COMMITMENT).f1
