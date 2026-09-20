"""The second detector: numbers a person read out loud, one digit at a time.

``privacy.md`` section 2 says detection is doubled — "regular expressions plus
NER" — and names the five categories both detectors are looking for: phone
numbers, email addresses, national ID numbers, bank account numbers, card
numbers. **This file adds no category.** It exists because the same five values
arrive in shapes a pattern cannot describe:

    제 번호는 공일공 일이삼사 오육칠팔이에요
    계좌는 국민 삼일이 이사 오육칠팔구공이요

Whisper writes what it hears. A Korean speaker reading a phone number aloud says
the digits as syllables, and ``find_pii`` sees no digits at all. Issue #143
collected these; they are the input that would be found by an incident rather
than by a test.

**A rule, not a model.** The obvious implementation was a Korean NER
checkpoint, and it is the wrong tool: a general Korean NER emits person, place,
organisation, date, time and quantity, none of which is "a bank account number",
and none of the five categories has an entry in any published inventory. What
the problem actually is, is a writing-system difference — 공일공 and 010 are the
same number in two scripts. A substitution answers it exactly, with no
checkpoint to pin, no GPU, no gated repository, and no accuracy that drifts when
somebody bumps a version.

**Why the offsets are free.** Each digit syllable is exactly one character and
becomes exactly one digit character, so the rewrite is length-preserving and a
span found in the rewritten text is the same span in the original. Mapping model
token offsets back to characters is where this kind of code usually goes wrong;
here there is nothing to map.

**Why it cannot invent a number.** Two reasons, and the second is the load-
bearing one:

1. Scale words are excluded. 십, 백, 천, 만 are not substituted, so 십오만 and
   삼십 are left alone — a quantity is not a phone number, and #148 is about the
   cost of forgetting that.
2. The patterns are the filter. 일이 (work) becomes ``12``, 이사 (moving house)
   becomes ``24``, and neither matches anything: the shortest shape ``find_pii``
   accepts is a ten-digit account. A run long enough to match would have to be
   ten-plus digit syllables in a row, which is a number being read out.

**The masked shape differs from privacy.md's example**, and safely. `010-****-5678`
keeps a mobile prefix because those three characters are digits; here they are
syllables, and `masking._hide` covers every character in a numeric span that is
not a separator. So a spoken number comes out `*** **** ****` -- the reader can
still see that a number was said, and none of it is recoverable. Keeping "the
first three" would mean keeping 공일공, which is the prefix written out.

So this file does not decide what is personal data. It rewrites one script into
another and asks the patterns the same question twice.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

from autune_integrations.privacy import PII_PATTERNS, find_pii

from .config import get_settings

# Sino-Korean digits as they are spoken, one syllable per digit.
#
# 영 and 공 are both zero: 영 is the written reading and 공 is what people say
# out loud in a number. 륙 is 육 after certain finals (오륙, 십륙) and Whisper
# writes both.
#
# **No scale words.** `autune_audio.eval.korean` has a numeral reader that does
# handle 십/백/천/만, because scoring has to turn 삼십 into 30 to compare it with
# "30". That is the opposite requirement from this one: a value with a scale
# word in it is a quantity, and masking quantities is what #148 is about. The
# two tables look similar and must not be merged.
_DIGIT_SYLLABLES: Final[dict[str, str]] = {
    "영": "0",
    "공": "0",
    "일": "1",
    "이": "2",
    "삼": "3",
    "사": "4",
    "오": "5",
    "육": "6",
    "륙": "6",
    "칠": "7",
    "팔": "8",
    "구": "9",
}

# A run of digits, spoken or written, broken by the separators a person pauses
# with.
#
# **Digits belong in here, not only syllables.** Whisper does not choose one
# script for a whole number: `공일공 1234 5678` and `010-1234 오육칠팔` are both
# real transcriptions, and a run made only of syllables stops at the first digit
# and leaves the rest of the number out of the count. That made a mixed number
# too short to rewrite and it came out in the clear.
#
# **The separators are `privacy._SEP`'s, not a guess at them.** A full stop is a
# separator there (`010.1234.5678` masks), and when it was not one here the run
# broke at it, fell under `MIN_RUN_DIGITS`, and `010.1234.오육칠팔` was never
# rewritten at all -- a leak the outbound guard cannot see either, because it
# cannot read spoken digits.
_SYLLABLES: Final = "".join(_DIGIT_SYLLABLES)
_RUN: Final = re.compile(rf"[{_SYLLABLES}0-9][{_SYLLABLES}0-9\s.-]*")

MIN_SPOKEN_SYLLABLES: Final = 3
"""How many spoken syllables a run needs when nothing else vouches for it.

