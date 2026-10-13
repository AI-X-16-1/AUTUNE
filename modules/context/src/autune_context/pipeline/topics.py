"""Topic-statement extraction — MVP, deliberately not an LLM task.

Embedding-based TextTiling splits the transcript where the conversation turns;
kiwipiepy noun phrases label each segment. What is matched across meetings is the
segment's mean-pooled embedding, so both sides stay consistent as long as they
run the same extractor. See docs/modules/context.md, "Topic-statement extraction
is not an LLM task (MVP)".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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
# Nouns a meeting says to open, close, thank or console, whatever it is about:
# "시작합시다", "다음 시간에 이어서 하죠", "수고 많으셨어요", "그나마 다행이에요".
# Generic in the same sense as the list above, kept apart because they are
# social rather than about the meeting's business.
_SOCIAL_NOUNS = frozenset(
    "시간 시작 마무리 수고 고생 감사 다행 축하 죄송 부탁 참석 안녕 환영".split()  # noqa: SIM905
)
_WEEKDAY = re.compile(r"[월화수목금토일]요일$")
# A word that is only an honorific and its particle: the 님 of "도윤 님," or the
# 씨 of "수빈 씨가", written apart from the name.
_DETACHED_HONORIFIC = re.compile(r"^(?:님|씨)[가-힣]{0,2}[^\w]*$")
_NAME_WORD = re.compile(r"^[가-힣]{2,4}$")
# Titles a name goes before: 한서진 in "한서진 팀장님". Only titles -- 고객님 is no
# title, and "신규 고객님" must not make 신규 a name.
_TITLES = frozenset(
    "과장 차장 부장 팀장 실장 본부장 대표 사장 이사 원장 교수 선생 선배 매니저 대리 주임".split()  # noqa: SIM905
)


@dataclass(frozen=True)
class TopicSegment:
    label: str
    text: str
    utterance_ids: list[str]
    vector: list[float]
    names: frozenset[str] = frozenset()
    """The meeting's ``person_names``, so what is read off ``text`` later
    (``keyphrases``, ``content_nouns``) leaves them out the way the label did."""


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

    Openings, closings and bare acknowledgements -- "자, 모였으면
    시작합시다.", "없으면 목요일에 다시 모이죠. 수고하셨습니다.", "네." -- are left
    out before the transcript is split (``_carries_no_topic``). Kept, they
    either became a segment of its own (two meetings' closings then linked each
    other) or were averaged into the topic next to them, pulling every
    meeting's vectors towards the same openings and closings. The people named
    in the meeting (``person_names``) never label a segment.
    """
    names = person_names(u.text for u in utterances)
    utterances = [u for u in utterances if not _carries_no_topic(u.text, names)]
    texts = [u.text for u in utterances]
    if not texts:
        return []

    vectors = np.asarray(embedder.embed(texts), dtype=np.float64)
    boundaries = _texttiling_boundaries(vectors, window, min_segment, depth_threshold)
    spans = _spans_from_boundaries(len(utterances), boundaries, min_segment)

    segments = [_segment(utterances, vectors, start, end, names) for start, end in spans]
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
    utterances: list[Utterance],
    vectors: np.ndarray,
    start: int,
    end: int,
    names: frozenset[str] = frozenset(),
) -> TopicSegment:
    chunk = utterances[start:end]
    mean = vectors[start:end].mean(axis=0)
    norm = np.linalg.norm(mean) or 1.0
    text = " ".join(u.text for u in chunk)
    return TopicSegment(
        label=_label(text, names=names),
        text=text,
        utterance_ids=[u.id for u in chunk],
        vector=(mean / norm).tolist(),
        names=names,
    )


def _label(text: str, *, names: frozenset[str] = frozenset(), max_words: int = 4) -> str:
    """The segment's most repeated noun phrase, longest first; ``""`` if it has none.

    Phrases are built from words (whitespace-separated eojeol), not from
    kiwipiepy's morphemes: a word the dictionary lacks -- 온보딩, 레이턴시 -- is
    split into pieces there, and a morpheme-level label names one of the pieces.
    Kiwi decides only whether a word is a noun and where its particle starts.

    A phrase is a run of noun words; a particle closes it, and anything that is
    not a content noun breaks it -- a verb (확정할게요), a number with its
    counter (5장, 2시), a one-letter noun (장, 안), a name with an honorific
    (지우님) or one of ``names`` (도윤 in "도윤 님,"), an adverbial noun in -적
    (기본적으로), a word in ``_GENERIC_NOUNS`` or ``_SOCIAL_NOUNS``. Every
    contiguous sub-phrase of up to
    ``max_words`` words is a candidate. The score is words x occurrences among
    the repeated candidates, so a phrase said twice (결제 모듈 연동) beats one of
    its words said three times (결제). Ties go to the longer phrase, then to the
    one said first.

    When nothing repeats, frequency says nothing, and the label is the
    segment's first phrase: a segment starts where the conversation turned,
    and the turn is usually someone raising the subject ("민구님 디자인 쪽은
    어떤가요?"). Picking the longest phrase instead named whatever happened to
    be said in two words (마무리 단계), and picking the phrase closest to the
    segment's embedding was tried and chose generic words (예상, 변경, 완료) --
    on a real meeting every candidate scored within 0.04 of the others.
    """
    phrases = _noun_phrases(text, names)
    counts, first_seen = _phrase_counts(phrases, max_words)
    repeated = [c for c in counts if counts[c] > 1]
    if not repeated:
        return " ".join(phrases[0][:max_words]) if phrases else ""
    return max(
        repeated,
        key=lambda c: (counts[c] * len(c.split()), len(c.split()), -first_seen[c]),
    )


