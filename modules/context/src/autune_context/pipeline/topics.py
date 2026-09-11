"""Topic-statement extraction — MVP, deliberately not an LLM task.

Embedding-based TextTiling splits the transcript where the conversation turns;
kiwipiepy noun phrases label each segment. What is matched across meetings is the
segment's mean-pooled embedding, so both sides stay consistent as long as they
run the same extractor. See docs/modules/context.md, "Topic-statement extraction
is not an LLM task (MVP)".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from autune_context.pipeline.base import Embedder
    from autune_contracts import Utterance

_NOUN_POS = {"NNG", "NNP", "SL", "SN"}
_SUFFIX_POS = {"XSN"}  # noun-deriving suffix: 개인 + 화 -> 개인화


@dataclass(frozen=True)
class TopicSegment:
    label: str
    text: str
    utterance_ids: list[str]
    vector: list[float]


def extract_topics(
    utterances: list[Utterance],
    embedder: Embedder,
    *,
    window: int = 3,
    min_segment: int = 3,
    depth_threshold: float = 0.1,
) -> list[TopicSegment]:
    """Split ``utterances`` into topic segments and label each one.

    Returns an empty list for a transcript too short to segment (< ``2 *
    min_segment`` utterances) — a one-topic meeting produces one segment.
    """
    texts = [u.text for u in utterances]
    if not texts:
        return []

    vectors = np.asarray(embedder.embed(texts), dtype=np.float64)
    boundaries = _texttiling_boundaries(vectors, window, min_segment, depth_threshold)
    spans = _spans_from_boundaries(len(utterances), boundaries, min_segment)

    return [_segment(utterances, vectors, start, end) for start, end in spans]


def _texttiling_boundaries(
    vectors: np.ndarray, window: int, min_segment: int, depth_threshold: float
) -> list[int]:
    n = len(vectors)
    if n < 2 * min_segment:
        return []

    gap_sim = np.empty(n - 1)
    for i in range(n - 1):
        left = vectors[max(0, i - window + 1) : i + 1].mean(axis=0)
        right = vectors[i + 1 : i + 1 + window].mean(axis=0)
        gap_sim[i] = _cosine(left, right)

    boundaries: list[int] = []
    for i in range(1, len(gap_sim) - 1):
        if gap_sim[i] <= gap_sim[i - 1] and gap_sim[i] <= gap_sim[i + 1]:
            depth = _local_peak(gap_sim, i, -1) + _local_peak(gap_sim, i, 1) - 2 * gap_sim[i]
            if depth >= depth_threshold:
                boundaries.append(i + 1)  # first utterance of the next segment
    return boundaries


def _local_peak(sim: np.ndarray, i: int, step: int) -> float:
    peak = sim[i]
    j = i + step
    while 0 <= j < len(sim) and sim[j] >= peak:
        peak = sim[j]
        j += step
    return float(peak)


def _spans_from_boundaries(
    n: int, boundaries: list[int], min_segment: int
) -> list[tuple[int, int]]:
    cuts = [0, *boundaries, n]
    spans: list[tuple[int, int]] = []
    start = 0
    for end in cuts[1:]:
        if end - start >= min_segment or end == n:
            spans.append((start, end))
            start = end
    if spans and spans[-1][1] != n:
        spans[-1] = (spans[-1][0], n)
    if not spans:
        spans = [(0, n)]
    # fold a too-short trailing segment into the previous one
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < min_segment:
        prev_start, _ = spans[-2]
        spans = [*spans[:-2], (prev_start, n)]
    return spans


def _segment(
    utterances: list[Utterance], vectors: np.ndarray, start: int, end: int
) -> TopicSegment:
    chunk = utterances[start:end]
    mean = vectors[start:end].mean(axis=0)
    norm = np.linalg.norm(mean) or 1.0
    text = " ".join(u.text for u in chunk)
    return TopicSegment(
        label=_label(text),
        text=text,
        utterance_ids=[u.id for u in chunk],
        vector=(mean / norm).tolist(),
    )


def _label(text: str, *, max_words: int = 4) -> str:
    """Most repeated run of content nouns in the segment; falls back to a snippet."""
    from kiwipiepy import Kiwi

    kiwi = _kiwi(Kiwi)
    counts: dict[str, int] = {}
    words: list[str] = []
    for token in kiwi.tokenize(text):
        if token.tag in _NOUN_POS:
            words.append(token.form)
        elif token.tag in _SUFFIX_POS and words:
            words[-1] += token.form
        else:
            words.clear()
            continue
        if len(words) > max_words:
            words.pop(0)
        for size in range(1, len(words) + 1):
            phrase = " ".join(words[-size:])
            counts[phrase] = counts.get(phrase, 0) + 1
    if not counts:
        return text[:40].strip()
    # break count ties towards the longer, more specific phrase
    return max(counts, key=lambda p: (counts[p], len(p.split()), len(p)))


_KIWI_CACHE: list = []


def _kiwi(kiwi_cls: type):
    if not _KIWI_CACHE:
        _KIWI_CACHE.append(kiwi_cls())
    return _KIWI_CACHE[0]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0