Letting a run contain digits is what makes a mixed transcription reachable, and
it also lets a number already written as digits reach across a space into
ordinary Korean: `버전 20260910 이사 갑니다` became one run, 이사 was read as 24,
and a version string plus the word for "moving house" came out masked as an
account number. Requiring some of the run to actually be spoken separates "a
number Whisper wrote in two scripts" from "a number standing next to a word".

Three, because a switch of script across a pause happens in chunks --
`공일공 1234`, `010-1234 오육칠팔` -- never for one syllable. A number written
entirely in digits needs none of this: the patterns read it directly.

**This threshold applies only across a separator**, which is the second half of
the rule and the part that was missing. Three was originally written as a claim
about numbers -- "a script switch never happens for one syllable" -- and that
claim is false where the switch happens *inside* a group:

    010-1234-56칠팔   nothing matched at all, so nothing was masked
    010 1234 567팔    read as a ten-digit account, so the rule kept 4567

The second is the worse one. It is masked, `counts` records a masked span, and
four digits of a phone number are standing in the result -- the same shape #138
closed on the pattern side by putting `digits` above `account`, arriving here
from the masker side. `_glued_to_a_digit` is what admits these; see it for why
the threshold cannot simply be lowered instead.
"""

MIN_RUN_DIGITS: Final = 8
"""Runs with fewer digit positions than this are left as they are.

Counted over **both scripts**: a syllable in the table and a digit already
written as one each count once. Counting syllables alone was the bug --
`공일공 1234 5678이요` has four syllables and twelve digits, and the syllable
count kept it under the threshold.

Not a correctness guard. The shortest thing any pattern accepts is nine digits
(a phone number: a two-to-four digit prefix, then three or four, then four), so
eight cannot hide a match. It is there so the rewritten string stays close to the original: with no
minimum, every stray 이 and 사 in a meeting becomes a digit in the text the
patterns then read, and a debugging session here would be spent staring at
nonsense.
"""


def _glued_to_a_digit(run: str) -> bool:
    """True when the run switches script with no separator at the switch.

    This is what tells `010 1234 567팔` from `버전 20260910 이사 갑니다`. Both are
    one run, both are mostly digits, both end in digit syllables; the difference
    is that the first switches script *inside* a group and the second switches
    across a space. Korean writes a number's groups without internal spaces and
    writes a following word with one, so the space is the signal -- and a
    stronger one than counting syllables. `567팔` has one syllable and is
    unmistakably part of the number; `20260910 이사` has two and is not.

    Only the admission rule moves. `MIN_RUN_DIGITS` still applies, and it is what
    keeps ordinary Korean out: `10일 이사 갑니다` and `2사분기` are glued too, at
    five and three digit positions.

    Both directions, because Whisper switches script in either -- `오1234...` is
    the same event as `...1234오` seen from the other side.

    The cost is real, and it is the trade `masking.py` states in its first
    paragraph. A run of ten-plus digits with a digit syllable written hard
    against it -- `202609101일자로` -- now reads as an account and comes out
    masked. That is a masked version string; what it buys is an unmasked phone
    number.
    """
    return any(
        (run[i] in _DIGIT_SYLLABLES and run[i - 1].isdigit())
        or (run[i].isdigit() and run[i - 1] in _DIGIT_SYLLABLES)
        for i in range(1, len(run))
    )


def _worth_rewriting(run: str) -> bool:
    """Long enough to be a number, and spoken enough to be one being read out."""
    spoken = sum(1 for c in run if c in _DIGIT_SYLLABLES)
    if spoken + sum(1 for c in run if c.isdigit()) < MIN_RUN_DIGITS:
        return False
    return spoken >= MIN_SPOKEN_SYLLABLES or (spoken >= 1 and _glued_to_a_digit(run))


_PARTICLE_ONSETS: Final = frozenset({"이"})
"""The digit syllables a particle can begin with. One, today."""

MAX_PARTICLE_SYLLABLES: Final = 1
"""How many trailing syllables of a run may turn out to be a particle.

