"""Business logic for module A: Audio Pipeline.

Owner: 김민경. See docs/modules/audio.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``aud_*`` tables.
Never imports another module.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.transcript import Utterance as ContractUtterance
from autune_core import Meeting, TeamMember, User, get_logger
from autune_core.errors import NotFoundError, PermissionDeniedError

from .persistence import transcript_payload

log = get_logger(__name__)


def require_team_member(session: Session, *, user_id: str, team_id: str) -> None:
    """Raise unless ``user_id`` belongs to ``team_id``.

    A token proves who is asking, not which team's meetings they may read. Named
    and shared rather than written inline, because every route this module grows
    asks the same question and an authorisation check that exists in two places
    is one that can come to mean two things.
    """
    member = session.scalar(
        sa.select(TeamMember.id).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if member is None:
        raise PermissionDeniedError("you are not a member of this team")


def transcript_for_meeting(
    session: Session, *, meeting_id: str, reader: User
) -> list[ContractUtterance]:
    """One meeting's transcript, for the person asking to read it.

    **The same builder the published event uses.** ``transcript_payload`` shapes
    what goes out on ``TranscriptReady``; taking its ``utterances`` rather than
    querying the rows again means the screen and the four consuming modules
    cannot come to disagree about what was said, or about the order it was said
    in. Two readers of the same rows is how the masker and the outbound guard
    drifted apart in #126.

    **Masked, because that is what is stored.** There is no unmasked column to
    read and no flag that returns one — module A masks before the first write
    (privacy.md section 2) — so this returns what the database has and the screen
    marks the masked spans rather than offering to reveal them.

    **Empty while the task is still running**, which is honest rather than a
    special case: ``persist_transcript`` writes every utterance in one
    transaction at the end, so a meeting part-way through has none rather than
    some. A caller that needs to tell "not yet" from "nothing was said" reads
    the meeting's status, which is what it is for.

    Speaking ratios are not here and never will be. privacy.md section 3 gives
    those to module E, delivered to the speaker and nobody else.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reader.id, team_id=meeting.team_id)

    # Ids only. A transcript is meeting content and a log line is a store.
    log.info("audio_transcript_read", meeting_id=meeting_id, reader_id=reader.id)
    return transcript_payload(session, meeting_id=meeting_id).utterances
