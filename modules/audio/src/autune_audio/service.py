"""Business logic for module A: Audio Pipeline.

Owner: 김민경. See docs/modules/audio.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``aud_*`` tables.
Never imports another module.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autune_audio.live import registry as live_registry
from autune_contracts.transcript import Utterance as ContractUtterance
from autune_core import Meeting, Participant, Team, TeamMember, User, get_logger, session_scope
from autune_core.auth import decode_token
from autune_core.deletion import on_user_deleted
from autune_core.errors import ConflictError, NotFoundError, PermissionDeniedError

from . import identification, storage
from .config import AudioSettings, get_settings
from .models import AudConsentAttestation, AudSpeakerEmbedding, TranscriptionJob
from .persistence import transcript_payload
from .schemas import SpeakerCandidate, SpeakerEntry, TeamMemberSummary
from .speakers import UNIDENTIFIED

log = get_logger(__name__)


class NotATeamMemberError(PermissionDeniedError):
    """A real user who is not on this team.

    A subclass so every HTTP route keeps answering 403 exactly as before,
    while the live socket -- which cannot answer with a status -- can tell
    this apart from a token that never resolved to anyone and close with
    its own code.
    """


def require_team_member(
    session: Session, *, user_id: str, team_id: str, message: str | None = None
) -> None:
    """Raise unless ``user_id`` belongs to ``team_id``.

    A token proves who is asking, not which team's meetings they may read. Named
    and shared rather than written inline, because every route this module grows
    asks the same question and an authorisation check that exists in two places
    is one that can come to mean two things.

    ``message`` lets a caller that checks somebody *other* than the person
    making the request -- ``assign_speaker`` checking the named ``user_id``,
    not just ``confirmed_by`` -- say so, rather than telling a caller who is
    themselves a member "you are not a member of this team" about a refusal
    that was actually about someone else.
    """
    member = session.scalar(
        sa.select(TeamMember.id).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    if member is None:
        raise NotATeamMemberError(message or "you are not a member of this team")


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

    A speaking ratio is not computed here. But since this branch (#6) this
    payload's utterances carry ``speaker_id``, ``start`` and ``end``, so a
    per-person duration is one ``GROUP BY`` away for any reader of this
    route -- whether that is a gap privacy.md section 3 needs closing is
    open in #361; this function's behaviour has not changed.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reader.id, team_id=meeting.team_id)

    # Ids only. A transcript is meeting content and a log line is a store.
    log.info("audio_transcript_read", meeting_id=meeting_id, reader_id=reader.id)
    return transcript_payload(session, meeting_id=meeting_id).utterances


def meeting_for(session: Session, *, meeting_id: str, reader: User) -> Meeting:
    """One meeting's own row, for a member of its team.

    What screen S12 polls. ``/transcripts/{id}`` returns an empty list all the
    way through the task, so without the status a screen cannot tell "not
    yet" from "nobody spoke" (``transcript_for_meeting``). Same check as the
    transcript read: a token says who is asking, membership says whether they
    may.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reader.id, team_id=meeting.team_id)
    return meeting


def teams_for(session: Session, *, member: User) -> list[Team]:
    """The teams ``member`` belongs to, by name.

    ``MeetingCreate`` takes a ``team_id`` and a browser holding only a token has
    no way to learn one; this is that way. Read-only over shared entities,
    which invariant 4 allows every module.
    """
    return list(
        session.scalars(
            sa.select(Team)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == member.id)
            .order_by(Team.name)
        )
    )


