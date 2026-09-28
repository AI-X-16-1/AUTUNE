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

_MASKED_CHUNK = re.compile(rf"\S*{re.escape(MASK_CHAR)}+\S*")
"""A whitespace-delimited chunk with a mask character somewhere in it.

``MASK_CHAR`` is imported rather than spelled here. Module A masks with the
patterns in ``autune_integrations.privacy``, and this module only recognises
what they leave behind — a copy of the character is a guard that stops matching
the day the notation changes, without saying so. It was written out twice in
this module before (#250).

The shape is module A's own reading of a masked token: a run of non-space
characters containing the mask. ``010-****-5678`` is one chunk because the
speaker said one number, and the digits either side of the mask are the part
that survived it.
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

RULES_VERSION = "spoken-2"
"""The version of the judgements in this file, appended to the extractor's.

``ko_core_news_lg-3.8.0`` names the weights, and the weights are half of what
decides the graph: the same parse gives a different set of topics once
``noun_stem`` reads through a particle. A graph built before that change has
to be tellable from one built after it, or precision measured across the two
is two extractors averaged together. Bump it whenever a rule here changes what
``noun_terms`` or ``is_plausible`` returns for the same tokens."""

_ALLOMORPHS: tuple[tuple[str, str], ...] = (
    ("은", "는"),
    ("이", "가"),
    ("을", "를"),
    ("과", "와"),
    ("이랑", "랑"),
    ("이나", "나"),
    ("이나마", "나마"),
    ("이라도", "라도"),
    ("이야말로", "야말로"),
    ("이든지", "든지"),
    ("이든가", "든가"),
    ("은커녕", "는커녕"),
    ("으로", "로"),
    ("으로서", "로서"),
    ("으로써", "로써"),
)
"""Particles whose spelling depends on the noun, as (after a consonant, after a
vowel). 서버는 but 모델은; 서버로 but 폴백으로.

The ``으로`` family splits differently: 로 follows a vowel **or** ㄹ (모델로),
and 으로 every other consonant. ``_fits`` holds that exception.

Standard Korean grammar, not a measurement — which is why it can serve as a
check on a cut. 차이랑 ends in the letters of 이랑, and cutting them would leave
차; 이랑 only follows a consonant and 차 has none, so the cut is 랑 and the noun
is 차이."""

_INVARIANT_PARTICLES: tuple[str, ...] = (
    # Case particles (격조사).
    "의",
    "에",
    "에서",
    "에게",
    "에게서",
    "한테",
    "한테서",
    "께",
    "께서",
    "더러",
    "에다",
    "에다가",
    "하고",
    "처럼",
    "만큼",
    "보다",
    "같이",
    # Auxiliary particles (보조사).
    "도",
    "만",
    "까지",
    "부터",
    "마저",
    "조차",
    "밖에",
    "대로",
    "뿐",
    "마다",
    "커녕",
    "치고",
)
"""Particles spelled the same after any noun.

