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
"""

from __future__ import annotations

from collections.abc import Sequence

from autune_core import get_logger
from autune_core.errors import ValidationError
from autune_integrations.privacy import find_unmasked

log = get_logger(__name__)


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
    categories = find_unmasked(text)
    if not categories:
        return
    log.info("extraction_typed_text_refused", field=field, categories=categories, **where)
    raise TypedPersonalDataError(field, categories)
