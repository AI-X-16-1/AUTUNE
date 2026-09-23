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

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from autune_integrations.privacy import MASK_CHAR

_MASK_BODY = r"0-9A-Za-z_@.+)\-–—"
"""What module A leaves standing *inside* a masked value, apart from the mask.

Read off what the masker writes (``autune_audio.masking._hide``): digits and
its separators for a number (``010-****-5678``), the first character and the
whole domain for an address (``k***@example.com``), and nothing else — a span
written in two scripts is hidden whole, so no Hangul survives inside one. The
separators are ``masking._SHAPE_CHARS`` minus its three spaces, which cannot
appear in a chunk this pattern bounds by whitespace anyway.

Horizontal space is left out for a second reason: ``privacy._EDGE`` is the
masker's own statement of what ends a number, and it is this class without the
punctuation.
"""

_MASKED_CHUNK = re.compile(
    rf"(?:[가-힣]|[{_MASK_BODY}]*){re.escape(MASK_CHAR)}+[{_MASK_BODY}{re.escape(MASK_CHAR)}]*"
)
"""One masked value, bounded by what could have been part of it.

``MASK_CHAR`` is imported rather than spelled here. Module A masks with the
patterns in ``autune_integrations.privacy``, and this module only recognises
what they leave behind — a copy of the character is a guard that stops matching
the day the notation changes, without saying so. It was written out twice in
this module before (#250).

**The bound is not whitespace.** It was, and that was wrong in the direction
this whole change exists to fix: Korean runs words together, and
``번호010-****-5678이에요`` claimed 번호 as well — a real noun, lost beside a
masked value exactly as #250 describes, with a missing space instead of a
missing particle. Raised in review of #250 by @lsh2217, who reproduced it.

**The single leading Hangul syllable is the one thing a masked value keeps in
this script.** A name, a place or an address is hidden as ``value[:1]`` plus
masks — 김민경 becomes 김** — so one syllable may stand immediately before the
mask, and no more. ``고객김**`` therefore claims 김** and leaves 고객 to be a
topic.

*Tradeoff noted, not resolved:* importing from ``autune_integrations`` runs that
package's ``__init__`` and with it the Slack, Notion, Jira and Calendar clients,
which is a heavier import than this module's "pure functions, no model" claim
suggests. The alternative is a local copy pinned to the masker's by a test,
which trades the guarantee for the import; ``packages/integrations`` is shared
and re-shaping its ``__init__`` needs its own decision. Raised in review of
#250.
"""

MAX_TERM_TOKENS = 4
"""How many tokens a run may join into one topic label.

A cap rather than no limit: a list read out loud ("검색 정렬 추천 알림 필터
전부") is one unbroken noun run, and without a cap the whole list becomes a
single node no reader recognises. Four is the longest compound the product
vocabulary actually uses ("검색 개인화 기능 개선")."""

MIN_TERM_CHARS = 2
"""A one-character noun is a counter — 주, 번, 명 — not a topic.

A **run** qualifies when at least one of its tokens clears this, which is the
rationale spelled out: "a run that survived the stoplist has a real noun in
it". It used to be assumed of any multi-token run, and 주 번 — two counters,
neither in the stoplist — became a topic on that assumption. Raised in review
of #222."""

