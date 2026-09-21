"""Score the masker against the corpus -- once, for the CLI and the tests.

They were two loops. They agreed on the happy path and disagreed on the two
things that matter: which rows are *expected* to differ, and what a row the
metric cannot align costs. The CLI failed every run on three rows the tests
had declared; the CLI skipped the row whose token count changed while the
tests raised on it. One scorer, and the declaration on the corpus row itself
(``Row.known_inexact``), is the fix for both (#179 review).

Nothing here prints. ``identify`` is what a message may say about a row.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from autune_audio.eval.corpus import Row
from autune_audio.eval.metrics import masking_precision, masking_recall
from autune_audio.masking import MASK_CHAR, mask

RECALL_TARGET = 0.95


@dataclass(frozen=True)
class Report:
    rows: int
    exact: int
    recall: float
    precision: float
    spans_caught: int
    spans_expected: int
    spans_correct: int
    spans_produced: int
    unexplained: list[Row]
    """Rows that differ and carry no ``known_inexact``. Any one fails the run."""
    declared: list[Row]
    """Rows that differ and say so. Reported, not failed."""
    fixed: list[Row]
    """Rows that carry ``known_inexact`` and came out exactly right. The
    declaration is stale and the run fails until it is removed -- the list
    cannot be allowed to outlive the problem it names."""
    missed_categories: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.unexplained and not self.fixed and self.recall >= RECALL_TARGET


def identify(row: Row) -> str:
    """What a failure message or a ``--verbose`` line may say about a row.

    Source, line number, categories. Never the text and never the masked form:
    a message goes to a CI log or an error tracker, and the pair is what makes
    a value recoverable (privacy.md section 2). The corpus is invented values
    today and real transcripts tomorrow -- ``corpus.py`` says that is how it
    grows -- and the day it does is not the day to remember this.
    """
    return f"{row.source}#{row.index} {' '.join(row.categories) or 'negative'}"


def score(
    rows: Sequence[Row],
    *,
    recogniser: object | None = None,
    masker: Callable[[str], str] | None = None,
    declarations_apply: bool = True,
) -> Report:
    """Run the masker over ``rows`` and say what happened.

    ``masker`` is for tests that want to score a made-up output without the
    real masker; the CLI passes a ``recogniser`` and leaves it None.

    ``declarations_apply`` is False when the masker is not the one the corpus
    describes -- ``--no-recogniser`` -- so a ``known_inexact`` written about the
    recogniser's behaviour is neither honoured nor policed there; the row is
    scored like any other.

    A row the metrics cannot align -- the masker changed the token count,
    which is what merging spans across a separator does -- is over-masking,
    not a leak: recall counts its reference spans as caught, and precision
    counts every span it produced as wrong. Skipping it, as the CLI used to,
    dropped it from both the numerator and the denominator and made the worst
    over-masking invisible in the number; raising, as the tests used to,
    stopped the run at it.
    """
    run = masker or (lambda text: mask(text, recogniser=recogniser).text)  # type: ignore[arg-type]

    caught = expected = correct = produced = 0
    unexplained: list[Row] = []
    declared: list[Row] = []
    fixed: list[Row] = []
    missed: Counter[str] = Counter()

    for row in rows:
        ours = run(row.text)
        differs = ours != row.masked
        declaration = row.known_inexact if declarations_apply else None

        if differs and declaration:
            declared.append(row)
        elif differs:
            unexplained.append(row)
            if row.is_positive:
                missed.update(row.categories)
        elif declaration:
            fixed.append(row)

        try:
            if row.is_positive:
                recall = masking_recall(row.masked, ours)
                caught += recall.spans_we_caught
                expected += recall.spans_in_reference
            precision = masking_precision(row.masked, ours)
            correct += precision.spans_correctly_masked
            produced += precision.spans_we_masked
        except ValueError:
            # The token counts differ: the masker ate a separator and merged
            # spans, which is over-masking, not a leak. Every reference span
            # is under the merged mask, so recall counts them caught; the
            # merged span counts as produced with none of it correct, so
            # precision pays. Before this the CLI skipped the row and the
            # tests crashed on it -- the one over-masking masking.py:216 was
            # fixed for left no mark in either number.
            if row.is_positive:
                spans = _masked_spans(row.masked)
                caught += spans
                expected += spans
            produced += _masked_spans(ours)

    return Report(
        rows=len(rows),
        exact=len(rows) - len(unexplained) - len(declared),
        recall=caught / expected if expected else 1.0,
        precision=correct / produced if produced else 1.0,
        spans_caught=caught,
        spans_expected=expected,
        spans_correct=correct,
        spans_produced=produced,
        unexplained=unexplained,
        declared=declared,
        fixed=fixed,
        missed_categories=missed,
    )


def _masked_spans(text: str) -> int:
    """How many runs of the mask character ``text`` holds."""
    spans = 0
    inside = False
    for char in text:
        if char == MASK_CHAR and not inside:
            spans += 1
        inside = char == MASK_CHAR
    return spans
