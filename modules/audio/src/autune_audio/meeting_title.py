"""A meeting's title is screened before it is stored (#1130, #1161).

A transcript is masked before its first write (privacy.md section 2). A title
is not a transcript: a person types it, it never met the masker, and every
module reads it from ``meetings`` each time it sends something -- B's
notices, C's alerts, D's brief, E's report, the agent layer's briefing. Until
#1161 it was stored as typed. The privacy owner's answer on #1130 (mkkim68,
2026-10-09) is that text a person types is screened when it is saved and
refused, in every module, with the one detector in ``autune_integrations``;
on #1161 (2-1, 2-2) that this holds for a title when a meeting is opened and
when it is renamed alike.

- **Refused, not masked.** A person's words are not rewritten for them; the
  save fails with a 422 and nothing changes.
- **The refusal names no value.** It says which field and which categories
  (``phone``, ``email``, ...) -- what to take out. The log line carries the
  categories and ids. Never the title: not in the response, the log or the
  exception.
- **Patterns only.** A name written in a title passes, as it passes the
  outbound check; a title can still name a client.
- **Only what is being written.** The title a meeting already has, sent back
  as it is, is not a write and does not come here (``service.rename_meeting``):
  a meeting titled before this rule keeps its title, and nothing stored is
  rewritten for it.
"""

from __future__ import annotations

from collections.abc import Sequence

from autune_core import get_logger
from autune_core.errors import ValidationError
from autune_integrations.privacy import find_unmasked

log = get_logger(__name__)

FIELD = "title"


class TitlePersonalDataError(ValidationError):
    """The title holds a pattern the detector reads; it was not saved.

    A ``ValidationError`` so it is a 422 like any other refused field, with two
    details more: ``reason`` for the screen to tell it from a length, and
    ``categories`` for it to say what to take out. The same two details module
    B's refusal of typed text carries."""

    def __init__(self, categories: Sequence[str]) -> None:
        super().__init__(
            "this title looks like it holds personal data and was not saved; "
            "take that value out and save again",
            field=FIELD,
        )
        self.details["reason"] = "personal_data"
        self.details["categories"] = list(categories)


def refuse_personal_data(title: str, **where: str | None) -> None:
    """Raise unless ``title`` may be stored as a person's own words.

    ``where`` goes to the log line and must be ids."""
    categories = find_unmasked(title)
    if not categories:
        return
    log.info("audio_meeting_title_refused", categories=categories, **where)
    raise TitlePersonalDataError(categories)