_NOUN_TAG_PREFIXES = ("nc", "nq", "f")
"""Morpheme tags this counts as a noun: the **content** nouns only.

``nc*`` is the common noun (``ncn``, ``ncpa``), ``nq`` the proper noun, and
``f`` is "foreign", which is where an English product term lands — ``API``,
``QA``, ``Slack``.

The rest of the ``n`` family is deliberately out, because matching here means
*joining a run*, not breaking one:

- **Pronouns** (``npd``, ``npp``) — 그거, 이거, 여기. They recur in most spoken
  utterances, and two characters is enough to clear the length floor, so 그거
  would be a node of nearly every meeting. Worse, 그거 검색 기능 would join into
  one run and split ``검색 기능`` into two topics by ``topic_key``.
- **Numerals** (``nnc``, ``nno``) — 두 가지 방법 is a quantity of methods, not
  a topic called "두 가지 방법".
- **Bound nouns** (``nb*``) — 것, 수, 가지. They cannot stand alone by
  definition, and inside a run they add a word no reader would recognise.

Raised in review of #222: the earlier version claimed these were let in "so a
run is broken correctly", which is backwards — a tag that matches is a tag that
joins."""

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
        # Demonstratives. The tag rule cannot break these: ``ko_core_news_lg``
        # tags 그거 as ``ncn``, an ordinary common noun, not as the pronoun
        # ``npd`` it is — so "그거 검색 기능" joins into one run and splits the
        # topic the meeting calls 검색 기능 everywhere else. Raised in review
        # of #222, where the tag rule alone was expected to cover them.
        "그거",
        "이거",
        "저거",
        "그것",
        "이것",
        "저것",
        "여기",
        "거기",
        "저기",
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
        # Contrast markers. ``ko_core_news_lg`` tags 대신 and 말고 as ordinary
        # common nouns, so a run swallows them: "인기순 정렬 대신 실시간
        # 개인화로" came back as one topic called 인기순 정렬 대신 실시간 —
        # two topics and the word between them, welded into a node no reader
        # recognises. Found while measuring step 2 (#32), where the same word
        # is the marker that makes the pair ``alternative_to``: a marker
        # swallowed by a label is a relation the rules can never see.
        "대신",
        "말고",
        "반면",
        # Cue words. The same argument one step further: these are what step 2
        # keys on, and the model tags them as ordinary nouns. "정렬 로직은
        # 인덱스가 필요 없습니다" came back with a topic called 필요, and a
        # graph whose central node is 필요 raises gaps about nothing. Raised in
        # review of #249.
        "필요",
        "이슈",
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


def masked_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges module A masked, as ``claimed`` ranges for ``noun_terms``.

    A masked value is not a topic and the graph already refuses one — the
    ``MASK_CHAR`` check in ``graph.build_topics`` is the last line and stays
    where it is. What that check cannot do is give back what was lost on the
    way: when the mask lands inside a noun run, the run carries it, and the run
    is dropped whole, together with the real topic beside it.

        고객 연락처 010-****-5678 확인 부탁
            without this  ->  one run, dropped, and 고객 연락처 goes with it
            with it       ->  고객 연락처, 확인 부탁

    Whether that happens is decided by whether a particle happens to sit
    between the noun and the masked value: "연락처는 010-****-5678 입니다"
    breaks the run by itself and keeps its topic, "연락처 010-****-5678" does
    not. That is a property of how the speaker phrased it and says nothing
    about what the meeting was about — and C reports on what is *absent* from
    the graph, so a topic lost here comes back to the reader as "논의되지
    않았다". #250.

    Not a privacy fix. Nothing masked reached a node before this and nothing
    does after it; the direction of the bug is loss, and the cost is a false
    gap on every meeting where somebody read out a number.
    """
    if MASK_CHAR not in text:
        # Most utterances in most meetings. Raised in review of #250.
        return []
    return [match.span() for match in _MASKED_CHUNK.finditer(text)]


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
    transcript. ``ner`` seeds ``claimed`` with ``masked_spans`` for the second
    half of the same rule — see there for why the model cannot be relied on to
    claim them itself.

    Runs are capped at ``MAX_TERM_TOKENS`` from the left: an over-long run
    yields its first compound and the rest is dropped, rather than being cut
    into further chunks. A second chunk's boundary is the fourth token and
    nowhere the speaker paused, so it would be a node no reader recognises —
    and a node nobody recognises is what a false gap gets raised on. Raised in
    review of #222, where the code chunked and this paragraph said it did not.
    """
    ranges = list(claimed)
    terms: list[tuple[int, str]] = []
    run: list[Token] = []

    def flush() -> None:
        take = run[:MAX_TERM_TOKENS]
        run.clear()
        if not take or not any(len(token.text) >= MIN_TERM_CHARS for token in take):
            return
        terms.append((take[0].start, " ".join(token.text for token in take)))

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
