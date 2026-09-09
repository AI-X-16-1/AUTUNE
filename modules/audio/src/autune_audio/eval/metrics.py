"""The three numbers module A is judged on.

Scoring is separated from the pipeline: these functions take reference and
hypothesis structures, not a model and an audio file. A run can be rescored
without a GPU, the arithmetic is testable before a corpus exists, and the metric
means the same thing across model versions — the same shape as the extraction
harness in ``autune_extraction.eval``.

DER comes from ``pyannote.metrics`` rather than a local implementation. Optimal
speaker mapping is where a hand-rolled version goes subtly wrong, and a figure
that cannot be compared to published ones is not worth having.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyannote.core import Annotation, Segment
from pyannote.metrics.diarization import DiarizationErrorRate

# A masked span keeps its shape and replaces the content. Anything holding "*"
# is masked; this is the same convention as autune_integrations.privacy.
_MASK = re.compile(r"\S*\*+\S*")
_TOKEN = re.compile(r"\S+")


@dataclass(frozen=True)
class Turn:
    """One speaker's stretch of speech, from a label file or from our output."""

    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class WordErrorRate:
    wer: float
    substitutions: int
    deletions: int
    insertions: int
    reference_words: int

    def __repr__(self) -> str:
        """Counts only. Never the words — this is meeting content."""
        return (
            f"WordErrorRate(wer={self.wer:.4f}, S={self.substitutions}, "
            f"D={self.deletions}, I={self.insertions}, N={self.reference_words})"
        )


def word_error_rate(reference: str, hypothesis: str) -> WordErrorRate:
    """Levenshtein distance over words, reported as its three parts.

    The parts matter more than the total here: deletions and insertions point at
    segmentation, substitutions at the acoustic model, and a Korean transcript
    can score badly for either reason.
    """
    ref = _TOKEN.findall(reference)
    hyp = _TOKEN.findall(hypothesis)
    if not ref:
        raise ValueError("reference is empty; word error rate is undefined")

    # distance[i][j] = edits to turn ref[:i] into hyp[:j], with the operation
    # that got there, so the three counts can be recovered.
    rows, cols = len(ref) + 1, len(hyp) + 1
    cost = [[0] * cols for _ in range(rows)]
    ops: list[list[str]] = [[""] * cols for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0], ops[i][0] = i, "D"
    for j in range(1, cols):
        cost[0][j], ops[0][j] = j, "I"

    for i in range(1, rows):
        for j in range(1, cols):
            if ref[i - 1] == hyp[j - 1]:
                cost[i][j], ops[i][j] = cost[i - 1][j - 1], "="
                continue
            substitute = cost[i - 1][j - 1] + 1
            delete = cost[i - 1][j] + 1
            insert = cost[i][j - 1] + 1
            best = min(substitute, delete, insert)
            cost[i][j] = best
            ops[i][j] = "S" if best == substitute else ("D" if best == delete else "I")

    counts = {"S": 0, "D": 0, "I": 0}
    i, j = len(ref), len(hyp)
    while i or j:
        op = ops[i][j]
        if op in counts:
            counts[op] += 1
        if op in ("=", "S"):
            i, j = i - 1, j - 1
        elif op == "D":
            i -= 1
        else:
            j -= 1

    return WordErrorRate(
        wer=(counts["S"] + counts["D"] + counts["I"]) / len(ref),
        substitutions=counts["S"],
        deletions=counts["D"],
        insertions=counts["I"],
        reference_words=len(ref),
    )


def _annotation(turns: list[Turn]) -> Annotation:
    annotation = Annotation()
    for turn in turns:
        annotation[Segment(turn.start, turn.end)] = turn.speaker
    return annotation


def diarization_error_rate(
    reference: list[Turn], hypothesis: list[Turn], *, collar: float = 0.25
) -> float:
    """DER, via pyannote.metrics.

    ``collar`` forgives boundary imprecision either side of a turn change, which
    is the convention published DER figures use. Scoring with collar 0 produces
    a worse number that is not comparable to anything.
    """
    if not reference:
        raise ValueError("reference has no turns; DER is undefined")
    metric = DiarizationErrorRate(collar=collar)
    return float(metric(_annotation(reference), _annotation(hypothesis)))


@dataclass(frozen=True)
class MaskingRecall:
    recall: float
    spans_in_reference: int
    spans_we_masked: int

    def __repr__(self) -> str:
        return (
            f"MaskingRecall(recall={self.recall:.4f}, "
            f"reference={self.spans_in_reference}, ours={self.spans_we_masked})"
        )


def masking_recall(reference_masked: str, ours: str) -> MaskingRecall:
    """How much of what the corpus masked did we mask.

    Recall, not precision, because the asymmetry is not close: an over-masked
    word is an annoyance and a leaked national ID number is an incident. The
    target in docs/modules/audio.md is 0.95 and then 0.99.

    Both arguments are already masked text. The unmasked original is read only
    to produce ``ours`` and is never passed here.
    """
    expected = len(_MASK.findall(reference_masked))
    if expected == 0:
        raise ValueError("reference has no masked spans; recall is undefined")
    got = len(_MASK.findall(ours))
    return MaskingRecall(
        recall=min(got, expected) / expected,
        spans_in_reference=expected,
        spans_we_masked=got,
    )
