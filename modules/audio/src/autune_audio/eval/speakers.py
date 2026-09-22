"""Scoring for the live speaker tracker's threshold, without a model.

Takes vectors and a hand-written reference, replays ``SpeakerTracker`` at
each threshold, and reports how many clusters opened and how clean they are.
The reference is the S1 section of evaluation 02: blocks of time where one
known person speaks. An utterance is scored by the block its midpoint falls
in; outside every block it is labelled but not scored.

Two numbers, both over scored utterances only:

- **purity** -- for each cluster, the share of its utterances that belong to
  its majority reference speaker; averaged over utterances. 1.0 means no
  cluster mixes two people.
- **completeness** -- for each reference speaker, the share of their
  utterances that landed in their largest cluster; averaged over speakers.
  1.0 means nobody was split.

With no scored utterances (every truth is ``None``) both metrics report 1.0:
there is nothing to be impure or incomplete about.

Design: ``docs/modules/audio-live-speakers.md`` section 6.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from autune_audio.live.speakers import SpeakerTracker


@dataclass(frozen=True)
class Block:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class Score:
    threshold: float
    capped: bool
    clusters: int
    purity: float
    completeness: float


def parse_reference(text: str) -> tuple[Block, ...]:
    """``"1.5-39.7:A,40.0-73.7:B"`` -> blocks. Whitespace around commas is fine."""
    blocks = []
    for item in text.split(","):
        span, speaker = item.strip().rsplit(":", 1)
        start, end = span.split("-")
        blocks.append(Block(float(start), float(end), speaker.strip()))
    return tuple(blocks)


def reference_speaker(blocks: Sequence[Block], start: float, end: float) -> str | None:
    middle = (start + end) / 2
    for block in blocks:
        if block.start <= middle < block.end:
            return block.speaker
    return None


def simulate(
    vectors: Sequence[np.ndarray],
    seconds: Sequence[float],
    *,
    threshold: float,
    max_speakers: int | None,
    min_seconds: float,
) -> list[str]:
    tracker = SpeakerTracker(
        threshold=threshold, min_seconds=min_seconds, max_speakers=max_speakers
    )
    return [tracker.label(v, s) for v, s in zip(vectors, seconds, strict=True)]


def _scored(labels: Sequence[str], truth: Sequence[str | None]) -> list[tuple[str, str]]:
    return [(label, who) for label, who in zip(labels, truth, strict=True) if who is not None]


def purity(labels: Sequence[str], truth: Sequence[str | None]) -> float:
    pairs = _scored(labels, truth)
    if not pairs:
        return 1.0
    by_cluster: dict[str, Counter[str]] = defaultdict(Counter)
    for label, who in pairs:
        by_cluster[label][who] += 1
    majority = sum(counts.most_common(1)[0][1] for counts in by_cluster.values())
    return majority / len(pairs)


def completeness(labels: Sequence[str], truth: Sequence[str | None]) -> float:
    pairs = _scored(labels, truth)
    if not pairs:
        return 1.0
    by_speaker: dict[str, Counter[str]] = defaultdict(Counter)
    for label, who in pairs:
        by_speaker[who][label] += 1
    shares = [counts.most_common(1)[0][1] / sum(counts.values()) for counts in by_speaker.values()]
    return float(np.mean(shares))


def sweep(
    vectors: Sequence[np.ndarray],
    seconds: Sequence[float],
    truth: Sequence[str | None],
    *,
    thresholds: Sequence[float],
    max_speakers: int | None,
    min_seconds: float,
) -> list[Score]:
    caps: list[int | None] = [None] if max_speakers is None else [None, max_speakers]
    scores = []
    for threshold in thresholds:
        for cap in caps:
            labels = simulate(
                vectors, seconds, threshold=threshold, max_speakers=cap, min_seconds=min_seconds
            )
            scores.append(
                Score(
                    threshold=threshold,
                    capped=cap is not None,
                    clusters=len(set(labels)),
                    purity=purity(labels, truth),
                    completeness=completeness(labels, truth),
                )
            )
    return scores
