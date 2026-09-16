"""What a written-Korean model does to spoken Korean, and what to do about it.

Pure functions, no model and no weights, for the same reason ``graph`` is pure:
these are judgements about what belongs in the topic graph, and a judgement
that can only be exercised by loading a 500 MB pipeline is a judgement nobody
tests. ``ner.SpacyNer`` feeds them what the pipeline produced.

Both of them exist because of one measurement. Run over the two shared
fixtures, ``ko_core_news_lg`` 3.8.0 finds six entities in
``transcript_ready.typical`` — ``오늘은``, ``한번``, ``A``, ``B``,
``다음 주 화요일까지``, ``010-****-5678`` — and one in
``transcript_ready.short``: ``네,``. Every topic the meetings are actually
about (``실시간 개인화``, ``인기순 정렬``, ``콜드스타트``, ``검색 개인화 기능``,
``응답 시간``) is a plain noun the NER never sees, and half of what it does
find is noise. A gap report built on that graph would be about ``한번`` and
``A``. See docs/modules/gap.md, "Step 1 as built".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MAX_TERM_TOKENS = 4
"""How many tokens a run may join into one topic label.

A cap rather than no limit: a list read out loud ("검색 정렬 추천 알림 필터
전부") is one unbroken noun run, and without a cap the whole list becomes a
single node no reader recognises. Four is the longest compound the product
vocabulary actually uses ("검색 개인화 기능 개선")."""

MIN_TERM_CHARS = 2
"""A one-character noun is a counter or a bound noun — 주, 것, 수, 번 — not a
topic. Multi-token runs are exempt: "다음 주" is already broken by the
stoplist, and a run that survived it has a real noun in it."""

_NOUN_TAG_PREFIXES = ("n", "f")
"""Morpheme tags this counts as a noun.

``n*`` is the noun family of the Sejong tagset ``ko_core_news_lg`` emits:
``ncn`` (common), ``ncpa`` (predicative), ``nq`` (proper), ``nbn`` (bound),
``npd``/``npp`` (pronoun). ``f`` is "foreign", which is where an English
product term lands — ``API``, ``QA``, ``Slack``. Pronouns and bound nouns come
in so a run is *broken* correctly by the stoplist and the length floor rather
than silently glued to its neighbour."""

_NOUN_SUFFIX_TAG = "xsn"
"""A noun-forming suffix, as in ``실시간`` (``ncpa+xsn``). A token that is a
noun plus this is still a bare noun; anything else after a ``+`` — a particle
(``j*``), an ending (``e*``), a predicate (``p*``) — means the token carries
grammar, and joining it into a label would put "개인화로" in a report where
"개인화" belongs."""

STOP_TERMS: frozenset[str] = frozenset(
    {
        # Time deixis. A meeting says these constantly and none of them is a
        # topic; the date NER already carries when something is due.
        "오늘",
        "내일",
        "어제",
        "모레",
        "지금",
        "이번",
        "지난",
        "다음",
        "요즘",
        "최근",
        "당시",
        "나중",
        "아까",
        # Discourse nouns. What a speaker says *about* the topic, never the
        # topic: "그 부분은 다음에 얘기하죠".
        "얘기",
        "이야기",
        "생각",
        "부분",
        "경우",
        "정도",
        "관련",
        "때문",
        "자체",
        "느낌",
        "상황",
        "내용",
        "사람",
        "우리",
        "저희",
        "한번",
        "문제",
    }
)
"""Nouns that break a run rather than joining it.

They are dropped for being *frequent and contentless in speech*, not for being
unimportant words — "문제" is here because "그게 문제인데" is how Korean says
"but", and a node called 문제 would be the most central topic of every meeting.
A term that matters will appear beside one of these, not as one.

This list is tuned by what dismissals say later (#35). It is deliberately
short: every entry is a topic the graph can no longer raise a gap about, and
that is the expensive direction of the two."""


@dataclass(frozen=True)
class Token:
    """One token as the pipeline tagged it, with where it sits in the text.

    ``tag`` is the morpheme analysis (``ncn+jxt``), not the coarse part of
    speech: coarse tags call ``개인화로`` an adverb and ``실시간은`` a noun,
    which is true of the whole token and useless for deciding whether the noun
    inside it can be joined to its neighbour.
    """

    text: str
    tag: str
    start: int
    end: int


def is_bare_noun(tag: str) -> bool:
    """Whether every morpheme in ``tag`` is a noun or a noun-forming suffix."""
    parts = tag.split("+")
    return all(
        part.startswith(_NOUN_TAG_PREFIXES) or part == _NOUN_SUFFIX_TAG for part in parts
    ) and bool(parts[0])


def noun_terms(
    tokens: Sequence[Token], claimed: Iterable[tuple[int, int]] = ()
) -> list[tuple[int, str]]:
    """Maximal runs of bare nouns, as ``(start, label)`` in text order.

    The offset comes back because the caller interleaves these with the
    entities the NER found, and the order a meeting said things in is what
    decides the label a topic keeps.

    This is the half of entity extraction a general Korean model cannot do:
    ``feature`` and ``system`` are this product's vocabulary and the model has
    no label for either, so the things a product meeting is *about* arrive as
    ordinary nouns. A run of them is the compound the speaker said —
    "검색 개인화 기능", "응답 시간", "콜드스타트" — and that is what the graph
    needs a node for. Which of the two kinds it is stays undecided (the label
    is ``term``); deciding it is what #13's trained model is for.

    A run breaks on anything that is not a bare noun, on a stopword, and on a
    span some entity already claimed — ``claimed`` carries those as character
    ranges. One character belongs to at most one thing, the rule ``FakeNer``
    already follows: without it "다음 주 화요일까지" is a date *and* the tail of
    a noun run, and the graph grows a node nobody can point at in the
    transcript.

    Runs are capped at ``MAX_TERM_TOKENS`` from the left, so an over-long list
    still yields its first compound instead of nothing.
    """
    ranges = list(claimed)
    terms: list[tuple[int, str]] = []
    run: list[Token] = []

    def flush() -> None:
        while run:
            take = run[:MAX_TERM_TOKENS]
            del run[: len(take)]
            text = " ".join(token.text for token in take)
            if len(take) > 1 or len(text) >= MIN_TERM_CHARS:
                terms.append((take[0].start, text))

    for token in tokens:
        overlaps = any(token.start < end and start < token.end for start, end in ranges)
        if not is_bare_noun(token.tag) or token.text in STOP_TERMS or overlaps:
            flush()
            continue
        run.append(token)
    flush()
    return terms


def is_plausible(label: str, text: str) -> bool:
    """Whether an entity the model found is worth a node.

    Two rules, both from the same measurement, both about precision — C's
    metric is precision and a false topic is what a false gap is raised on.

    - **A one-character person is not a person.** ``A/B 결과`` gives ``A`` and
      ``B`` as ``PS``, and so do ``A안``/``B안``. A meeting transcript has no
      one-letter names in it, and two of them became the most connected nodes
      of the ``typical`` fixture's graph.
    - **A metric without a number is not a metric.** ``QT`` is the quantity
      label, and on spoken Korean it fires on ``한번``, ``네,``, ``좀``. What
      makes a quantity worth graphing is the quantity: "응답 3초", "95%".

    Neither rule looks at the *content* of a span beyond its shape, and no
    caller logs the text it rejects — the span is the meeting's, not the
    model's vocabulary.
    """
    stripped = text.strip()
    if label == "person":
        return len(stripped) > 1
    if label == "metric":
        return any(character.isdigit() for character in stripped)
    return bool(stripped)
