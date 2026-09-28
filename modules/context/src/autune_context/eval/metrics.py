"""Small reporting helpers both suites share — interval, per-category table.

A 50-case set moves 2 points per case, so every accuracy this package prints
carries its Wilson interval: two runs whose intervals overlap are not evidence
that one configuration beats the other.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Protocol


class Scored(Protocol):
    @property
    def category(self) -> str: ...

    @property
    def correct(self) -> bool: ...


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion; ``(0, 0)`` for no trials.

    Wilson rather than the normal approximation because the normal one
    collapses to a zero-width interval at 0% or 100%, which a small category
    reaches all the time.
    """
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def accuracy_line(label: str, successes: int, total: int) -> str:
    acc = successes / total if total else 0.0
    lo, hi = wilson_interval(successes, total)
    return f"{label}: {successes}/{total} = {acc:.2f} (95% CI {lo:.2f}-{hi:.2f})"


def by_category(results: Iterable[Scored]) -> list[str]:
    """One aligned line per category, in first-seen order (the dataset's order)."""
    counts: dict[str, list[int]] = {}
    for r in results:
        bucket = counts.setdefault(r.category, [0, 0])
        bucket[0] += r.correct
        bucket[1] += 1
    width = max((len(c) for c in counts), default=0)
    return [
        f"  {category:<{width}}  {ok:>2}/{n:<2} = {ok / n:.2f}"
        for category, (ok, n) in counts.items()
    ]


def ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} = {numerator / denominator:.2f}" if denominator else "n/a"


def sweep_points(start: float = 0.05, stop: float = 0.95, step: float = 0.05) -> Sequence[float]:
    count = round((stop - start) / step) + 1
    return [round(start + i * step, 3) for i in range(count)]
