"""A reported miss is masked where it is stored (S30, #555).

Until this module the only masking path ran before the first write. A miss
found by a reader stayed in ``utterances.text`` -- and in whatever B, C and D
had already taken from ``TranscriptReady`` -- for the rest of the retention
window.

**The request carries offsets, never the text.** The browser sends which
characters of which utterance; the span is read from the row here, masked, and
written back. The unmasked string is therefore never in a request body, a
proxy log or an access log, and inside this module it is a local variable that
is never logged, returned or put in an exception (privacy.md section 2).

**Masked, not deleted.** privacy.md section 2 said a reported miss deletes the
utterance; S30 masks the selected span. Masking removes exactly what was
reported and keeps the rest of what was said -- the utterance an action item
quotes as its evidence stays readable around the redaction. The doc is updated
in the same change.

**Downstream learns by a second ``TranscriptReady``.** async-pipeline.md
already requires every consumer to be idempotent because "a meeting can be
reprocessed after a correction", so re-announcing the corrected transcript is
the contract as written -- no new event, no contract change. The route
publishes after its transaction commits, as ``process_recording`` does, so the
event can never describe text the database does not hold.

**"This meeting's similar spans" are exact repeats.** The same string elsewhere
in the meeting is masked in the same request when asked: a phone number said
twice is the case that matters, and an exact match is the one way to find more
of it without guessing. A workspace-wide pattern rule is not built yet.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_core import Meeting, User, Utterance, get_logger
from autune_core.errors import NotFoundError, ValidationError

from .masking import hide_reported
from .service import require_team_member

log = get_logger(__name__)


@dataclass(frozen=True)
class Reported:
    utterances: int
    """How many utterances changed, the reported one included."""
    occurrences: int
    """How many places the span was masked."""


def report_miss(
    session: Session,
    *,
    meeting_id: str,
    utterance_id: str,
    start: int,
    end: int,
    category: str,
    include_similar: bool,
    reporter: User,
) -> Reported:
    """Mask ``text[start:end]`` of one utterance, and its exact repeats if asked.

    ``category`` is logged as the only description of what was found -- the
    count of misses per category is what tells the detector where it is weak,
    and it says nothing about the content.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reporter.id, team_id=meeting.team_id)

    utterance = session.get(Utterance, utterance_id, with_for_update=True)
    if utterance is None or utterance.meeting_id != meeting_id:
        raise NotFoundError("utterance", utterance_id)
    if not 0 <= start < end <= len(utterance.text):
        # Offsets only in the message: the text is what must not be in one.
        raise ValidationError(f"span {start}..{end} is outside the utterance")

    span = utterance.text[start:end]
    if not span.strip() or "*" in span:
        raise ValidationError("the selected span has nothing left to mask")
    hidden = hide_reported(span)

    utterance.text = utterance.text[:start] + hidden + utterance.text[end:]
    changed = 1
    occurrences = 1 + _replace_all(utterance, span, hidden)

    if include_similar:
        # Matched here, not with ``LIKE`` in SQL: a bound parameter is part of
        # a ``StatementError``'s message, and the engine does not set
        # ``hide_parameters`` (#356) -- the unmasked span must not be in one.
        others = session.scalars(
            sa.select(Utterance)
            .where(Utterance.meeting_id == meeting_id, Utterance.id != utterance_id)
            .with_for_update()
        ).all()
        for other in others:
            found = _replace_all(other, span, hidden)
            if found:
                occurrences += found
                changed += 1

    session.flush()
    log.info(
        "audio_pii_miss_reported",
        meeting_id=meeting_id,
        utterance_id=utterance_id,
        category=category,
        utterances=changed,
        occurrences=occurrences,
    )
    return Reported(utterances=changed, occurrences=occurrences)


def _replace_all(utterance: Utterance, span: str, hidden: str) -> int:
    count = utterance.text.count(span)
    if count:
        utterance.text = utterance.text.replace(span, hidden)
    return count
