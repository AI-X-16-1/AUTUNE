"""Hide personal data in a transcript before anything writes it down.

The shapes themselves are not here. ``autune_integrations.privacy`` owns them,
because that module refuses text that still matches them and this one hides what
does — two copies of the same patterns is how the guard and the masker came to
disagree about what personal data is (#126). This file owns what to *do* with a
span; what counts as a span is one decision, made once.

`docs/architecture/privacy.md` section 2 puts this between transcription and the
first `INSERT`, and the rules around it are unusually strict: the unmasked
string is a local variable in one function, and it is never returned, logged,
cached, queued, or sent anywhere. There is no unmasked column and no debug flag
that keeps one.

**Recall over precision, and not by a little.** A wrongly masked word is an
annoyance; a leaked national ID number is an incident. Every judgement call in
this file goes the same way, so the patterns are deliberately loose and the
shapes deliberately generous.

**Shape is preserved, content is removed** — `010-****-5678`, `k***@example.com`.
A reader has to be able to see that a phone number was said without being able
to read it, or the transcript stops making sense and people start asking for the
original.

Detection is doubled: patterns here, and a named-entity model behind
``EntityRecogniser``. The patterns carry the five MVP categories on their own;
the model is what catches the ones a pattern cannot describe, and it is a
Protocol rather than an import so this module works before the model exists.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from autune_integrations.privacy import MASK_CHAR, find_pii

# Kept inside a numeric span so the value still reads as a phone number or an
# account. Everything else is content, whoever found the span.
_SHAPE_CHARS = "-. +"

# The categories `find_pii` produces from a digit shape, and the only ones with
# a layout worth preserving. Anything else came from the recogniser and is
# hidden whole.
_NUMERIC_CATEGORIES = frozenset({"phone", "rrn", "card", "account", "digits"})


@runtime_checkable
class EntityRecogniser(Protocol):
    """The second detector. Spans it returns are masked as ``category``.

    A Protocol rather than a concrete model so this module is usable, testable
    and reviewable before one is chosen — the same seam module B put in front of
    its classifier and module D in front of its embedder.

    Whatever backs it runs **in our own process or on our own inference server**.
    Handing a meeting transcript to somebody else's NER service is the thing
    this file exists to prevent, and there is no config string that turns it on.
    """

    def find(self, text: str) -> list[tuple[int, int, str]]:
        """``(start, end, category)`` for each span to hide, in any order."""
        ...


@dataclass(frozen=True)
class Masked:
    """Masked text and how much was hidden, by category.

    ``counts`` is what `aud_masking_events` stores. Categories and counts only —
    never the spans, never the original. A table of "what we masked" would
    recreate the column privacy.md forbids.
    """

    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def spans(self) -> int:
        return sum(self.counts.values())

    def __repr__(self) -> str:
        """Counts only. This object is one line away from a log call."""
        return f"Masked(spans={self.spans}, categories={sorted(self.counts)})"


def mask(text: str, *, recogniser: EntityRecogniser | None = None) -> Masked:
    """Replace personal data in ``text``, keeping its shape.

    The caller holds the only reference to the unmasked string and must let it
    go: ``masked = mask(utterance).text`` and nothing else.
    """
    spans: list[tuple[int, int, str]] = find_pii(text)
    if recogniser is not None:
        spans.extend(recogniser.find(text))

    kept = _resolve_overlaps(spans)
    counts: Counter[str] = Counter(category for _, _, category, _ in kept)

    out: list[str] = []
    cursor = 0
    for start, end, category, merged in kept:
        out.append(text[cursor:start])
        out.append(_hide(text[start:end], category, merged=merged))
        cursor = end
    out.append(text[cursor:])
    return Masked(text="".join(out), counts=counts)


def _resolve_overlaps(
    spans: list[tuple[int, int, str]],
) -> list[tuple[int, int, str, bool]]:
    """Sort, and merge anything that touches. Nothing is discarded.

    Two detectors finding the same number is the normal case, not an error —
    that is what doubling detection means.

    Overlaps are **merged, not dropped**. An earlier version kept the first span
    and skipped any that started inside it, which quietly *shrank* what was
    covered: a name at (0, 3) and an address at (2, 20) left everything from 3
    to 20 in the clear. Every other judgement in this file goes toward covering
    more when the answer is unclear, and this was the one place going the other
    way.

    The fourth element says whether a span is the result of a merge. A merged
    span has no digit layout worth preserving — two values ran together, and the
    rules that keep a card's last four or a national ID's century digit are
    counting positions that no longer mean anything. Two ways that leaked: a
    name ending in a space merged with a following phone number took the numeric
    rule and passed the name through in the clear, and an RRN merged with an
    adjacent number moved the kept index off the century digit and onto part of
    the birth date. Hiding a merged span whole avoids reasoning about either.
    """
    # (start, end, category, longest single contributor) while building.
    building: list[list] = []
    for start, end, category in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        length = end - start
        if building and start <= building[-1][1]:
            entry = building[-1]
            if length > entry[3]:
                entry[2], entry[3] = category, length
            entry[1] = max(entry[1], end)
            continue
        building.append([start, end, category, length])

    # Merged only when the union came out larger than any one detector's span.
    # Two detectors returning the same span -- or one containing the other --
    # is agreement, not a run-together, and the layout of the longer one still
    # describes it.
    return [(s, e, c, (e - s) > longest) for s, e, c, longest in building]


def _digits_to_keep(category: str, digits: list[str]) -> set[int]:
    """Which digit positions survive, by category.

    A **mobile** prefix stays, which is the format privacy.md writes down:
    `010-****-5678`. It identifies nobody — every Korean mobile begins with one
    of four prefixes — and keeping it is what makes the line still read as a
    phone number.

    An **area code does not stay.** 02 and 031 say where somebody is, which is
    the kind of thing this file removes, and privacy.md's example is a mobile so
    it does not ask for them to be kept.

    ``rrn`` keeps only the leading digit of the second group, the
    century-and-sex marker. privacy.md gives no example for this one, and the
    birth-date half is as identifying as the rest, so it goes: where the doc is
    silent this takes the safer reading, which is the one it asks for everywhere
    else.

    ``digits`` keeps **nothing**. It is the catch-all for a run of digits no
    shaped pattern could describe, which means nobody knows what the last four
    of it are the last four *of*: `010123456789001011234567` is a phone number
    followed by a national ID, and the tail rule would leave four digits of the
    ID standing. The separator layout still survives, so the line still reads as
    a number having been said.

    Everything else keeps its last four — what a person says out loud to tell
    two numbers apart, and what a card statement already shows.
    """
    if category == "rrn":
        # Counted from the start, because the first group is always six digits.
        # Counting from the end was the same position only while an RRN was
        # exactly thirteen digits; the second group now takes six to eight, and
        # at fourteen the kept index slid off the marker onto a serial digit.
        return {6}
    if category == "digits":
        return set()
    count = len(digits)
    positions = set(range(count))
    tail = positions & set(range(count - 4, count))
    keep = tail
    if category == "phone" and "".join(digits[:2]) == "01":
        keep = tail | (positions & {0, 1, 2})
    # A rule that keeps four positions says nothing about a span that has four.
    #
    # `range(count - 4, count)` is negative-indexed below four digits, and the
    # negatives are not the positions they look like: at two digits it produced
    # {-2, -1, 0, 1}, which contains both real positions, so `_hide` kept the
    # whole span and `counts` recorded it as masked. A recogniser span of
    # `(0, 2, "account")` came out in the clear. `find_pii` cannot reach this --
    # its shortest shape is nine digits -- but `EntityRecogniser` is a
    # documented, tested seam and short spans are exactly what a model returns.
    #
    # Clamping the range alone would leave the other half: the mobile rule adds
    # three more positions, so a seven-digit span labelled `phone` kept all
    # seven. Both are the same mistake, which is counting positions without
    # checking there are more of them than the rule keeps. Where there are not,
    # this falls back to what `digits` already does and keeps nothing.
    return set() if keep == positions else keep


def _hide(value: str, category: str, *, merged: bool = False) -> str:
    """Keep the shape a reader needs, remove the part they must not have."""
    if merged:
        # See _resolve_overlaps: a merged span's digit positions no longer line
        # up with any one value's layout.
        return value[:1] + MASK_CHAR * (len(value) - 1)
    if category == "email":
        # The first character and the domain: enough to tell two people apart in
        # a transcript, not enough to write to either of them.
        local, _, domain = value.partition("@")
        return f"{local[:1]}{MASK_CHAR * 3}@{domain}"

    if category not in _NUMERIC_CATEGORIES:
        # What the recogniser is for: a name, a place, an address. There is no
        # digit layout to preserve, so the first character stays and the rest
        # goes -- 김민경 becomes 김**, the way a Korean document redacts a name
        # and the way the email rule above already works.
        #
        # Dispatched on the category rather than on whether the span happens to
        # contain digits. An address ends in a building number, and keying off
        # the digits sent it down the numeric path, where every Korean character
        # in it passed through untouched.
        return value[:1] + MASK_CHAR * (len(value) - 1)

    if any(not char.isdigit() and char not in _SHAPE_CHARS for char in value):
        # Written in two scripts, so there is no digit layout to preserve.
        #
        # `_digits_to_keep` counts the characters that are digits, and in a
        # mixed span those are only the half already written as digits. On
        # `010-1234 오육칠팔` it counted seven and the phone rule keeps a mobile
        # prefix and the last four -- which is all seven. Every digit stayed:
        #
        #     010-1234 오육칠팔  ->  010-1234 ****
        #
        # Keeping "the last four" is also unachievable here when those four are
        # syllables: leaving them is leaving the number spelled out. So a mixed
        # span is hidden whole, for the same reason a merged one is -- the
        # positions the rules count no longer mean what the rules assume.
        return "".join(char if char in _SHAPE_CHARS else MASK_CHAR for char in value)

    digits = [c for c in value if c.isdigit()]
    keep = _digits_to_keep(category, digits)

    out: list[str] = []
    index = 0
    for char in value:
        if not char.isdigit():
            # Separators keep the shape a reader needs; anything else in a span
            # the recogniser called a phone number is content. A span it returns
            # for "공일공 일이삼사 오육칠팔" is exactly the case patterns cannot
            # describe, and passing the syllables through masked nothing while
            # the counts recorded a masked span.
            out.append(char if char in _SHAPE_CHARS else MASK_CHAR)
            continue
        out.append(char if index in keep else MASK_CHAR)
        index += 1
    return "".join(out)
