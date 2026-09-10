"""Hide personal data in a transcript before anything writes it down.

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

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

MASK_CHAR = "*"

# The categories the patterns below produce, and the only ones with a digit
# layout worth preserving. Anything else comes from the recogniser and is hidden
# whole.
_NUMERIC_CATEGORIES = frozenset({"phone", "rrn", "card", "account"})

# Speech, not writing. A transcript of somebody reading a phone number aloud
# comes back with whatever separators Whisper felt like: "010-1234-5678",
# "010 1234 5678", "01012345678". Every pattern here allows all three, because
# the one it does not allow is the one that leaks.
_SEP = r"[-.\s]?"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Longest shapes first: an RRN also looks like two number groups, and a card
    # number contains things that look like account fragments. Whichever runs
    # first wins the span, so the most specific has to.
    ("rrn", re.compile(rf"\b\d{{6}}{_SEP}[1-4]\d{{6}}\b")),
    ("card", re.compile(rf"\b(?:\d{{4}}{_SEP}){{3}}\d{{4}}\b")),
    # Any leading-zero prefix, not an enumerated list of them. An earlier
    # version spelled out 01x/02/03x-06x and let 070, 080 and 0505 through
    # untouched -- 070 is a common Korean VoIP range and gets said in meetings.
    # Enumerating is how a pattern goes stale: the number ranges change and the
    # regex does not, and the one it does not accept is the one that leaks.
    ("phone", re.compile(rf"\b0\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{4}}\b")),
    # +82-10-1234-5678. Without this the account pattern eats the first two
    # groups and leaves the last eight digits standing, which is worse than not
    # matching at all.
    ("phone", re.compile(rf"\+?82{_SEP}\d{{1,3}}{_SEP}\d{{3,4}}{_SEP}\d{{4}}\b")),
    ("account", re.compile(r"\b\d{2,3}-\d{2,6}-\d{2,6}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
)


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
    spans: list[tuple[int, int, str]] = []
    for category, pattern in _PATTERNS:
        spans.extend((m.start(), m.end(), category) for m in pattern.finditer(text))
    if recogniser is not None:
        spans.extend(recogniser.find(text))

    kept = _resolve_overlaps(spans)
    counts: Counter[str] = Counter(category for _, _, category in kept)

    out: list[str] = []
    cursor = 0
    for start, end, category in kept:
        out.append(text[cursor:start])
        out.append(_hide(text[start:end], category))
        cursor = end
    out.append(text[cursor:])
    return Masked(text="".join(out), counts=counts)


def _resolve_overlaps(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Sort, and merge anything that touches. Nothing is discarded.

    Two detectors finding the same number is the normal case, not an error —
    that is what doubling detection means.

    Overlaps are **merged, not dropped**. An earlier version kept the first span
    and skipped any that started inside it, which quietly *shrank* what was
    covered: a name at (0, 3) and an address at (2, 20) left everything from 3
    to 20 in the clear. Every other judgement in this file goes toward covering
    more when the answer is unclear, and this was the one place going the other
    way.

    The category of a merged span comes from its longest contributor, since that
    is the one that saw the whole value. It only decides how the span is hidden
    and what the counts say, never whether it is.
    """
    kept: list[tuple[int, int, str]] = []
    for start, end, category in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if kept and start <= kept[-1][1]:
            previous_start, previous_end, previous_category = kept[-1]
            longest = (
                category if (end - start) > (previous_end - previous_start) else previous_category
            )
            kept[-1] = (previous_start, max(previous_end, end), longest)
            continue
        kept.append((start, end, category))
    return kept


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

    Everything else keeps its last four — what a person says out loud to tell
    two numbers apart, and what a card statement already shows.
    """
    count = len(digits)
    if category == "rrn":
        return {count - 7}
    tail = set(range(count - 4, count))
    if category == "phone" and "".join(digits[:2]) == "01":
        return {0, 1, 2} | tail
    return tail


def _hide(value: str, category: str) -> str:
    """Keep the shape a reader needs, remove the part they must not have."""
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

    digits = [c for c in value if c.isdigit()]
    keep = _digits_to_keep(category, digits)

    out: list[str] = []
    index = 0
    for char in value:
        if not char.isdigit():
            out.append(char)
            continue
        out.append(char if index in keep else MASK_CHAR)
        index += 1
    return "".join(out)
