"""Calibration arithmetic for module E's metric (docs/engineering/testing.md).

Pure functions over (probability, outcome) pairs. E's accuracy target is not
"does it rank risky meetings first" but "when it says 30%, do about 30% of
those meetings go on to have a decision reversed" — a probability shown on a
dashboard is read as a frequency, so that is the promise to check.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

_EPS = 1e-15


@dataclass(frozen=True)
class ReliabilityBin:
    """One probability bucket: what was predicted on average vs. what happened."""

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float


@dataclass(frozen=True)
class CalibrationReport:
    count: int
    positives: int
    base_rate: float
    brier: float
    brier_climatology: float
    """Brier score of always predicting ``base_rate``. The bar a model must clear
    to be worth more than a constant."""
    brier_skill: float | None
    """``1 - brier / brier_climatology``. Positive beats the constant; ``None``
    when every outcome is the same and the constant is already perfect."""
    log_loss: float
    ece: float
    """Expected calibration error: count-weighted mean ``|predicted - observed|``
    over the reliability bins."""
    bins: tuple[ReliabilityBin, ...]


def brier(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    return sum((p - float(y)) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(
        probabilities
    )


def log_loss(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    total = 0.0
    for p, y in zip(probabilities, outcomes, strict=True):
        p = min(max(p, _EPS), 1.0 - _EPS)
        total -= math.log(p) if y else math.log(1.0 - p)
    return total / len(probabilities)


def reliability_bins(
    probabilities: Sequence[float], outcomes: Sequence[bool], n_bins: int = 10
) -> tuple[ReliabilityBin, ...]:
    """Equal-width bins over ``[0, 1]``; empty bins are left out."""
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probabilities, outcomes, strict=True):
        buckets[min(int(p * n_bins), n_bins - 1)].append((p, y))
    return tuple(
        ReliabilityBin(
            lower=i / n_bins,
            upper=(i + 1) / n_bins,
            count=len(bucket),
            mean_predicted=sum(p for p, _ in bucket) / len(bucket),
            observed_rate=sum(1 for _, y in bucket if y) / len(bucket),
        )
        for i, bucket in enumerate(buckets)
        if bucket
    )


def calibration_report(
    probabilities: Sequence[float], outcomes: Sequence[bool], n_bins: int = 10
) -> CalibrationReport:
    if not probabilities:
        raise ValueError("calibration needs at least one labeled prediction")
    count = len(probabilities)
    positives = sum(1 for y in outcomes if y)
    base_rate = positives / count
    score = brier(probabilities, outcomes)
    climatology = brier([base_rate] * count, outcomes)
    bins = reliability_bins(probabilities, outcomes, n_bins)
    return CalibrationReport(
        count=count,
        positives=positives,
        base_rate=base_rate,
        brier=score,
        brier_climatology=climatology,
        brier_skill=(1.0 - score / climatology) if climatology > 0 else None,
        log_loss=log_loss(probabilities, outcomes),
        ece=sum(b.count * abs(b.mean_predicted - b.observed_rate) for b in bins) / count,
        bins=bins,
    )