_ACCEPTS_A_RECORDING = frozenset({"scheduled", "failed", "recording"})
"""Meeting statuses a recording may be submitted for.

``recording`` because the live channel leaves a meeting there and the upload
at stop is what moves it on (audio-live-transcription.md, section 3.4). The
other four are refusals with different reasons: ``analyzing`` already has a
run in flight, and ``awaiting_confirmation``, ``complete`` and ``delivered``
have a transcript four modules have already been told about.
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

    **``expires_at`` is set here, from the team's retention window.** Nothing
    else in the repository writes it (#206), and module D reads
    ``expires_at IS NULL`` as "never expires" — so a meeting created without it
    is one the retention sweep and every retention-aware read ignore for good.
    Resolved now rather than at read time: a team that later shortens its
    retention does not retroactively un-record what was agreed.
    """
    require_team_member(session, user_id=owner.id, team_id=team_id)
    team = session.get(Team, team_id)
    if team is None:  # membership just passed, so the team exists; this is a torn read
        raise NotFoundError("team", team_id)

    meeting = Meeting(
        team_id=team_id,
        title=title,
        started_at=started_at,
        status="scheduled",
        expires_at=datetime.now(tz=UTC) + timedelta(days=team.retention_days),
    )
    session.add(meeting)
    session.flush()

    # The title is the team's own words and can carry a client name; it is not
    # logged. The id is enough to follow the meeting through the pipeline.
    log.info("audio_meeting_created", meeting_id=meeting.id, team_id=team_id, owner_id=owner.id)
    return meeting


def start_transcription(session: Session, *, meeting_id: str, uploader: User) -> TranscriptionJob:
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

    **Returns the attempt, not the meeting.** The job row is what the worker
    is queued -- an opaque id, never a path (privacy.md section 1) -- and what
    the recording is named after. Any earlier attempt for this meeting still
    ``queued`` or ``running`` is marked ``superseded`` so that, should it turn
    up late, the worker declines it rather than racing this one.

    **The row is locked for the read.** Two uploads finishing together -- a
    double click, a retry -- would both read ``scheduled`` and both queue, and
    the meeting would be transcribed twice and announced twice with different
    ``utt_`` ids, which breaks D's lineage (@kjfcvx12 on #259, #194). With the
    lock the second waits, reads ``analyzing``, and gets the 409 it should.
    """
    meeting = session.get(Meeting, meeting_id, with_for_update=True)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=uploader.id, team_id=meeting.team_id)

    if meeting.status not in _ACCEPTS_A_RECORDING:
        raise ConflictError(
            f"meeting {meeting_id} is {meeting.status}; a recording can only be "
            f"submitted for a meeting that is {' or '.join(sorted(_ACCEPTS_A_RECORDING))}"
        )

    if meeting.status == "recording" and live_registry.is_open(meeting_id):
        # The browser that owns the live session uploads after ``ended``,
        # when the claim is already gone. Anyone else uploading now would
        # flip the meeting to analyzing under a socket that is still
        # streaming, and the real recording would be refused when it comes.
        raise ConflictError(
            f"meeting {meeting_id} has a live session open; stop it before uploading"
        )

    now = datetime.now(tz=UTC)
    for stale in session.scalars(
        sa.select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting_id,
            TranscriptionJob.status.in_(("queued", "running")),
        )
    ):
        stale.status = "superseded"
        stale.finished_at = now
        log.info("audio_job_superseded", job_id=stale.id, meeting_id=meeting_id)

    job = TranscriptionJob(meeting_id=meeting_id, status="queued")
    session.add(job)
    meeting.status = "analyzing"
    session.flush()
    log.info(
        "audio_transcription_started",
        meeting_id=meeting_id,
        job_id=job.id,
        uploader_id=uploader.id,
    )
    return job


class Claim(NamedTuple):
    """What the worker learned at the door. See ``claim_job``."""

    meeting_id: str
    run: bool
    """This attempt is current and now ``running``; go ahead."""
    owns_file: bool
    """Nobody else will delete this attempt's upload; the caller must."""


def claim_job(session: Session, *, job_id: str) -> Claim:
    """The worker's first line: which meeting, whether to run, and who has the file.

    A ``queued`` job becomes ``running`` and is run. Anything else is declined:

    - ``running`` -- a redelivery while the first delivery is still going
      (``acks_late`` and Redis' one-hour visibility timeout make that a long
      meeting, not a fault). The first delivery owns the file and will delete
      it in its ``finally``; this one must not touch it.
    - ``done`` / ``failed`` -- a redelivery after the attempt finished. The
      file is already gone; ``owns_file`` is True so the caller's delete is a
      no-op that confirms it.
    - ``superseded`` -- a later upload for the same meeting was accepted
      first. This attempt's file is still there and nobody else will delete
      it, so the caller does. Running it would race the current attempt.

    ``mark_failed`` is not called for a declined job because nothing about the
    *meeting* failed. Raises ``NotFoundError`` for an id nobody queued: a
    broker carrying a message this database has no record of should be loud.

    **The row is locked for the read**, for the same reason the meeting row
    is at the claim: two deliveries of one message arriving together would
    both read ``queued`` and both run, and the second's ``finally`` would
    delete the file under the first's decode (@lsh2217 on #259). The second
    waits, reads ``running``, and is declined.
    """
    job = session.get(TranscriptionJob, job_id, with_for_update=True)
    if job is None:
        raise NotFoundError("job", job_id)
    if job.status != "queued":
        log.info("audio_job_declined", job_id=job_id, status=job.status)
        return Claim(job.meeting_id, run=False, owns_file=job.status != "running")
    job.status = "running"
    session.flush()
    return Claim(job.meeting_id, run=True, owns_file=True)