Korean attaches its particles directly to the number, and several of them begin
with a syllable that is also a digit: ``오육칠팔이에요`` ends in 이 (2),
``오육칠팔구공이요`` ends in 이 (2), ``공일공이랑`` ends in 이 (2). Read
greedily, the run takes the particle's first syllable as a digit and the number
comes out one digit too long -- which is the same boundary problem that made the
patterns themselves blind to Korean (#126), arriving from the other side.

So the end of a run is a guess, and this is how many syllables the guess may be
wrong by. **One, not three.** Of a particle's syllables only its first can be a
digit: 이 is, and 에 · 요 · 랑 · 로 are not in ``_DIGIT_SYLLABLES`` and never
enter the run at all. This used to say three, on the reasoning that ``이에요``
is three syllables long -- but the run stops at 이, so the guess can only ever
be wrong by that one. Giving back up to three handed real digits to the
sentence: ``공칠삼 육팔칠오 육구칠이일이에요`` came out ``*** **** ****일이에요``
with the 일 (=1) in the clear (#158 review, round 4).
"""

# Which pattern claimed a span, as a rank. `PII_PATTERNS` is declared
# most-specific-first, so a smaller number is a more confident reading: `phone`
# describes `010 1234 5678` better than `account` does, even though both match.
_SPECIFICITY: Final[dict[str, int]] = {}
for _rank, (_category, _pattern) in enumerate(PII_PATTERNS):
    _SPECIFICITY.setdefault(_category, _rank)


def _rewrite(text: str, start: int, end: int) -> str:
    """``text`` with the digit syllables in ``[start, end)`` written as digits.

    Length-preserving: one syllable in, one digit out, separators untouched. A
    span found in the result is the same span in ``text``.
    """
    out = list(text)
    for index in range(start, end):
        digit = _DIGIT_SYLLABLES.get(text[index])
        if digit is not None:
            out[index] = digit
    return "".join(out)


def _spell_out(text: str) -> str:
    """Every long run rewritten, greedily. For tests and for reading the diff."""
    for match in _RUN.finditer(text):
        if _worth_rewriting(match.group()):
            text = _rewrite(text, match.start(), match.end())
    return text


class SpokenNumberRecogniser:
    """Finds the five categories in numbers that were read out as words.

    Implements ``masking.EntityRecogniser``. Returns spans over the **original**
    text; ``masking._hide`` then covers every non-separator character in them,
    which is the behaviour it already has for exactly this case.
    """

    def find(self, text: str) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        for match in _RUN.finditer(text):
            if not _worth_rewriting(match.group()):
                continue
            found.extend(_best_reading(text, match.start(), match.end()))
        return found


def _best_reading(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    """Every value the patterns can see in one run, under the best reading of it.

    The run is rewritten repeatedly, each time giving one more trailing syllable
    back to the sentence, and every reading the patterns recognise is collected.
    **The winner is the reading that covers the most of the run**, and among
    readings that cover the same characters, the one whose most specific span
    is most specific -- ``phone`` over ``account`` for the same digits.

    Coverage first, because this is a masker. The other order let a narrow
    ``phone`` reading out-rank an ``account`` reading that covered every digit:
    ``공삼구 공구 공팔구오팔팔이에요`` -- a 3-2-6 bank layout -- came out
    ``공삼구 ** *******에요`` with its first group in the clear and ``counts``
    saying a phone number had been masked. A wrong category is a wrong row in
    ``aud_masking_events``; a wrong coverage is a leak.

    Coverage is the *union* of the spans' characters, not their sum.
    ``find_pii`` returns overlapping spans on purpose, so two patterns claiming
    the same digits would otherwise count them twice and a reading of thirteen
    characters could out-score one of fifteen.

    **A trailing 이 given back to the sentence does not count as lost
    coverage.** It is the one syllable that can be either a digit or the start
    of a particle, so a reading that excludes it is not covering less of the
    number -- it is making the other guess about the same character. Without
    this, coverage-first would read ``공일공 일이삼사 오육칠팔이에요`` as twelve
    digits every time, because twelve characters beat eleven, and the particle
    would be masked with the number. With it the two readings tie on coverage
    and specificity decides: ``phone`` over the twelve-digit catch-all, and the
    이 stays on the sentence.

    Where both readings are specific the tie still goes to rank, and rank can
    be wrong about the particle: ``공일공일이삼사오육칠팔이에요`` reads as
    thirteen digits (``rrn`` ranks above ``phone``) and 이 is masked with the
    number. That is the safe direction and a wrong row in ``aud_masking_events``,
    not a leak; it is noted rather than special-cased.

    **All of that reading's spans are returned, not its best one.** A run is one
    run of digits, not one number: two numbers read back to back have nothing
    between them that ends the run, and returning a single span left the second
    one in the clear. Merging what overlaps is `masking._resolve_overlaps`'s job
    and it already does it.

    Trying several readings is what the particle problem forces. Read greedily,
    ``오육칠팔이에요`` is one digit too long and the phone pattern rejects it for
    the digit that follows; give the 이 back and it is a phone number. And
    ``오육칠팔구공이요`` read greedily is a group of seven, wider than any bank
    layout, so greedy reading found nothing at all -- a leak, not a mislabel.
    """
    best: list[tuple[int, int, str]] = []
    best_key: tuple[int, int] | None = None

    for given_back in range(MAX_PARTICLE_SYLLABLES + 1):
        stop = end - given_back
        if stop <= start:
            break
        rewritten = _rewrite(text, start, stop)
        if rewritten == text:
            continue
        spans = [
            (span_start, span_end, category)
            for span_start, span_end, category in find_pii(rewritten)
            if span_start < end and span_end > start  # a number elsewhere is not ours
        ]
        if not spans:
            continue
        given = text[stop:end]
        slack = len(given) if given and set(given) <= _PARTICLE_ONSETS else 0
        key = (
            len({i for s, e, _ in spans for i in range(s, e)}) + slack,
            -min(_SPECIFICITY.get(c, len(_SPECIFICITY)) for _, _, c in spans),
        )
        if best_key is None or key > best_key:
            best, best_key = spans, key
    return best


class FakeRecogniser:
    """Fixed spans, no rules. What a test uses to drive the masker directly."""

    def __init__(self, spans: list[tuple[int, int, str]] | None = None) -> None:
        self._spans = spans or []

    def find(self, text: str) -> list[tuple[int, int, str]]:
        return self._spans


@lru_cache(maxsize=1)
def get_recogniser() -> SpokenNumberRecogniser | FakeRecogniser:
    """The configured recogniser, built once per process.

    ``"none"`` is a real setting rather than an omission: it is what the
    evaluation harness uses to measure the patterns on their own, and what
    answers "did the recogniser cause this?" without editing code.
    """
    setting = get_settings().recogniser
    if setting == "none":
        return FakeRecogniser()
    return SpokenNumberRecogniser()
