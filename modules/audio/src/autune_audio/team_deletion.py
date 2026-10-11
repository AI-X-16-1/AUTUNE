"""The last member of a team deletes it (#1007).

``service.leave_team`` refuses the last member: a team with nobody on it could
be neither read nor deleted. Until this module that left a team somebody used
alone with no end but the retention window, and its name, its integrations'
tokens and its masking rules with no end at all. Decided on #1007 by the five
module owners (2026-10-09), and written into privacy.md section 4
and ADR 0007 before this code: the one person left may delete the team, with
its name typed.

**A deletes the rows because A is the only writer of ``teams`` and
``meetings``** (invariant 4). Everything any module keeps for the team or for
one of its meetings is reachable by ``ON DELETE CASCADE`` from one of the two,
so the ``DELETE`` here takes B's, C's, D's, E's and the agent layer's rows with
it. What the cascade cannot reach is what ``on_meeting_deleted`` is for -- B's
calendar events and C's agenda lines are moved to clean-up queues keyed by
person -- and those hooks run first, for every meeting, as in ``retention.py``.

Nothing here reaches outside Autune and nobody is told. What stays in the
team's own tools is listed in privacy.md section 4, and the screen says it
before the name is typed.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio.live import registry as live_registry
from autune_core import Meeting, Team, TeamMember, User, get_logger
from autune_core.deletion import run_meeting_hooks
from autune_core.errors import ConflictError, NotFoundError, ValidationError

from . import storage
from .config import AudioSettings
from .models import AudTeamInvitation, TranscriptionJob
from .service import NotATeamMemberError, require_team_member

log = get_logger(__name__)


class TeamHasOtherMembersError(ConflictError):
    """Somebody else is on the team. Nobody deletes a team other people are
    on, and nobody takes them off it first."""

    code = "team_has_other_members"


class TeamMeetingInProgressError(ConflictError):
    """A meeting of the team is still being transcribed or recorded."""

    code = "team_meeting_in_progress"


class TeamNameMismatchError(ValidationError):
    """The name sent is not the team's. Its own code, so the screen can say
    which of its fields is wrong."""

    code = "team_name_mismatch"


@dataclass(frozen=True)
class TeamDeleted:
    """What one deletion removed. Counts and ids only -- a name can name a client."""

    meetings: int
    recordings: int
    """Recordings of finished attempts that were still on disk."""


def _same_name(typed: str, name: str) -> bool:
    """Compared as a person reads it: surrounding space does not count, and
    neither does how a keyboard composed the Hangul. Case does."""
    return unicodedata.normalize("NFC", typed).strip() == unicodedata.normalize("NFC", name).strip()


def delete_team(session: Session, *, team_id: str, member: User, name: str) -> TeamDeleted:
    """Delete ``team_id`` and everything under it, for its last member.

    **Only the one person left.** Every membership of the team is locked
    first, in ``leave_team``'s order, so a mate who is leaving and this
    request are counted one after the other. Then the team's pending
    invitations are deleted -- they would cascade anyway -- because that is
    what stops somebody joining underneath: ``invitations.accept`` holds its
    invitation's row, so one in flight finishes before this goes on and one
    that comes later finds no invitation. The members are counted again after
    it, for the one that finished.

    **Refused while a meeting is being processed** (the module owner, on
    #1007). The team's meetings are locked the way ``start_transcription``,
    ``begin_live`` and ``lock_running_job`` lock one, so none of them starts
    or finishes underneath, and then:

    - a job that is ``queued`` or ``running`` refuses. Deleted under a queued
      job, ``process_recording`` would end at ``claim_job`` before it owns the
      recording, and the file would wait for the orphan sweep's age limit
      (invariant 11). A job stays ``running`` until its ``TranscriptReady``
      has gone out (``mark_published``), so this also covers the moment
      between the transcript's commit and the event -- a meeting that is gone
      is not announced.
    - an open live session refuses: the socket checks membership once, at its
      hello, and would go on sending a transcript to the browser.

    The lock is ``FOR NO KEY UPDATE``. It still excludes those three, and
    unlike ``FOR UPDATE`` it lets a hook's own session insert a row that
    refers to the meeting.

    **Every meeting's hooks run before any row goes** -- the order of
    ``retention.sweep``, and what two owners asked for on #1007. A hook that
    raises stops the deletion: this function raises, the caller rolls back,
    and the team and every meeting of it are still there to ask again, as a
    user hook stops an account deletion (#358). Clean-up a hook had already
    queued in its own session still runs; every hook is safe to repeat.

    B's hook also queues the team's project-minutes copies for taking back,
    in a table that goes with the team -- module B's owner chose to leave
    those copies in the team's tools (#1007). Between that hook's commit and
    this transaction's, B's periodic drain could still take one back. Nothing
    here prevents that and nothing depends on it.

    **No recording of the team is left behind.** A job that failed or was
    stopped keeps its recording for a restart, and with its row gone the
    orphan sweep would know that file only by its age. ``forget_recordings``
    deletes them here, before the caller commits: a file that will not go
    raises as it does everywhere in this module, the rows stay, and the
    person asks again.

    A voice profile is the person's and is not touched here:
    ``retention.forget_idle_profiles`` takes it at the next sweep if no
    remaining meeting names its owner.
    """
    require_team_member(session, user_id=member.id, team_id=team_id)
    rows = list(
        session.scalars(
            sa.select(TeamMember).where(TeamMember.team_id == team_id).with_for_update()
        )
    )
    if not any(row.user_id == member.id for row in rows):
        # Gone while this request waited for the lock, as in ``leave_team``.
        raise NotATeamMemberError("you are not a member of this team")
    if len(rows) > 1:
        raise TeamHasOtherMembersError("a team with other members on it cannot be deleted")
    session.execute(sa.delete(AudTeamInvitation).where(AudTeamInvitation.team_id == team_id))
    on_team = session.scalar(
        sa.select(sa.func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)
    )
    if on_team != 1:
        raise TeamHasOtherMembersError("a team with other members on it cannot be deleted")

    team = session.get(Team, team_id)
    if team is None:
        raise NotFoundError("team", team_id)
    if not _same_name(name, team.name):
        # Neither name is repeated: a team's name can name a client.
        raise TeamNameMismatchError("the name does not match the team's name")

    meeting_ids = list(
        session.scalars(
            sa.select(Meeting.id)
            .where(Meeting.team_id == team_id)
            .order_by(Meeting.id)
            .with_for_update(key_share=True)
        )
    )
    jobs = list(
        session.execute(
            sa.select(TranscriptionJob.id, TranscriptionJob.status).where(
                TranscriptionJob.meeting_id.in_(
                    sa.select(Meeting.id).where(Meeting.team_id == team_id)
                )
            )
        )
    )
    transcribing = sum(1 for _, status in jobs if status in ("queued", "running"))
    live = sum(1 for meeting_id in meeting_ids if live_registry.is_open(meeting_id))
    if transcribing or live:
        log.info(
            "team_deletion_refused",
            team_id=team_id,
            user_id=member.id,
            transcribing=transcribing,
            live=live,
        )
        raise TeamMeetingInProgressError(
            "a meeting of this team is still being processed; delete the team once it has finished"
        )

    for meeting_id in meeting_ids:
        run_meeting_hooks(meeting_id)
    for meeting_id in meeting_ids:
        session.execute(sa.delete(Meeting).where(Meeting.id == meeting_id))
    session.execute(sa.delete(Team).where(Team.id == team_id))
    session.flush()
    recordings = forget_recordings([job_id for job_id, _ in jobs])
    log.info(
        "team_deleted",
        team_id=team_id,
        user_id=member.id,
        meetings=len(meeting_ids),
        recordings=recordings,
    )
    return TeamDeleted(meetings=len(meeting_ids), recordings=recordings)


def forget_recordings(job_ids: Sequence[str], *, settings: AudioSettings | None = None) -> int:
    """Delete any recording still named after one of ``job_ids``; say how many.

    For jobs nothing is running: the caller has refused a team with a queued
    or running one, so no task owns any of these files. ``delete_orphan``
    raises when a file is still there afterwards, and that is not caught.
    """
    removed = 0
    for job_id in job_ids:
        path = storage.upload_path(job_id, settings)
        if path.exists():
            storage.delete_orphan(path)
            removed += 1
    return removed
