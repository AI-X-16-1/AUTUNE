"""Business logic for module A: Audio Pipeline.

Owner: 김민경. See docs/modules/audio.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``aud_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.transcript import Utterance as ContractUtterance
from autune_core import Meeting, TeamMember, User, get_logger
from autune_core.errors import ConflictError, NotFoundError, PermissionDeniedError

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


_ACCEPTS_A_RECORDING = frozenset({"scheduled", "failed"})
"""Meeting statuses a recording may be submitted for.

The other five are refusals with different reasons: ``recording`` and
``analyzing`` already have a run in flight, and ``awaiting_confirmation``,
``complete`` and ``delivered`` have a transcript four modules have already been
told about.
"""


def create_meeting(
    session: Session,
    *,
    owner: User,
    title: str,
    team_id: str,
    started_at: datetime | None = None,
) -> Meeting:
    """Open a meeting for ``team_id``, before there is any audio.

    **Module A creates it because module A owns it.** ``meetings`` is a shared
    entity and invariant 4 gives A the only write — so the row cannot be made by
    whoever happens to need it, and until now nothing made it at all: the table
    had no writer outside the tests and the pipeline had no front door.

    Separate from the upload on purpose. The live-microphone path (S10/S13) has
    a meeting well before it has a recording, and a scheduled meeting exists
    before anybody presses anything. Folding creation into the upload would give
    that path its own second way to make a row, and two writers of a shared
    entity is how the column that means one thing here comes to mean another
    there.

    ``status`` starts at ``scheduled`` — the column's default, written out here
    because the lifecycle in this module reads better when every transition is
    visible in one file.
    """
    require_team_member(session, user_id=owner.id, team_id=team_id)

    meeting = Meeting(team_id=team_id, title=title, started_at=started_at, status="scheduled")
    session.add(meeting)
    session.flush()

    # The title is the team's own words and can carry a client name; it is not
    # logged. The id is enough to follow the meeting through the pipeline.
    log.info("audio_meeting_created", meeting_id=meeting.id, team_id=team_id, owner_id=owner.id)
    return meeting


def start_transcription(session: Session, *, meeting_id: str, uploader: User) -> Meeting:
    """Claim the meeting for a recording that is about to be queued.

    **The status flip is the claim, and it has to happen before the enqueue.**
    ``persist_transcript`` replaces a meeting's utterances rather than appending
    — that is what makes the task safe to redeliver — so two recordings running
    against one meeting do not merge, they race, and the meeting keeps whichever
    finished last with no trace that the other existed. Refusing here is how
    that stops being possible.

    ``ConflictError`` rather than a silent second queue: the caller uploaded a
    file and is entitled to know it was not accepted.

    ``failed`` is accepted alongside ``scheduled`` because it is the state a
    recovery starts from — a decoder that fell over or an enqueue that never
    reached the broker leaves a meeting with no transcript and no task, and the
    alternative to retrying it is abandoning the meeting row and everything
    attached to it. ``complete`` is not accepted: that transcript has already
    gone out to four modules, and replacing it underneath them is the rerun
    problem in #194 rather than something an upload decides on its own.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=uploader.id, team_id=meeting.team_id)

    if meeting.status not in _ACCEPTS_A_RECORDING:
        raise ConflictError(
            f"meeting {meeting_id} is {meeting.status}; a recording can only be "
            f"submitted for a meeting that is {' or '.join(sorted(_ACCEPTS_A_RECORDING))}"
        )

    meeting.status = "analyzing"
    session.flush()
    log.info("audio_transcription_started", meeting_id=meeting_id, uploader_id=uploader.id)
    return meeting


def mark_failed(session: Session, *, meeting_id: str) -> None:
    """Record that this meeting's transcription will not finish.

    Takes no user. Both callers are places where there is nobody to authorise
    against: the worker, whose task has just raised, and the endpoint, whose
    enqueue did not reach the broker. An authorisation check here would either
    be skipped or be given a fake user to satisfy it, and both are worse than
    not having one — the meeting was already claimed by a request that *was*
    checked.

    A meeting that stayed ``analyzing`` forever would be indistinguishable from
    one still being transcribed, and the screen would spin on it for good.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)

    meeting.status = "failed"
    session.flush()
    log.info("audio_meeting_failed", meeting_id=meeting_id)


def mark_complete(session: Session, *, meeting_id: str) -> None:
    """The transcript is written and the meeting is done being transcribed.

    Called inside the same transaction as ``persist_transcript``, so the status
    and the rows it describes commit together. Split out rather than set inline
    there because ``persist_transcript`` is about utterances, and a function
    that also moves the meeting's lifecycle along is one whose name stops
    telling you what it does.

    Not ``delivered`` and not ``awaiting_confirmation``: those are B's and E's
    to decide, later in the meeting's life. A only says that its own step
    finished.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)

    meeting.status = "complete"
    session.flush()
