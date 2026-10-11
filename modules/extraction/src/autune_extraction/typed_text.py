"""Text a person typed is screened before it is stored (#1130).

A transcript is masked before its first write (privacy.md section 2). What a
person types on one of module B's screens is not a transcript and never met
the masker, and until #1130 it was stored as typed and screened only on its way
out. The privacy owner's answer there (mkkim68, 2026-10-09, 가-2): screen it at
save and refuse, every module the way module C refuses a rewritten question,
with the one detector in ``autune_integrations`` -- so nothing the detector
reads is in B's store, nor in what B hands D and E.

- **Refused, not masked.** A person's words are not rewritten for them; the
  save fails with a 422 and nothing changes.
- **The refusal names no value.** It says which field and which categories
  (``phone``, ``email``, ...) -- what to take out -- and the log line carries
  the same and an id. Never the text, in the response, the log or the
  exception.
- **Patterns only.** A name a person writes, or someone's words copied in by
  hand, passes, as it passes the outbound check.
- **Only what is being written.** Text sent back exactly as it is stored is
  not a write: a row saved before this rule stays editable in its other
  fields, and nothing already stored is rewritten or deleted.
- **Read with its line breaks folded, stored with them** (module B's owner,
  2026-10-10). The detector was written for a transcript, where a line break
  is where one speaker's number ends and the next one's begins, so most of
  its shapes stop at one. A person typing breaks a line where they like: a
  resident number with the break beside its hyphen, an account over three
  lines and a +82 phone number broken before its last group were all read
  as nothing and stored. So the text is read twice, as typed and with each
  run of line breaks as one space, and refused when either reading finds a
  pattern. What is stored is what was typed; a memo keeps its lines.

  What that newly refuses and is not somebody's number: two numbers that
  meet across the break and make a shape together -- a line ending in six
  digits and the next starting with six to eight (a single line feed between
  them was refused before this, as an account; a blank line or a CRLF now is
  too), and digit groups joined by a hyphen at the break that come to eleven
  digits or more. A number with words on its own side of the break is not
  joined to anything. The rule's cost, accepted on #1130: a sentence the
  detector reads wrongly cannot be saved.

  What it still does not catch: a break inside a group of digits
  ("8512⏎25-1234567"), a hyphen typed on both sides of the break, an email
  address broken at its "@". The detector reads groups, and a fold puts a
  space where the break was; it does not guess which breaks to close up.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from autune_core import get_logger
from autune_core.errors import ValidationError
from autune_integrations.privacy import find_unmasked

log = get_logger(__name__)

_LINE_BREAKS = re.compile(r"[\r\n]+")
"""A run of line breaks: LF, CRLF, a lone CR, and any blank lines between two
lines. The other characters that break a line (a form feed, U+2028) the
detector already reads as a space."""


def _categories(text: str) -> list[str]:
    """What the detector reads in ``text``, as typed and with its line breaks
    folded to a space -- see the module's note. Both readings, so that folding
    can only add to what is refused, whatever the detector's patterns become;
    a category the fold found comes after the ones the typed text gave."""
    found = find_unmasked(text)
    folded = _LINE_BREAKS.sub(" ", text)
    if folded != text:
        found = [*found, *(name for name in find_unmasked(folded) if name not in found)]
    return found


class TypedPersonalDataError(ValidationError):
    """A person's text holds a pattern the detector reads; it was not saved.

    A ``ValidationError`` so it is a 422 like any other refused field, with two
    details more: ``reason`` for the screen to tell it from a length or a
    format, and ``categories`` for it to say what to take out."""

    def __init__(self, field: str, categories: Sequence[str]) -> None:
        super().__init__(
            "this text looks like it holds personal data and was not saved; "
            "take that value out and save again",
            field=field,
        )
        self.details["reason"] = "personal_data"
        self.details["categories"] = list(categories)


def refuse_personal_data(
    text: str | None, *, field: str, stored: str | None = None, **where: str | None
) -> None:
    """Raise unless ``text`` may be stored as a person's own words.

    ``stored`` is what the field holds now: the same text again is not a write.
    ``where`` goes to the log line and must be ids."""
    if not text or text == stored:
        return
    categories = _categories(text)
    if not categories:
        return
    log.info("extraction_typed_text_refused", field=field, categories=categories, **where)
    raise TypedPersonalDataError(field, categories)
