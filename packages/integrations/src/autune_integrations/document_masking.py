"""Hide personal data in a document's text before anything stores it, and
screen every excerpt again before it is shown or handed to a model (#817).

**What counts as personal data is not decided here.** The spans come from
``privacy.find_pii`` -- the one detector the repository has, recall-first, the
one the outbound guard refuses by (#817, 10(a)). Module A hides what it finds
in a transcript (``autune_audio.masking``); a module other than A cannot
import that, so this hides a document's spans itself, more bluntly: every
letter and digit of a span goes and only its layout stays. Nothing is kept for
readability -- no last four, no mobile prefix. Autune keeps no original of an
uploaded file, so what is hidden here cannot be shown again by anyone.

**Stored text passes the guard the repository already has.** ``mask_document``
checks its own result with ``find_unmasked`` and raises if anything is left,
so a piece that reaches a table would also pass ``assert_masked``.

**Masked is not anonymous.** A patterned value is hidden; a name written in a
sentence, or content that identifies somebody by what it describes, is not
(ADR 0007, accepted cost). That is why masked document text still has a
deletion path and a retention window (``docs/architecture/privacy.md``).

**A ``*`` in a document is a character somebody typed.** In a transcript it
is only ever a mask Autune wrote, and the detector lets go of any match that
holds one. A document has ``**bold**``, a bullet, ``3*4`` -- and now and then
a value written with stars between its parts (``010*1234*5678``), which the
detector's patterns cannot join and which would be stored whole. So a
document is read twice, as it stands and with every ``*`` taken for a space,
and what either reading finds is hidden (``_spans``). Two things follow.
A value somebody half hid by hand (``010-****-5678``) stays as typed: what is
left of it is not a value the detector knows. And nothing in masked text
tells a mask from a typed star, so this module does not offer to say whether
a text "holds a mask"; whether anything was hidden is
``MaskedDocument.counts``, known when the text is masked and not afterwards.

**A line in a document can end inside a value.** An utterance has no line
breaks; text taken out of a PDF or a ``.docx`` breaks where the page or the
cell did. The detector joins the parts of most values across spaces and not
across a line break, so ``900101-`` at the end of one line and ``1234567`` at
the start of the next were stored whole (@mkkim68 on #1199). So a document is
read a third time, with every line break (CR, LF) taken for a space. A star
can sit at a line's end as well (``900101*`` and then ``1234567``), where
each of those two readings sees half a value, so a text that has both is read
once more with both taken for spaces. Being a document's masker, this hides
too much before too little.

**What no reading puts back together.** Each of these is still stored with
some or all of it readable:

- a line that ends inside a group of digits and not between two groups
  (``010-23`` and then ``45-6789``): with the break a space, the group is two
  short numbers;
- a break that left a hyphen on both lines (``900101-`` and then
  ``-1234567``);
- a value that runs across the edge of a PDF page: the page's own lines -- a
  page number, a running header -- stand between its halves in the text a
  reader takes out, and no reading here takes a line away;
- an e-mail address broken across lines: a space is no part of an address, so
  at most the half that still reads as one is hidden.

The unmasked string is a parameter and a local here and nothing else: it is
not returned, logged, cached or put in an exception.

Not built: hiding only values that validate (a resident number with a good
check digit, a card that passes Luhn), so that more amounts and quantities
survive. That is a rule for the privacy owner to accept first.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from autune_core.errors import PrivacyViolationError

from .privacy import MASK_CHAR, find_pii, find_unmasked


@dataclass(frozen=True)
class MaskedDocument:
    text: str
    """Safe to store. The original is not kept beside it."""
    counts: dict[str, int] = field(default_factory=dict)
    """How many spans of each category were hidden -- for a log line."""


def _merged(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Overlapping spans as one, under the category that began first: a phone
    number run into a card number is hidden across the union, never across
    one of the two."""
    merged: list[tuple[int, int, str]] = []
    for start, end, category in sorted(spans):
        if merged and start < merged[-1][1]:
            last_start, last_end, last_category = merged[-1]
            merged[-1] = (last_start, max(last_end, end), last_category)
        else:
            merged.append((start, end, category))
    return merged


def _spans(text: str) -> list[tuple[int, int, str]]:
    """What the detector reads in ``text`` as it stands, what it reads with
    every ``*`` taken for a space, what it reads with every line break taken
    for a space, and what it reads with both. Each other text is the same
    length, so its spans are positions in ``text`` too."""
    spans = find_pii(text)
    if MASK_CHAR in text:
        spans = spans + find_pii(text.replace(MASK_CHAR, " "))
    # CR and LF only: every other character that breaks a line is a space to
    # the detector already (``privacy._HSPACE``).
    unbroken = text.replace("\r", " ").replace("\n", " ")
    if unbroken != text:
        spans = spans + find_pii(unbroken)
        if MASK_CHAR in unbroken:
            spans = spans + find_pii(unbroken.replace(MASK_CHAR, " "))
    return spans


def _hide(value: str) -> str:
    return "".join(MASK_CHAR if char.isalnum() else char for char in value)


def mask_document(text: str) -> MaskedDocument:
    """``text`` with every span of personal data hidden."""
    spans = _merged(_spans(text))
    pieces: list[str] = []
    position = 0
    for start, end, _ in spans:
        pieces.append(text[position:start])
        pieces.append(_hide(text[start:end]))
        position = end
    pieces.append(text[position:])
    masked = "".join(pieces)
    left = find_unmasked(masked)
    if left:
        # Categories only: the values are what must not be written anywhere.
        raise PrivacyViolationError(
            "a document still held personal data after masking", categories=sorted(left)
        )
    return MaskedDocument(masked, dict(Counter(category for _, _, category in spans)))


def screen_output(text: str) -> str:
    """The last screen before an excerpt or a title leaves for a person's
    screen or a model's prompt: whatever the detector finds now is hidden now.
    Stored text was masked when it was stored; this is for what was never
    masked (a typed title) and for a detector that has learned a shape since."""
    return mask_document(text).text
