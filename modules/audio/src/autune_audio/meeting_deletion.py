"""A member of a team deletes one of its meetings (#1161).

Until this module a meeting ended only with its retention window or with its
team. A meeting opened by mistake, or one that should not have been recorded,
stayed for the window. Decided on #1161 by the module owners: any member of
the meeting's team may delete it, with the meeting's title typed as a team's
name is typed to delete the team (#1007), and not while it is being
transcribed.

**The same path as expiry and team deletion.** Every ``on_meeting_deleted``
hook runs, then the ``meetings`` row is deleted and everything any module
keeps for the meeting goes with it by ``ON DELETE CASCADE`` -- A deletes the
row because A is the only writer of ``meetings`` (invariant 4). What the
cascade cannot reach is what the hooks are for; see ``retention.sweep``.

Nobody is told, and nothing here reaches outside Autune. What a hook queues
for taking back from the team's tools and what stays there is listed in
privacy.md section 4, and the screen says it before the title is typed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio.live import registry as live_registry
from autune_core import Meeting, User, get_logger
from autune_core.deletion import run_meeting_hooks
from autune_core.errors import ConflictError, NotFoundError, ValidationError

from .models import TranscriptionJob
from .service import require_team_member
from .team_deletion import forget_recordings

log = get_logger(__name__)


class MeetingInProgressError(ConflictError):
    """The meeting is still being transcribed or recorded."""

    code = "meeting_in_progress"


class MeetingTitleMismatchError(ValidationError):
    """The title sent is not the meeting's. Its own code, so the screen can
    say which of its fields is wrong."""

    code = "meeting_title_mismatch"


@dataclass(frozen=True)
class MeetingDeleted:
    """What one deletion removed. A count only -- a title can name a client."""

    recordings: int
    """Recordings of finished attempts that were still on disk."""


def _same_title(typed: str, title: str) -> bool:
    """Compared as a person reads it, as ``team_deletion`` compares a name:
    surrounding space does not count, and neither does how a keyboard
    composed the Hangul. Case does."""
    return (
        unicodedata.normalize("NFC", typed).strip() == unicodedata.normalize("NFC", title).strip()
    )


def delete_meeting(
    session: Session, *, meeting_id: str, member: User, title: str
) -> MeetingDeleted:
    """Delete ``meeting_id`` and everything under it, for a member of its team.

    **Any member**, as any member may rename it: a meeting has no opener on
    its row. Somebody who is not on the team gets the 403 every reader of the
    meeting gets, before the title is compared -- a mismatch would say the
    meeting exists and what it is not called.

    **The title is compared with the one the meeting has now.** The row is
    locked first and read again, so a rename that was in flight has finished
    and it is its title that counts.

    **Refused while the meeting is being processed** (the module owner, on
    #1161: until transcription ends or is cancelled). The lock is the one
    ``start_transcription``, ``begin_live`` and ``lock_running_job`` take, so
    none of them starts or finishes underneath, and then, as
    ``team_deletion.delete_team`` refuses a team:

    - a job that is ``queued`` or ``running`` refuses. Deleted under a queued
      job, ``process_recording`` would end at ``claim_job`` before it owns the
      recording, and the file would wait for the orphan sweep's age limit
      (invariant 11). A job stays ``running`` until its ``TranscriptReady``
      has gone out, so a meeting that is gone is not announced.
    - an open live session refuses: the socket checks membership once, at its
      hello, and would go on sending a transcript to the browser.

    The lock is ``FOR NO KEY UPDATE``, which still excludes those three and
    lets a hook's own session insert a row that refers to the meeting.

    **The hooks run before the row goes**, and a hook that raises stops the
    deletion: this function raises, the caller rolls back, and the meeting is
    still there to ask again. Clean-up a hook had already queued in its own
    session still runs; every hook is safe to repeat.

    **No recording of the meeting is left behind.** A job that failed or was
    stopped keeps its recording for a restart, and with its row gone the
    orphan sweep would know that file only by its age. A file that will not
    go raises, the row stays, and the person asks again.

    A voice profile is the person's and is not touched here:
    ``retention.forget_idle_profiles`` takes it at the next sweep if no
    remaining meeting names its owner.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=member.id, team_id=meeting.team_id)
    team_id = meeting.team_id
    current = session.scalar(
        sa.select(Meeting.title).where(Meeting.id == meeting_id).with_for_update(key_share=True)
    )
    if current is None:
        # Gone while this request waited for the lock.
        raise NotFoundError("meeting", meeting_id)
    if not _same_title(title, current):
        # Neither title is repeated: a title can name a client.
        raise MeetingTitleMismatchError("the title does not match the meeting's title")

    jobs = list(
        session.execute(
            sa.select(TranscriptionJob.id, TranscriptionJob.status).where(
                TranscriptionJob.meeting_id == meeting_id
            )
        )
    )
    transcribing = sum(1 for _, status in jobs if status in ("queued", "running"))
    live = live_registry.is_open(meeting_id)
    if transcribing or live:
        log.info(
            "meeting_deletion_refused",
            meeting_id=meeting_id,
            team_id=team_id,
            user_id=member.id,
            transcribing=transcribing,
            live=live,
        )
        raise MeetingInProgressError(
            "this meeting is still being processed; "
            "delete it once that has finished or been cancelled"
        )

    run_meeting_hooks(meeting_id)
    session.execute(sa.delete(Meeting).where(Meeting.id == meeting_id))
    session.flush()
    recordings = forget_recordings([job_id for job_id, _ in jobs])
    log.info(
        "meeting_deleted",
        meeting_id=meeting_id,
        team_id=team_id,
        user_id=member.id,
        recordings=recordings,
    )
    return MeetingDeleted(recordings=recordings)
