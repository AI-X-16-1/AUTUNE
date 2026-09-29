"""Topic-statement extraction — MVP, deliberately not an LLM task.

Embedding-based TextTiling splits the transcript where the conversation turns;
kiwipiepy noun phrases label each segment. What is matched across meetings is the
segment's mean-pooled embedding, so both sides stay consistent as long as they
run the same extractor. See docs/modules/context.md, "Topic-statement extraction
is not an LLM task (MVP)".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from autune_context.pipeline.base import Embedder
    from autune_contracts import Utterance

_CONTENT_POS = {"NNG", "NNP", "SL"}
_VERB_POS = {"VV", "VA", "VX", "VCN", "XSV", "XSA"}
_HONORIFICS = {"님", "씨"}  # 민재 + 님: a person, not an agenda item
# Nouns that are common to every meeting and so name none of them: time words,
# meeting talk, and the verbal nouns that carry a meeting's actions (확정하다,
# 확인하다) rather than its subject. Each one ends a phrase where it stands.
_GENERIC_NOUNS = frozenset(
    """
    오늘 내일 어제 모레 지금 이번 다음 지난 지난번 저번 요번 이제 아까 나중
    이번주 다음주 지난주 이번달 다음달 지난달 주말 오전 오후 요일 날짜
    회의 미팅 안건 얘기 이야기 말씀 생각 의견 질문 답변 부분 정도 내용 경우
    관련 사항 쪽 전체 다들 모두 우리 저희 사람 분들 이거 그거 저거 뭔가
    확인 확정 진행 공유 논의 정리 검토 결정 준비 요청 전달 참고 보고 체크
    """.split()  # noqa: SIM905 - a word list reads as one
)
_WEEKDAY = re.compile(r"[월화수목금토일]요일$")


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

    A transcript too short to segment (< ``2 * min_segment`` utterances) is one
    segment, as is a one-topic meeting. A segment ``_label`` finds no noun in is
    dropped, so a transcript of nothing but small talk yields no topics.
    """
    texts = [u.text for u in utterances]
    if not texts:
        return []

    vectors = np.asarray(embedder.embed(texts), dtype=np.float64)
    boundaries = _texttiling_boundaries(vectors, window, min_segment, depth_threshold)
    spans = _spans_from_boundaries(len(utterances), boundaries, min_segment)

    segments = [_segment(utterances, vectors, start, end) for start, end in spans]
    # A segment with no noun to name it is small talk or a timekeeping aside;
    # linking on it would only connect meetings that both had some.
    return [segment for segment in segments if segment.label]


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
    """The segment's most repeated noun phrase, longest first; ``""`` if it has none.

    Phrases are built from words (whitespace-separated eojeol), not from
    kiwipiepy's morphemes: a word the dictionary lacks -- 온보딩, 레이턴시 -- is
    split into pieces there, and a morpheme-level label names one of the pieces.
    Kiwi decides only whether a word is a noun and where its particle starts.

    A phrase is a run of noun words; a particle closes it, and anything that is
    not a content noun breaks it -- a verb (확정할게요), a number with its
    counter (5장, 2시), a one-letter noun (장, 안), a name with an honorific
    (민재님), a word in ``_GENERIC_NOUNS``. Every contiguous sub-phrase of up to
    ``max_words`` words is a candidate. Only repeated candidates compete when
    there are any, and the score is words x occurrences, so a phrase said twice
    (결제 모듈 연동) beats one of its words said three times (결제). Ties go to
    the longer phrase, then to the one said first.
    """
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for phrase in _noun_phrases(text):
        for size in range(1, min(len(phrase), max_words) + 1):
            for i in range(len(phrase) - size + 1):
                candidate = " ".join(phrase[i : i + size])
                counts[candidate] = counts.get(candidate, 0) + 1
                first_seen.setdefault(candidate, len(first_seen))
    if not counts:
        return ""
    repeated = [c for c in counts if counts[c] > 1] or list(counts)
    return max(
        repeated,
        key=lambda c: (counts[c] * len(c.split()), len(c.split()), -first_seen[c]),
    )


def _noun_phrases(text: str) -> list[list[str]]:
    from kiwipiepy import Kiwi

    tokens = _kiwi(Kiwi).tokenize(text)
    phrases: list[list[str]] = []
    current: list[str] = []
    t = 0
    for match in re.finditer(r"\S+", text):
        while t < len(tokens) and tokens[t].start < match.start():
            t += 1
        word_start = t
        while t < len(tokens) and tokens[t].start < match.end():
            t += 1
        head = _noun_head(match.group(), match.start(), tokens[word_start:t])
        if head is not None:
            current.append(head[0])
        if current and (head is None or head[1]):
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    return phrases


def _noun_head(word: str, offset: int, tokens: list) -> tuple[str, bool] | None:
    """The content noun ``word`` starts with, and whether a particle closes it.

    ``None`` when the word does not start with one -- see ``_label``.
    """
    stem, closed = word, False
    tags: set[str] = set()
    for token in tokens:
        tag = token.tag.split("-")[0]  # VA-I (irregular) -> VA
        if tag in _VERB_POS or (tag == "XSN" and token.form in _HONORIFICS):
            return None
        if tag == "VCP" or (tag.startswith(("J", "E", "S")) and tag not in {"SL", "SN", "SH"}):
            stem, closed = word[: token.start - offset], True
            break
        tags.add(tag)
    if not tags & _CONTENT_POS:
        return None
    # 장, 안, 2배 are too short to name anything; v2.0 is a name
    letters = sum(ch.isalpha() for ch in stem)
    if letters < 2 and not ("SL" in tags and sum(ch.isalnum() for ch in stem) >= 2):
        return None
    if stem in _GENERIC_NOUNS or _WEEKDAY.search(stem):
        return None
    return stem, closed


_KIWI_CACHE: list = []


def _kiwi(kiwi_cls: type):
    if not _KIWI_CACHE:
        _KIWI_CACHE.append(kiwi_cls())
    return _KIWI_CACHE[0]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0