def keyphrases(text: str, names: frozenset[str] = frozenset(), *, k: int = 5) -> list[str]:
    """The ``k`` best noun phrases of ``text``, by ``_label``'s score, none
    contained in another -- what the segment is about, without the register
    and small words its embedding also carries. Phrases said once rank after
    every repeated one, in the order they were said."""
    counts, first_seen = _phrase_counts(_noun_phrases(text, names), 4)
    ranked = sorted(
        counts,
        key=lambda c: (-counts[c] * len(c.split()), -len(c.split()), first_seen[c]),
    )
    chosen: list[str] = []
    for candidate in ranked:
        if any(candidate in other or other in candidate for other in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) == k:
            break
    return chosen


def content_nouns(text: str, names: frozenset[str] = frozenset()) -> set[str]:
    """Every word of every noun phrase in ``text`` -- see ``_label``."""
    return {word for phrase in _noun_phrases(text, names) for word in phrase}


def _phrase_counts(
    phrases: list[list[str]], max_words: int
) -> tuple[dict[str, int], dict[str, int]]:
    """Occurrences and first position of every contiguous sub-phrase of up to
    ``max_words`` words."""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for phrase in phrases:
        for size in range(1, min(len(phrase), max_words) + 1):
            for i in range(len(phrase) - size + 1):
                candidate = " ".join(phrase[i : i + size])
                counts[candidate] = counts.get(candidate, 0) + 1
                first_seen.setdefault(candidate, len(first_seen))
    return counts, first_seen


def _carries_no_topic(text: str, names: frozenset[str]) -> bool:
    """An utterance with no noun to name anything that is also a bare
    acknowledgement (two words or fewer: "네.", "없습니다.") or says one of
    ``_SOCIAL_NOUNS`` (시작합시다, 수고하셨습니다, 다음 시간에 하죠).

    Both conditions, not the first alone: "매월 5일까지 제출해야 한다" and "늦으면
    다음 달로 넘어간다" have no such noun either, and they are what a deadline
    discussion is made of -- dropping them moved real topics' vectors enough to
    lose two links on the v3 set.
    """
    if _noun_phrases(text, names):
        return False
    if len(text.split()) <= 2:
        return True
    from kiwipiepy import Kiwi

    return any(
        t.tag.startswith("NN") and t.form in _SOCIAL_NOUNS for t in _kiwi(Kiwi).tokenize(text)
    )


def person_names(texts: Iterable[str]) -> frozenset[str]:
    """The words a meeting uses as somebody's name.

    A name is what an honorific is attached to. Written together (지우님,
    팀장님이고) the word is already no noun to ``_noun_head``; written apart --
    "도윤 님,", "수빈 씨가", which is how a transcript often spaces it -- the name
    is a plain noun word, and a label could be it. So: the word before a
    detached honorific, and the word before a title that carries one (한서진 in
    "한서진 팀장님"; _TITLES), when it is two to four Hangul syllables that
    kiwipiepy reads as one proper noun. The proper-noun test keeps a department
    before a title (개발 팀장님, 영업 이사님) and a common noun before a detached
    님 (고객 님께) out; the honorific must be its own morpheme, so 씨앗 is no 씨.
    Collected over the whole meeting, because a name said with 님 once is said
    without it later.
    """
    from kiwipiepy import Kiwi

    names: set[str] = set()
    for text in texts:
        words = [(m.group(), m.start(), toks) for m, toks in _words(text, _kiwi(Kiwi))]
        for (prev, _, prev_tokens), (word, _, tokens) in zip(words, words[1:], strict=False):
            detached = (
                _DETACHED_HONORIFIC.match(word) is not None
                and bool(tokens)
                and tokens[0].form in _HONORIFICS
            )
            honorific = detached or any(
                t.tag.startswith("XSN") and t.form in _HONORIFICS and prev_t.form in _TITLES
                for prev_t, t in zip(tokens, tokens[1:], strict=False)
            )
            stem = prev.rstrip(",.!?~")
            proper = (
                bool(prev_tokens) and prev_tokens[0].tag == "NNP" and prev_tokens[0].form == stem
            )
            if honorific and proper and _NAME_WORD.match(stem):
                names.add(stem)
    return frozenset(names)


def _words(text: str, kiwi) -> list[tuple[re.Match[str], list]]:
    """Each whitespace-separated word of ``text`` with kiwipiepy's tokens in it."""
    tokens = kiwi.tokenize(text)
    words = []
    t = 0
    for match in re.finditer(r"\S+", text):
        while t < len(tokens) and tokens[t].start < match.start():
            t += 1
        word_start = t
        while t < len(tokens) and tokens[t].start < match.end():
            t += 1
        words.append((match, tokens[word_start:t]))
    return words


def _noun_phrases(text: str, names: frozenset[str] = frozenset()) -> list[list[str]]:
    from kiwipiepy import Kiwi

    phrases: list[list[str]] = []
    current: list[str] = []
    for match, tokens in _words(text, _kiwi(Kiwi)):
        head = _noun_head(match.group(), match.start(), tokens, names)
        if head is not None:
            current.append(head[0])
        if current and (head is None or head[1]):
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    return phrases


def _noun_head(
    word: str, offset: int, tokens: list, names: frozenset[str] = frozenset()
) -> tuple[str, bool] | None:
    """The content noun ``word`` starts with, and whether a particle closes it.

    ``None`` when the word does not start with one -- see ``_label``.
    """
    stem, closed = word, False
    tags: set[str] = set()
    for token in tokens:
        tag = token.tag.split("-")[0]  # VA-I (irregular) -> VA
        if tag in _VERB_POS or (tag == "XSN" and token.form in {*_HONORIFICS, "적"}):
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
    if stem in _GENERIC_NOUNS or stem in _SOCIAL_NOUNS or stem in names:
        return None
    if _WEEKDAY.search(stem):
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
