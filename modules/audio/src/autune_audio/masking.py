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
    ("phone", re.compile(rf"\b0(?:1[016-9]|2|[3-6][0-5]){_SEP}\d{{3,4}}{_SEP}\d{{4}}\b")),
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
    """Sort, and drop any span that starts inside one already kept.

    Two detectors finding the same number is the normal case, not an error —
    that is what doubling detection means. The longer span wins, because the
    shorter one is usually a fragment of the same value and masking only part of
    a national ID number is worse than useless.
    """
    ordered = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    for span in ordered:
        if kept and span[0] < kept[-1][1]:
            continue
        kept.append(span)
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

    digits = [c for c in value if c.isdigit()]
    if not digits:
        # A span with no digits is what the recogniser is for: a name, a place,
        # an address. There is no shape to preserve, so the first character
        # stays and the rest goes -- 김민경 becomes 김**, which is how a Korean
        # document redacts a name and matches what the email rule already does.
        return value[:1] + MASK_CHAR * (len(value) - 1)

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