def mark_failed(session: Session, *, job_id: str) -> None:
    """Record that this attempt will not finish, and the meeting with it.

    Takes no user. Both callers are places where there is nobody to authorise
    against: the worker, whose task has just raised, and the endpoint, whose
    enqueue did not reach the broker. An authorisation check here would either
    be skipped or be given a fake user to satisfy it, and both are worse than
    not having one — the meeting was already claimed by a request that *was*
    checked.

    A meeting that stayed ``analyzing`` forever would be indistinguishable from
    one still being transcribed, and the screen would spin on it for good.

    **A superseded or finished job does not touch the meeting.** The meeting's
    status belongs to its current attempt. A first attempt that turns up late,
    dies, and marks ``failed`` the meeting a second attempt is busy with was
    the race #275 was opened over; keying failure on the job rather than the
    meeting is what closes it.

    **Only an ``analyzing`` meeting can fail here.** ``failed`` means "will
    not reach the four consumers", and once the meeting is ``complete`` this
    function cannot tell whether they were told: the sweep calls it for a
    job that sat ``running`` too long, and that job may have published and
    died one line later. Flipping ``complete`` here would invite a
    re-upload that announces the meeting twice (#194; @lsh2217 on #259). The
    one caller that *knows* the publish did not happen -- the task, from the
    ``except`` around ``publish`` itself -- calls ``mark_unannounced``.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)

    if job.status not in ("queued", "running"):
        log.info("audio_job_failed_skipped", job_id=job_id, status=job.status)
        return

    job.status = "failed"
    job.finished_at = datetime.now(tz=UTC)

    meeting = session.get(Meeting, job.meeting_id)
    if meeting is not None and meeting.status == "analyzing":
        meeting.status = "failed"
        log.info("audio_meeting_failed", meeting_id=meeting.id, job_id=job_id)
    else:
        log.info(
            "audio_meeting_failed_skipped",
            meeting_id=job.meeting_id,
            job_id=job_id,
            status=None if meeting is None else meeting.status,
        )
    session.flush()


def mark_unannounced(session: Session, *, job_id: str) -> None:
    """The transcript is committed and the meeting ``complete``, and the
    event did not go out. Fail both so a re-upload can.

    Called from exactly one place: the ``except`` around ``publish`` in the
    task, which is the only code that knows the publish itself raised --
    as opposed to the bookkeeping after it (``mark_published``), whose
    failure must *not* turn a delivered meeting red. Without this a broker
    that refuses the event leaves a ``complete`` meeting no consumer has
    heard of and that refuses another upload (@kjfcvx12 on #259).

    The rows stay: they are masked and correct. The re-run replaces them,
    which is #194's ``utt_`` churn -- worse than a republish, better than a
    meeting nobody can reach.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)
    meeting = session.get(Meeting, job.meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", job.meeting_id)
    job.status = "failed"
    job.finished_at = datetime.now(tz=UTC)
    meeting.status = "failed"
    session.flush()
    log.warning("audio_meeting_unannounced", meeting_id=meeting.id, job_id=job_id)


def mark_complete(session: Session, *, job_id: str) -> None:
    """The transcript is written and the meeting is done being transcribed.

    Called inside the same transaction as ``persist_transcript``, so the status
    and the rows it describes commit together. Split out rather than set inline
    there because ``persist_transcript`` is about utterances, and a function
    that also moves the meeting's lifecycle along is one whose name stops
    telling you what it does.

    Not ``delivered`` and not ``awaiting_confirmation``: those are B's and E's
    to decide, later in the meeting's life. A only says that its own step
    finished.

    **The job stays ``running``.** ``done`` means the four consumers were
    told, and they are told after this commits (``mark_published``). A job
    marked done here would make a failed publish unrecoverable:
    ``mark_failed`` rightly refuses to touch a finished job.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)
    meeting = session.get(Meeting, job.meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", job.meeting_id)

    meeting.status = "complete"
    session.flush()


def mark_published(session: Session, *, job_id: str) -> None:
    """The event went out; this attempt is over.

    The last write of a successful run, in its own small transaction after
    ``publish``. From here a redelivery is declined at ``claim_job`` as a
    finished job; between ``mark_complete`` and this line it is declined as
    ``running``, which leaves the file to this run -- the same outcome.
    """
    job = session.get(TranscriptionJob, job_id)
    if job is None:
        raise NotFoundError("job", job_id)
    job.status = "done"
    job.finished_at = datetime.now(tz=UTC)
    session.flush()


def sweep_orphans(
    session: Session, *, settings: AudioSettings, keep: str | None = None
) -> list[str]:
    """Delete uploads nobody is coming for. Returns the job ids swept.

    The handover leaves a file for a task; a task can be lost after the
    enqueue -- broker purged, worker never came back, a route that no longer
    matches -- and then the file sits in ``temp_dir`` with no owner, which is
    the durable copy invariant 11 exists to prevent (@PARKJAEKYUNG0525 on
    #259, item 3).

    **Decided against the database, not the clock.** #209 swept on mtime and
    could delete a file a late task was about to adopt. Here a file is an
    orphan when its job says so: ``done``, ``failed`` or ``superseded`` means
    the attempt is over and the file should already be gone; no job at all
    means nothing will ever look for it. A ``queued`` or ``running`` job is
    left alone until it is older than ``orphan_after_hours``, and then both
    the file and the job are failed -- a job that old has no worker.

    A file without the ``.upload`` suffix is one ``handover`` wrote and never
    got to ``assign``: the request died between the write and the claim. A
    file *with* a job's name that the database does not know is the next
    window along -- renamed, not yet committed. Neither has a row to
    consult, and both windows are one request long, so both are deleted on
    mtime only past the threshold. Those are the two places the clock
    decides, and it decides about files no task can be about to adopt.

    One query for all the job files, not one per file: this runs at the
    start of every task and its cost grows with the backlog. Concurrent
    sweeps are not serialised; they can both delete the same already-gone
    file (``missing_ok``) and both log it, which is redundant, not wrong.

    Runs at the start of every ``process_recording``, skipping ``keep`` -- the
    caller's own job -- until there is a periodic trigger for it (#207, #258).
    Ids only in the log; the filenames are ids.
    """
    directory = Path(settings.temp_dir)
    if not directory.is_dir():
        return []

    cutoff = datetime.now(tz=UTC) - timedelta(hours=settings.orphan_after_hours)

    def stale(path: Path) -> bool:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC) < cutoff

    files = [path for path in directory.iterdir() if path.is_file()]
    by_job = {jid: path for path in files if (jid := storage.job_id_of(path)) is not None}
    if keep is not None:
        by_job.pop(keep, None)
    jobs = {
        job.id: job
        for job in session.scalars(
            sa.select(TranscriptionJob).where(TranscriptionJob.id.in_(list(by_job)))
        )
    }

    swept: list[str] = []
    for path in files:
        if storage.job_id_of(path) is None:
            if stale(path):
                storage.delete_orphan(path)
                log.info("audio_orphan_unassigned_deleted")
            continue

    for job_id, path in by_job.items():
        job = jobs.get(job_id)
        if job is None:
            # A file with a job's name and no row yet is a request between
            # ``assign`` and its commit -- milliseconds, but a concurrent
            # sweep lands in them (@lsh2217 on #259). Same grace as an
            # unassigned file: the clock decides, about a file no task can be
            # about to adopt yet.
            if not stale(path):
                continue
        elif job.status in ("queued", "running"):
            if job.created_at >= cutoff:
                continue
            mark_failed(session, job_id=job_id)
            log.warning("audio_job_abandoned", job_id=job_id)

        storage.delete_orphan(path)
        swept.append(job_id)
        log.info("audio_orphan_deleted", job_id=job_id, job_known=job is not None)

    return swept


def attest_consent(session: Session, *, meeting_id: str, attested_by: User) -> None:
    """Record that everyone in this meeting's recording consented, on the
    word of a member of its team.

    **The only path in the repository to ``participants.consented = True``**
    (#190). Consent is a statement about people, and before identification (#6)
    a participant row is a voice, not a person -- so there is nowhere to write
    a per-person answer, and the one honest statement available is a team
    member's about the whole meeting. Any member of the team, not necessarily
    the one who uploaded and not necessarily one who was there: ``meetings``
    has no ``created_by`` to narrow it, so the check is membership and the
    limit is documented. This records that statement and applies
    it: every participant row the meeting has *now* is set True, and
    ``persistence._participants_for`` reads the attestation for every row it
    creates *later*, so a rerun that invents a label the first run never saw
    gets the same value. Module B's condition on #190, and it is pinned by
    ``test_a_rerun_gives_a_new_label_the_same_consent``.

    Idempotent: a second call finds the row and changes nothing but the
    participant flags, which were already True. A reload is not a second
    statement.

    Not per person, not revocable, and it does not tell B and C that a meeting
    they already analysed has changed. Those are S10, S11 and #190's follow-ups.
    Nor does anything here undo what B, C and E derived once the meeting was
    analysed -- today the only way that data goes is with the meeting itself
    (CASCADE), and before identification (#6) there is no per-person unit to
    revoke for. The default is not loosened by any of this: a meeting with no
    attestation is exactly as it was.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=attested_by.id, team_id=meeting.team_id)

    if session.get(AudConsentAttestation, meeting_id) is None:
        session.add(AudConsentAttestation(meeting_id=meeting_id, attested_by=attested_by.id))

    result = session.execute(
        sa.update(Participant)
        .where(Participant.meeting_id == meeting_id, Participant.consented.is_(False))
        .values(consented=True)
    )
    updated = int(getattr(result, "rowcount", 0))
    session.flush()
    # Counts and ids only. Who attested is in the row; the log says it happened.
    log.info(
        "consent_attested_by_member",
        meeting_id=meeting_id,
        attested_by=attested_by.id,
        participants_updated=updated,
    )


def authenticate_live(session: Session, *, token: str, meeting_id: str) -> User:
    """Who is on the other end of a live socket, and may they be.

    A WebSocket handler cannot take ``CurrentUser`` as a dependency, so the
    same two checks the HTTP routes make -- decode the token, confirm the
    membership -- are one function here, and the socket and the routes cannot
    come to different conclusions about the same token.

    One deliberate divergence from ``current_user``: a well-signed token whose
    user row is gone raises ``PermissionDeniedError`` here, not
    ``NotFoundError``. The socket maps ``NotFoundError`` to a close code
    meaning "no such meeting", and a deleted user must not be reported as that.
    """
    user_id = decode_token(token).get("sub")
    if not user_id:
        raise PermissionDeniedError("token carries no subject")
    user = session.get(User, user_id)
    if user is None:
        raise PermissionDeniedError("token names nobody")
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=user.id, team_id=meeting.team_id)
    return user


_ACCEPTS_A_LIVE_SESSION = frozenset({"scheduled", "recording"})


def begin_live(session: Session, *, meeting_id: str) -> None:
    """Mark the meeting as being recorded.

    ``recording`` is accepted as well as ``scheduled`` because a socket that
    drops leaves the meeting there, and the browser -- which still holds the
    recording -- must be able to reconnect. It stays ``recording`` after
    ``stop`` for the same reason: the upload that follows is what moves it on.
    """
    # Locked for the read, same as ``start_transcription``: a hello and an
    # upload racing the same meeting must not both read a status that lets
    # them both through.
    meeting = session.get(Meeting, meeting_id, with_for_update=True)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    if meeting.status not in _ACCEPTS_A_LIVE_SESSION:
        raise ConflictError(
            f"meeting {meeting_id} is {meeting.status}; a live session needs a meeting "
            f"that is {' or '.join(sorted(_ACCEPTS_A_LIVE_SESSION))}"
        )
    meeting.status = "recording"
    session.flush()
    log.info("live_meeting_recording", meeting_id=meeting_id)


def speakers_for(session: Session, *, meeting_id: str, reader: User) -> list[SpeakerEntry]:
    """Every speaker label of a meeting, with who it is or might be.

    The candidate is computed here rather than stored: a stored one is stale
    the moment somebody else confirms a profile, and recomputing is one query.

    **Only members of this meeting's team can be candidates.** A candidate
    from another team would say that person attended this team's meeting.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=reader.id, team_id=meeting.team_id)

    # `Participant.id` is a random id (`new_id`), not a sortable one -- this
    # only makes the read deterministic between two requests (e.g. after a
    # row update), not correctly ordered. The real order is sorted in below,
    # from the number in the label.
    participants = list(
        session.scalars(
            sa.select(Participant)
            .where(Participant.meeting_id == meeting_id)
            .order_by(Participant.id)
        )
    )
    observations = {
        row.speaker_label: row
        for row in session.scalars(
            sa.select(AudSpeakerEmbedding).where(AudSpeakerEmbedding.meeting_id == meeting_id)
        )
        if row.speaker_label is not None
    }
    profiles = _profiles_of_team(session, team_id=meeting.team_id)
    threshold = get_settings().identification_threshold

    entries = []
    for participant in participants:
        observation = observations.get(participant.speaker_label)
        candidate = None
        if participant.user_id is None and observation is not None:
            found = identification.best_candidate(
                observation.vector,
                profiles,
                model_version=observation.model_version,
                threshold=threshold,
            )
            if found is not None:
                candidate = SpeakerCandidate(
                    user_id=found.user_id, name=found.display_name, similarity=found.similarity
                )
        entries.append(
            SpeakerEntry(
                speaker_label=participant.speaker_label,
                user_id=participant.user_id,
                candidate=candidate,
            )
        )
    entries.sort(key=_speaker_order)
    return entries


_LABEL_NUMBER = re.compile(rf"^{re.escape(UNIDENTIFIED)} (\d+)$")
"""``speakers.rename_speakers`` and the live path both number a label this
way, by first appearance in time -- so the number *is* the ordering the
screen wants, and reading it back is cheaper than tracking appearance order
anywhere else."""


def _speaker_order(entry: SpeakerEntry) -> tuple[int, int]:
    """Sort key for ``speakers_for``: the integer in "화자 N", ascending.

    A label that does not match the shape (a future form, a bug upstream)
    sorts after every numbered one rather than raising -- ``(1, 0)`` for all
    of them, so Python's stable sort leaves them in the order the query
    already gave (``Participant.id``, an arbitrary but deterministic
    tiebreak) instead of reordering or crashing the read.
    """
    match = _LABEL_NUMBER.match(entry.speaker_label)
    if match is None:
        return (1, 0)
    return (0, int(match.group(1)))


def _profiles_of_team(session: Session, *, team_id: str) -> list[identification.Profile]:
    """Confirmed voices of this team's members, grouped by person.

    The team join is the privacy boundary, and it is in the query rather than
    a filter afterwards so that no path can skip it.
    """
    rows = session.execute(
        sa.select(AudSpeakerEmbedding, User.display_name)
        .join(User, User.id == AudSpeakerEmbedding.user_id)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id, AudSpeakerEmbedding.user_id.is_not(None))
    ).all()
    by_user: dict[tuple[str, str, str], list[tuple[float, ...]]] = {}
    for row, display_name in rows:
        key = (row.user_id, display_name, row.model_version)
        by_user.setdefault(key, []).append(tuple(row.vector))
    return [
        identification.Profile(
            user_id=user_id, display_name=name, vectors=tuple(vectors), model_version=version
        )
        for (user_id, name, version), vectors in by_user.items()
    ]


def members_of(session: Session, *, team_id: str, reader: User) -> list[TeamMemberSummary]:
    """The team's people, for the picker. Members only -- asking about a team
    you are not in is a 403, not an empty list."""
    require_team_member(session, user_id=reader.id, team_id=team_id)
    rows = session.execute(
        sa.select(User.id, User.display_name)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(User.display_name)
    ).all()
    return [TeamMemberSummary(user_id=user_id, name=name) for user_id, name in rows]


def assign_speaker(
    session: Session,
    *,
    meeting_id: str,
    speaker_label: str,
    user_id: str,
    confirmed_by: User,
) -> None:
    """ "``화자 2`` is this person."

    Two effects, in this order: the participant row carries the user (which is
    what ``transcript_payload`` publishes as ``speaker_id``), and the meeting's
    observation vector for that label becomes one of the person's profile
    vectors.

    The profile row records where it came from, and a second confirmation of
    the same (meeting, label) **replaces** it. Without that, correcting a
    mistake would leave the wrong voice in somebody's profile for good.

    **The assignment is the product; the profile copy is a bonus.** The
    participant write happens first and is never undone by a later failure in
    the profile copy (see the guard around the insert below) -- a person keeps
    the credit for confirming even if the vector never makes it into anyone's
    profile.

    No observation row is the normal case, not an edge: an unconsented meeting
    has none, and confirming still assigns the speaker, with no profile and no
    error. But the **replace** still has to happen: a previous confirmation of
    this exact (meeting, label) can have left a profile row even though there
    is no observation to replace it with now -- the meeting was reprocessed
    and the observation dropped, or it was never re-derived at all -- and that
    stale row is the *wrong* person's voice if this call is naming someone
    else. So the delete below runs every time, not only when ``observation``
    is not ``None``.
    """
    meeting = session.get(Meeting, meeting_id, with_for_update=True)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)
    require_team_member(session, user_id=confirmed_by.id, team_id=meeting.team_id)
    require_team_member(
        session,
        user_id=user_id,
        team_id=meeting.team_id,
        message="the named user is not a member of this team",
    )

    participant = session.scalar(
        sa.select(Participant).where(
            Participant.meeting_id == meeting_id,
            Participant.speaker_label == speaker_label,
        )
    )
    if participant is None:
        raise NotFoundError("speaker", f"{meeting_id}/{speaker_label}")
    participant.user_id = user_id
    session.flush()

    observation = session.scalar(
        sa.select(AudSpeakerEmbedding).where(
            AudSpeakerEmbedding.meeting_id == meeting_id,
            AudSpeakerEmbedding.speaker_label == speaker_label,
        )
    )

    # Unconditional and independent of the INSERT below, and outside any
    # SAVEPOINT of its own: a human has just said this speaker is not who a
    # previous confirmation said, and the old profile must not survive that
    # correction regardless of whether a new one can be written. Its bound
    # parameters are only a meeting id and a label, never a vector, so #356
    # does not apply and it needs no guard.
    session.execute(
        sa.delete(AudSpeakerEmbedding).where(
            AudSpeakerEmbedding.source_meeting_id == meeting_id,
            AudSpeakerEmbedding.source_speaker_label == speaker_label,
        )
    )

    learned = False
    if observation is not None:
        # A vector is bound biometric data; ``packages/core``'s engine does
        # not set ``hide_parameters`` and a ``StatementError`` here would
        # carry it (#356, fixed outside this branch), so only the INSERT --
        # the one statement that carries a vector -- runs inside a SAVEPOINT,
        # and a ``SQLAlchemyError`` is caught and logged by exception type
        # only, never its message or parameters. On that failure the source
        # is simply left with no profile, same as the no-observation case
        # above -- not with the stale one the DELETE already removed. That
        # is the safe direction to fail in: nothing wrong is retained, and
        # ``speaker_profile_copy_failed`` is the trace.
        try:
            with session.begin_nested():
                session.add(
                    AudSpeakerEmbedding(
                        user_id=user_id,
                        vector=list(observation.vector),
                        model_version=observation.model_version,
                        source_meeting_id=meeting_id,
                        source_speaker_label=speaker_label,
                        confirmed_by=confirmed_by.id,
                        confirmed_at=datetime.now(tz=UTC),
                    )
                )
            learned = True
        except SQLAlchemyError as exc:
            log.warning("speaker_profile_copy_failed", error=type(exc).__name__)

    session.flush()
    # A meeting id and a boolean -- no name, no vector.
    log.info("speaker_assigned", meeting_id=meeting_id, learned=learned)


def _delete_observations_owned_by(session: Session, *, user_id: str) -> int:
    """Delete every *observation* row this person's voice is still sitting
    in, not just their profile rows.

    An observation (``meeting_id`` + ``speaker_label``, the check constraint
    guarantees ``user_id IS NULL``) is a 256-d vector of somebody's voice in
    one meeting. Once ``assign_speaker`` has named a participant, that
    meeting's observation for their label is fully attributable through
    ``participants.user_id`` even though the vector row itself carries no
    ``user_id`` -- a plain ``WHERE user_id == ...`` delete, which is the
    shape a profile row has, never touches it. Left behind, it is a
    biometric vector of a person who asked for their voice to be forgotten,
    and the next team member to (re-)confirm that same label would copy it
    straight back into a fresh profile for them.

    A correlated ``EXISTS`` over ``participants``, not a join: this deletes
    from ``aud_speaker_embeddings`` alone, and the participant row itself is
    untouched here -- callers decide separately whether ``user_id`` on it
    should also be cleared (``forget_user_voice`` does; ``delete_voice_profile``
    deliberately does not, since the person is still using the product and
    still is who spoke).
    """
    owned_by_user = (
        sa.select(Participant.id)
        .where(
            Participant.meeting_id == AudSpeakerEmbedding.meeting_id,
            Participant.speaker_label == AudSpeakerEmbedding.speaker_label,
            Participant.user_id == user_id,
        )
        .exists()
    )
    result = session.execute(sa.delete(AudSpeakerEmbedding).where(owned_by_user))
    return int(getattr(result, "rowcount", 0))


def delete_voice_profile(session: Session, *, user: User) -> int:
    """Forget this person's voice. privacy.md section 4: a user can delete
    their own data at any time. Identification simply stops offering them.

    Deletes both shapes of row their voice can be in: their own profile rows
    (``user_id`` set), and any meeting observation still attributable to them
    through a participant row they were confirmed against (see
    ``_delete_observations_owned_by``) -- otherwise "deleted" would not be
    true of the vector that matters most, the one still sitting in a meeting
    somebody could re-confirm.
    """
    observations_removed = _delete_observations_owned_by(session, user_id=user.id)
    result = session.execute(
        sa.delete(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == user.id)
    )
    profiles_removed = int(getattr(result, "rowcount", 0))
    removed = profiles_removed + observations_removed
    session.flush()
    log.info("voice_profile_deleted", rows=removed)
    return removed


@on_user_deleted("audio")
def forget_user_voice(user_id: str) -> None:
    """Leaving the product takes the voice with it -- and every trace of the
    person as someone who *confirmed* or *attested*, and as someone who
    *spoke*, even on rows that belong to somebody else or to a meeting.

    The FK cascades (``SET NULL`` on ``confirmed_by``, ``attested_by`` and
    ``participants.user_id``, ``CASCADE`` on a profile's own ``user_id``)
    when the ``users`` row is really deleted; this hook is the path for a
    deletion that does not remove the row itself, so it does that scrubbing
    by hand instead of only the person's own profile rows:

    - meeting observations still attributable to them through a participant
      row (``_delete_observations_owned_by`` -- must run *before* that
      participant row's ``user_id`` is cleared below, since it is what finds
      them);
    - ``participants.user_id`` itself, or ``transcript_payload`` keeps
      publishing a person who left the product as ``speaker_id`` to every
      later reader and every future event;
    - their own profile rows;
    - ``confirmed_by`` on somebody else's profile, and ``attested_by`` on a
      meeting's consent attestation -- ids the product no longer has anyone
      behind.
    """
    with session_scope() as session:
        _delete_observations_owned_by(session, user_id=user_id)
        session.execute(
            sa.update(Participant).where(Participant.user_id == user_id).values(user_id=None)
        )
        session.execute(
            sa.delete(AudSpeakerEmbedding).where(AudSpeakerEmbedding.user_id == user_id)
        )
        session.execute(
            sa.update(AudSpeakerEmbedding)
            .where(AudSpeakerEmbedding.confirmed_by == user_id)
            .values(confirmed_by=None)
        )
        session.execute(
            sa.update(AudConsentAttestation)
            .where(AudConsentAttestation.attested_by == user_id)
            .values(attested_by=None)
        )
