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


def _hide(value: str) -> str:
    return "".join(MASK_CHAR if char.isalnum() else char for char in value)


def mask_document(text: str) -> MaskedDocument:
    """``text`` with every span of personal data hidden."""
    spans = _merged(find_pii(text))
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


def holds_mask(text: str) -> bool:
    """Whether something in ``text`` was hidden -- the reader is then told
    that the value is not kept and is in the file they have."""
    return MASK_CHAR in text
