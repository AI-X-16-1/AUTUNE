"""Token alignment shared by the error-rate metrics.

One Levenshtein implementation, so CER, MER and PIER disagree only in what they
tokenise and what they count, never in how they align.

The distance is unique; the alignment is not. Where two alignments cost the
same, the backtrace here prefers a substitution, then a deletion, then an
insertion, and rapidfuzz — which HiKE scores with — resolves the tie its own
way. CER and MER are distances, so they cannot tell. PIER counts *where* an
edit lands, so on a tied alignment it can attribute an edit to a neighbouring
position: 10 of 1,181 real predictions in the fidelity fixture, and the
direction is not random — substitution-first backtracing lands more edits on
tagged positions, so our PIER read higher on 9 of the 10, +0.12 points on the
1,121-row corpus mean. That divergence is measured by
``scripts/hike_fidelity_fixture.py`` rather than closed, because closing it
means porting rapidfuzz's bit-parallel backtrace.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def edit_ops(reference: Sequence[str], hypothesis: Sequence[str]) -> list[tuple[str, int]]:
    """A minimal token alignment as (op, reference position) pairs.

    ``S`` and ``D`` sit on the reference token they touch. ``I`` sits on the
    reference token the inserted one precedes, which is ``len(reference)`` for
    an insertion after the last word — the same positions rapidfuzz reports,
    which is what HiKE's PIER counts over.
    """
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    ops: list[list[str]] = [[""] * cols for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0], ops[i][0] = i, "D"
    for j in range(1, cols):
        cost[0][j], ops[0][j] = j, "I"

    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                cost[i][j], ops[i][j] = cost[i - 1][j - 1], "="
                continue
            substitute, delete, insert = (
                cost[i - 1][j - 1] + 1,
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
            )
            best = min(substitute, delete, insert)
            cost[i][j] = best
            ops[i][j] = "S" if best == substitute else ("D" if best == delete else "I")

    edits: list[tuple[str, int]] = []
    i, j = len(reference), len(hypothesis)
    while i or j:
        op = ops[i][j]
        if op == "S":
            edits.append(("S", i - 1))
            i, j = i - 1, j - 1
        elif op == "=":
            i, j = i - 1, j - 1
        elif op == "D":
            edits.append(("D", i - 1))
            i -= 1
        else:
            edits.append(("I", i))
            j -= 1
    edits.reverse()
    return edits


def count(edits: Iterable[tuple[str, int]]) -> tuple[int, int, int]:
    """(substitutions, deletions, insertions)."""
    counts = {"S": 0, "D": 0, "I": 0}
    for op, _ in edits:
        counts[op] += 1
    return counts["S"], counts["D"], counts["I"]