Together with ``_ALLOMORPHS`` this is the standard inventory of case and
auxiliary particles, walked once rather than grown by what was seen (#345).
Left out on purpose: the vocative (아, 야, 여) and the informal 이야/야, which
a noun that names a topic does not take in a meeting, and the ending-like 요,
서, 며, whose single syllable would be the longest match on too many words
that are not particles at all. The copula is not here either — ``noun_stem``
refuses a ``jp`` tag outright."""

_STACK_HEADS: tuple[str, ...] = (
    "에",
    "에서",
    "에게",
    "에게서",
    "한테",
    "한테서",
    "께서",
    "에다",
    "에다가",
    "하고",
    "처럼",
    "만큼",
    "보다",
    "같이",
    "까지",
    "부터",
    "마저",
    "조차",
    "밖에",
    "대로",
    "마다",
    "으로",
    "으로서",
    "로서",
    "으로써",
    "로써",
    "이랑",
)
"""Particles another particle stacks onto, when they are the first of the two.

Each is two syllables or more, save 에, which no noun a meeting names ends in
(화면에도, 캐시에만). ``ko_core_news_lg`` does not tag a stack
consistently: 결과까지도 is ``jxc+jxc`` but 모듈까지도 is one ``jxc``, and
캐시처럼은 and 고객만큼은 are one ``jxt`` each. A stack the model tagged as one
morpheme gets one cut (see ``noun_stem``), so these pairs are on the list
whole.

같이 is here for a second reason: it ends in 이, so without 같이나 on the list
the longest match in 서버같이나 is 이나, and the stem is 서버같. Found by
``test_a_stem_never_ends_in_a_listed_particle``.

Any other one-syllable head — 로, 과, 만 — is not, because a noun can end in that
syllable: 결과, 경로, 불만. With 과도 on the list, 결과도 would be cut to 결.
Those stacks are cut one particle at a time instead, and only as far as the
tag has particles for (서버로는 is ``jca+jxt``, two). Where the model gives a
one-syllable-headed stack a single tag — 결과만은 is ``jxt`` — the cut stops
one short and leaves 결과만. That is the direction chosen: a label one syllable
too long still contains the noun, and one syllable too short is contained by
words that have nothing to do with it (불 is in 불만, 불가, 불안)."""

_STACK_TAILS: tuple[tuple[str, str], ...] = (
    ("은", "는"),
    ("이", "가"),
    ("을", "를"),
    ("이나", "나"),
    ("이라도", "라도"),
    ("의", "의"),
    ("도", "도"),
    ("만", "만"),
    ("까지", "까지"),
    ("부터", "부터"),
    ("마저", "마저"),
    ("조차", "조차"),
)
"""What stacks onto a ``_STACK_HEADS`` particle, as (after a consonant, after a
vowel): 까지는, 처럼은, 에서부터, 까지도, 조차도."""

_RIEUL = 8
"""ㄹ's index among the final consonants of a Hangul syllable."""


def _final_consonant(syllable: str) -> int | None:
    """The index of ``syllable``'s final consonant, 0 for none, or ``None`` when
    it is not a Hangul syllable (API, QA — nothing is known about those)."""
    code = ord(syllable) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return code % 28


def _fits(ending: str, before: str) -> bool:
    """Whether ``ending`` can follow the syllable ``before``, per
    ``_ALLOMORPHS``. A particle with one spelling fits anything.

    Compared by prefix, so a stack is held to its first particle's rule:
    이랑은 follows a consonant because 이랑 does. Every longer spelling in the
    table begins with a shorter one that takes the same side (이 / 이랑,
    로 / 로서), which is what makes the first match the right one."""
    final = _final_consonant(before)
    if final is None:
        return True
    for after_consonant, after_vowel in _ALLOMORPHS:
        takes_rieul = after_vowel.startswith("로")
        if ending.startswith(after_consonant):
            return final != 0 and not (takes_rieul and final == _RIEUL)
        if ending.startswith(after_vowel):
            return final == 0 or (takes_rieul and final == _RIEUL)
    return True


def _stack(head: str) -> list[str]:
    """``head`` with each of ``_STACK_TAILS`` on it, in the spelling its last
    syllable takes."""
    after_consonant = bool(_final_consonant(head[-1]))
    tails = [consonant if after_consonant else vowel for consonant, vowel in _STACK_TAILS]
    return [head + tail for tail in tails if tail != head]


PARTICLES: dict[str, int] = dict(
    sorted(
        {
            **{particle: 1 for pair in _ALLOMORPHS for particle in pair},
            **{particle: 1 for particle in _INVARIANT_PARTICLES},
            **{stacked: 2 for head in _STACK_HEADS for stacked in _stack(head)},
            # Not a particle, but what ``ko_core_news_lg`` tags as one after a
            # noun: 콜드스타트입니다 comes back ``ncn+ncpa+jxc``. When the
            # model tags it as the copula it is (``jp``) the token is not read
            # through at all — see ``noun_stem``.
            "입니다": 1,
        }.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
"""What ``noun_stem`` cuts off the end of a token, longest first, with how many
particles each ending is.

The cut is made on the **surface**, not along the model's morphemes. The tag
decides *whether* a particle is attached and how many; this list decides
*what* they were. The morpheme split (``lemma_``) cannot be trusted for the
second question on this vocabulary: it gives 개인화로 as 개인 + 화로 and
콜드스타트입니다 as 콜드 + 스타트입니다 — a wrong topic that looks right. #278.

**This list is load-bearing, and nothing structural stands behind it.** An
ending that is not on it is still cut wherever a shorter ending on it matches:
the first version of this list had no 밖에, 대로 or 라도, and 인덱스밖에,
계획대로 and 캐시라도 came out as 인덱스밖, 계획대 and 캐시라 — each a label
that looks like a noun and matches by containment (#345). Re-tagging the cut
stem does not catch that; it was measured and passes 계획대 as ``ncn``. What
does catch part of it is ``_fits``: a cut that leaves a syllable the particle
cannot follow is not taken; ``_AMBIGUOUS_ENDINGS`` and ``_CASE_LEFTOVERS``
refuse the rest that were found. Only an ending that matches nothing on the
list, not even its last syllable, is sure to cost the topic and invent
nothing."""

_CASE_LEFTOVERS: tuple[str, ...] = (
    "에",
    "에서",
    "에게",
    "한테",
    "께",
    "으로",
    "까지",
    "부터",
    "보다",
    "처럼",
    "마다",
    "만큼",
    "이랑",
    "하고",
)
"""Particles no noun a meeting names ends in.

A stem still ending in one of these after every cut the tag allowed is
refused: 서버에서 left over from an unlisted 서버에서나마 means the list was
missing a stack, not that the noun is called that. The net under
``PARTICLES`` for the stacks it does not generate."""

_AMBIGUOUS_HEADS: tuple[str, ...] = ("로", "과", "와", "랑", "만")
"""One-syllable particles another stacks onto, which nouns also end in: 경로,
결과, 사랑, 불만. See ``_AMBIGUOUS_ENDINGS``."""

_AMBIGUOUS_ENDINGS: frozenset[str] = frozenset(
    {stacked for head in _AMBIGUOUS_HEADS for stacked in _stack(head)} | {"라도", "대로"}
)
"""Endings a particle and a noun's own last syllable spell alike, where the tag
does not tell them apart either.

``ko_core_news_lg`` gives both readings one ``j``:

    개인화로는  ncn+jxt   개인화 + 로는      경로는    ncn+jxt   경로 + 는
    서버와도    ncn+jxc   서버 + 와도        성과도    ncn+jxc   성과 + 도
    배포만은    ncn+jxt   배포 + 만은        미만은    ncn+jxt   미만 + 은
    캐시라도    ncn+jxc   캐시 + 라도        인프라도  ncn+jxc   인프라 + 도
    일정대로    ncn+jca   일정 + 대로        무대로    ncn+jca   무대 + 로

Either cut invents a topic on the other half — 경, 개인화로, 서버와, 성,
배포만, 인프 — so the token is refused when the cut would be its last, unless
what is left is in ``_NOUNS_ENDING_LIKE_A_PARTICLE``. Where the tag has a
``j`` for each particle the count settles it and the cut is made: 서버로는
(``jca+jxt``) is 서버, and 서버와는 (``jct+jxt``) is 서버.

The stacks are those of ``_AMBIGUOUS_HEADS``, generated the way ``PARTICLES``
generates its own; 라도 and 대로 are the single particles with the same
problem. 야말로 is not here: ``_fits`` settles it. 카메라야말로 can only be
카메라 + 야말로, and 모델이야말로 only 모델 + 이야말로."""

_NOUNS_ENDING_LIKE_A_PARTICLE: tuple[str, ...] = (
    "경로",
    "회로",
    "인프라",
    "결과",
    "효과",
    "성과",
    "불만",
    "미만",
)
"""Nouns whose last syllable is a particle, measured, so they keep their topic.

``_AMBIGUOUS_ENDINGS`` refuses a token rather than guess which reading it is,
and these are the words that refusal costs most often in this product's
meetings: 경로는, 결과는, 불만은 would otherwise name nothing. A token that is
one of these (as its last word) plus a listed particle is that noun. Tuned the
way ``STOP_TERMS`` is — what has been seen, not every noun that ends in 과 or
로."""

_PARTICLE_LOOKALIKES: frozenset[str] = frozenset({"재시도", "난이도"})
"""Nouns the model tags as a shorter noun plus a particle.

``ko_core_news_lg`` gives a bare 재시도 as ``ncpa+jxc`` — 재시 and the particle
도 — and 난이도 as ``ncn+jcs``. Read through, those become topics called 재시
and 난이, and ``detect.match`` compares by containment, so 재시 would count as
the meeting having covered a template keyword 재시도. Measured on the words
this product's meetings use, and tuned the way ``STOP_TERMS`` is: it holds
what has been seen, not a guess at every 度 noun (정확도, 속도, 만족도 are
tagged correctly).

**Measured on ``ko_core_news_lg`` 3.8.0 and true of no other version.** A new
model can start splitting 정확도 or stop splitting 재시도. ``pytest -m model``
will not notice unless one of its sentences happens to use the word, so a
model upgrade means tagging the 度 nouns again by hand and rewriting this list
from what comes back."""

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
        # The meeting itself. Every transcript names it — "지난 회의에서",
        # "회의 끝나고" — and it arrived with a particle often enough that the
        # particle used to refuse it. Once ``noun_stem`` reads through the
        # particle, 회의 was the first topic of ``transcript_ready.typical``.
        # 회의실 is the room: "강남 회의실에서" has ``LC`` claim only 강남.
        # Found while building #278.
        "회의",
        "회의실",
        # Positional bound nouns the model tags as common nouns (``ncn``, not
        # ``nbn``): "방법 중에", "서버 쪽에서", "주 안에". With the particle
        # read through they join the noun before them, and 서버 쪽 becomes a
        # second topic beside 서버. Measured in #278, alongside 때, which the
        # model does tag ``nbn`` and needs no entry.
        "중",
        "쪽",
        "안",
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


def noun_stem(token: Token) -> str | None:
    """The noun ``token`` names, with its particle cut off, or ``None``.

    A bare noun is its own stem. A noun carrying a particle — 리스크는
    (``ncn+jxt``), 로직은, 인덱스가 — is the noun without it. Korean attaches a
    particle to nearly every noun that is not the first half of a compound, so
    refusing those tokens, as ``noun_terms`` did before this, refused most of
    what a meeting names: "가장 큰 리스크는 콜드스타트입니다" had no topic in
    it at all, and "정렬 로직은" came back as 정렬. #278.

    Anything else is ``None``: a verb, an adverb, and a noun followed by the
    copula (``jp``) or an ending (``e*``). 붙입니다 is tagged ``ncn+jp+etm`` —
    a verb the model misread as a noun and the copula — and reading through
    it would give a topic called 붙. The copula is where the model is least
    reliable, so it is the part left out.

    Stacked particles are cut one ending at a time, and only as many as the
    tag has ``j`` morphemes for: 서버에서부터 (``jca+jxc``) is 부터 and then
    에서. The count is what keeps a noun whose last syllable spells a particle
    whole — 결과도, 합의는 and 경로로 carry one ``j`` each, so one cut is made
    and 과, 의, 로 stay. Cutting until nothing on the list matches would give
    결, 합 and 경. A stack the model tags as one morpheme (모듈까지도, ``jxc``)
    is on ``PARTICLES`` whole and costs the one cut. Where one cut could be
    read two ways, the token is refused (``_AMBIGUOUS_ENDINGS``) unless the
    noun is one known to end like a particle (``_NOUNS_ENDING_LIKE_A_PARTICLE``),
    and so is a stem a particle is still on (``_CASE_LEFTOVERS``).
    """
    parts = token.tag.split("+")
    if not parts[0].startswith(_NOUN_TAG_PREFIXES):
        return None
    if is_bare_noun(token.tag):
        return token.text
    head = next(
        index
        for index, part in enumerate(parts)
        if not (part.startswith(_NOUN_TAG_PREFIXES) or part == _NOUN_SUFFIX_TAG)
    )
    if not all(part.startswith("j") and part != "jp" for part in parts[head:]):
        return None
    stem = token.text
    remaining = len(parts) - head
    while remaining > 0 and stem not in _PARTICLE_LOOKALIKES:
        cuts = [
            (particle, count)
            for particle, count in PARTICLES.items()
            if stem.endswith(particle)
            and len(stem) > len(particle)
            and _fits(particle, stem[-len(particle) - 1])
        ]
        if not cuts:
            break
        # A measured noun decides between two readings the longest match would
        # otherwise take blind: 인프라도 is 인프라 + 도, not 인프 + 라도.
        known = [
            cut for cut in cuts if stem[: -len(cut[0])].endswith(_NOUNS_ENDING_LIKE_A_PARTICLE)
        ]
        particle, count = (known or cuts)[0]
        if (
            remaining <= count
            and not known
            and any(
                stem.endswith(ending) and len(ending) >= len(particle)
                for ending in _AMBIGUOUS_ENDINGS
            )
        ):
            return None
        stem = stem[: -len(particle)]
        remaining -= count
    if stem == token.text:
        return stem if stem in _PARTICLE_LOOKALIKES else None
    if stem.endswith(_CASE_LEFTOVERS):
        return None
    return stem


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

    A token joins a run as its ``noun_stem``, so 리스크는 joins as 리스크. A
    particle ends the phrase it is attached to, so the run ends *after* that
    token: "정렬 로직은 인덱스가" is 정렬 로직 and 인덱스, not one run of three.
    The stoplist is checked against the stem, one token at a time — 오늘은 is
    refused because 오늘 is, and 오늘 회의 keeps 회의.

    A run breaks on anything that has no noun stem, on a stopword, and on a
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
        stem = None if overlaps else noun_stem(token)
        if stem is None or stem in STOP_TERMS:
            flush()
            continue
        run.append(Token(text=stem, tag=token.tag, start=token.start, end=token.start + len(stem)))
        if stem != token.text:
            flush()
    flush()
    return terms


def entity_text(text: str, last: Token) -> str:
    """An entity's span with the particle on its last token cut off.

    The model's spans end where the word does, particle included — 오늘은,
    다음 주 화요일까지, 인덱스가 — and a label carries whatever the span
    carried. The same ``noun_stem`` the noun runs use decides the cut, so the
    two paths cannot disagree about where a word ends: before this, 오늘 was
    refused as a term and accepted as 오늘은, a date (#230).
    """
    stem = noun_stem(last)
    if stem is None or stem == last.text or not text.endswith(last.text):
        return text
    return text[: len(text) - len(last.text) + len(stem)]


def is_plausible(label: str, text: str) -> bool:
    """Whether an entity the model found is worth a node.

    Three rules, all about precision — C's metric is precision and a false
    topic is what a false gap is raised on.

    - **A stopword is not a topic whichever path found it.** 오늘 is a ``DT``
      span to the model and a word the noun runs refuse; one list decides
      both. The whole span is compared, not each word in it: 다음 주 화요일 is
      a deadline the meeting set, and it survives even though 다음 alone does
      not (#230).
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
    if stripped in STOP_TERMS:
        return False
    if label == "person":
        return len(stripped) > 1
    if label == "metric":
        return any(character.isdigit() for character in stripped)
    return bool(stripped)
